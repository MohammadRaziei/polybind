# Native dependencies of a hybrid C++/Python project: where they come from and which tag was tested (spec.md, sections 5 and 9).
#
#   include("<output of `python -m polybind cmake-dir`>/PolybindDeps.cmake")
#   polybind_deps_load()                      # reads polybind.toml / [tool.polybind] and polybind.lock through Python
#   polybind_resolve_dep(liburlparser)        # afterwards the targets named in the settings exist
#   polybind_deps_record_target()             # adds the `polybind_deps_record` target
#
# The settings are TOML, which CMake cannot read: polybind_deps_load() runs `python -m polybind deps export` and keeps
# the JSON. A dependency with `optional = true` may be missing: nothing fails, POLYBIND_DEP_<N>_FOUND is FALSE and the caller
# leaves the feature out. -DPOLYBIND_DEPS_SKIP_<N>=ON does not even try. After a successful resolve:
#   POLYBIND_DEP_<N>_FOUND      TRUE
#   POLYBIND_DEP_<N>_PROVIDER   fetch, uv, pip or system   (what really delivered it)
#   POLYBIND_DEP_<N>_VERSION    the version that was found
# <N> is the dependency name in upper case, every character that is not a letter or a digit turned into _.
#
# polybind.toml is edited by hand. polybind.lock is written only by `polybind deps record` (the polybind_deps_record target):
# the tag that passed the tests, the provider that delivered it and, for a git source, the commit.
#
# Providers [python, uv, pip, fetch, system]:
#   uv, pip  a Python package that answers `python -m <module> <cmake-flag>` (or <module>.<cmake-function>() when the
#            module has no command line), installed into the build tree by that tool, or already importable.
#   python   uv when it is found, else pip.
#   fetch    `git clone --depth 1` of one tag with FetchContent: no Python, no submodule, no history.
#   system   find_package() on the normal CMake search path. Never part of `auto`.
# A provider whose table is missing in the settings (python.*, fetch.url) is off.

include_guard(GLOBAL)

set(POLYBIND_DEPS_PROVIDER "auto" CACHE STRING "Where the native dependencies come from: auto, python, uv, pip, fetch or system")
set_property(CACHE POLYBIND_DEPS_PROVIDER PROPERTY STRINGS auto python uv pip fetch system)
set(POLYBIND_DEPS_PYTHON "" CACHE FILEPATH "Python interpreter of the python providers (default: the one CMake finds)")
set(POLYBIND_DEPS_PREFER_PROVIDER "" CACHE STRING "With POLYBIND_DEPS_PROVIDER=auto: try this provider first (python, uv, pip, fetch or system), the others after it")
set_property(CACHE POLYBIND_DEPS_PREFER_PROVIDER PROPERTY STRINGS "" python uv pip fetch system)
option(POLYBIND_DEPS_PIN "Use exactly the tag recorded in polybind.lock and never update it" ON)
option(POLYBIND_DEPS_STRICT "Fail instead of warning when the version found is not the recorded one" OFF)
option(POLYBIND_DEPS_OFFLINE "Install and fetch nothing: only use what is already there" OFF)
# POLYBIND_DEPS_PROVIDER_<N> (for example POLYBIND_DEPS_PROVIDER_LIBURLPARSER=fetch) forces the provider of one dependency: only that one
# is tried and failing is an error. To only change the order of `auto` (the others stay as a fallback): `prefer-provider` of the
# dependency, POLYBIND_DEPS_PREFER_PROVIDER for all, POLYBIND_DEPS_PREFER_PROVIDER_<N> for one; the more specific one wins, in that
# order: <N> variable, global variable, settings. Below all of them comes the provider recorded in polybind.lock.

# --- small helpers -------------------------------------------------------------------------------------------------

# _polybind_json_get(<out> <json> <member>...): the value at that path, or the empty string when it is absent or null.
function(_polybind_json_get out json)
    string(JSON _v ERROR_VARIABLE _err GET "${json}" ${ARGN})
    if(_err)
        set(${out} "" PARENT_SCOPE)
    else()
        set(${out} "${_v}" PARENT_SCOPE)
    endif()
endfunction()

# _polybind_json_get_default(<out> <default> <json> <member>...): like _polybind_json_get, but <default> when the member is not there.
function(_polybind_json_get_default out default json)
    string(JSON _v ERROR_VARIABLE _err GET "${json}" ${ARGN})
    if(_err)
        set(${out} "${default}" PARENT_SCOPE)
    else()
        set(${out} "${_v}" PARENT_SCOPE)
    endif()
endfunction()

# _polybind_json_list(<out> <json> <member>...): the strings of a JSON array as a CMake list (empty when absent).
function(_polybind_json_list out json)
    set(_items "")
    string(JSON _n ERROR_VARIABLE _err LENGTH "${json}" ${ARGN})
    if(NOT _err AND _n GREATER 0)
        math(EXPR _last "${_n} - 1")
        foreach(_i RANGE ${_last})
            string(JSON _v GET "${json}" ${ARGN} ${_i})
            list(APPEND _items "${_v}")
        endforeach()
    endif()
    set(${out} "${_items}" PARENT_SCOPE)
endfunction()

function(_polybind_deps_uname out name)
    string(TOUPPER "${name}" _u)
    string(MAKE_C_IDENTIFIER "${_u}" _u)
    set(${out} "${_u}" PARENT_SCOPE)
endfunction()

# polybind_version_satisfies(<version> <spec> <out>): spec is comma separated clauses of ~=, >=, >, <=, <, == or != and a
# plain number, like ">=2.2.0,<3". "~=2.2.1" means ">=2.2.1,<2.3". An empty spec accepts everything.
function(polybind_version_satisfies version spec out)
    set(_ok TRUE)
    string(REPLACE " " "" _spec "${spec}")
    string(REPLACE "," ";" _clauses "${_spec}")
    foreach(_clause IN LISTS _clauses)
        if(_clause STREQUAL "")
            continue()
        endif()
        if(NOT _clause MATCHES "^(~=|>=|<=|==|!=|>|<)([0-9]+(\\.[0-9]+)*)$")
            message(FATAL_ERROR "deps: unsupported version clause '${_clause}' in '${spec}' (use ~=, >=, >, <=, <, == or != and a plain number)")
        endif()
        set(_op "${CMAKE_MATCH_1}")
        set(_want "${CMAKE_MATCH_2}")
        if(NOT version MATCHES "^[0-9][0-9.]*$")
            set(_ok FALSE)  # "0.0.0+unknown" and the like never satisfy a range
        elseif(_op STREQUAL "~=")
            string(REPLACE "." ";" _parts "${_want}")
            list(LENGTH _parts _n)
            if(_n LESS 2)
                message(FATAL_ERROR "deps: '~=${_want}' needs at least two numbers (~=2.2 or ~=2.2.1)")
            endif()
            math(EXPR _keep "${_n} - 1")
            list(SUBLIST _parts 0 ${_keep} _prefix)
            math(EXPR _at "${_keep} - 1")
            list(GET _prefix ${_at} _top)
            math(EXPR _top "${_top} + 1")
            list(REMOVE_AT _prefix ${_at})
            list(APPEND _prefix ${_top})
            list(JOIN _prefix "." _upper)
            if(version VERSION_LESS _want OR NOT version VERSION_LESS _upper)
                set(_ok FALSE)
            endif()
        elseif(_op STREQUAL ">=" AND version VERSION_LESS _want)
            set(_ok FALSE)
        elseif(_op STREQUAL ">" AND NOT version VERSION_GREATER _want)
            set(_ok FALSE)
        elseif(_op STREQUAL "<=" AND version VERSION_GREATER _want)
            set(_ok FALSE)
        elseif(_op STREQUAL "<" AND NOT version VERSION_LESS _want)
            set(_ok FALSE)
        elseif(_op STREQUAL "==" AND NOT version VERSION_EQUAL _want)
            set(_ok FALSE)
        elseif(_op STREQUAL "!=" AND version VERSION_EQUAL _want)
            set(_ok FALSE)
        endif()
    endforeach()
    set(${out} ${_ok} PARENT_SCOPE)
endfunction()

# --- loading -------------------------------------------------------------------------------------------------------

# _polybind_deps_interpreter(<out>): POLYBIND_DEPS_PYTHON, else the interpreter CMake found, else look for one.
function(_polybind_deps_interpreter out)
    set(_py "${POLYBIND_DEPS_PYTHON}")
    if(_py STREQUAL "")
        if(Python3_EXECUTABLE)
            set(_py "${Python3_EXECUTABLE}")
        elseif(Python_EXECUTABLE)
            set(_py "${Python_EXECUTABLE}")
        else()
            find_package(Python3 COMPONENTS Interpreter QUIET)
            set(_py "${Python3_EXECUTABLE}")
        endif()
    endif()
    set(${out} "${_py}" PARENT_SCOPE)
endfunction()

# polybind_deps_load([ROOT <dir>]): the settings and the lock of the project in <dir> (default: CMAKE_SOURCE_DIR).
function(polybind_deps_load)
    cmake_parse_arguments(PARSE_ARGV 0 _a "" "ROOT" "")
    if(NOT _a_ROOT)
        set(_a_ROOT "${CMAKE_SOURCE_DIR}")
    endif()
    _polybind_deps_interpreter(_py)
    if(_py STREQUAL "")
        message(FATAL_ERROR "deps: no Python interpreter found (set POLYBIND_DEPS_PYTHON): the settings are TOML and are read by `python -m polybind`")
    endif()
    execute_process(
        COMMAND "${_py}" -m polybind deps export --root "${_a_ROOT}"
        OUTPUT_VARIABLE _json ERROR_VARIABLE _err RESULT_VARIABLE _rc)
    if(NOT _rc EQUAL 0)
        message(FATAL_ERROR "deps: `${_py} -m polybind deps export` failed (is polybind installed for that interpreter?):\n${_err}")
    endif()
    if(NOT _err STREQUAL "")
        message(WARNING "deps: ${_err}")
    endif()
    set_property(GLOBAL PROPERTY POLYBIND_DEPS_JSON "${_json}")
    set_property(GLOBAL PROPERTY POLYBIND_DEPS_ROOT "${_a_ROOT}")
    set_property(GLOBAL PROPERTY POLYBIND_DEPS_PYTHON_EXE "${_py}")
    # `polybind deps record` refuses when this configure used the recorded tags.
    file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/polybind-deps")
    file(WRITE "${CMAKE_BINARY_DIR}/polybind-deps/pin.txt" "${POLYBIND_DEPS_PIN}\n")
    if(NOT CMAKE_SCRIPT_MODE_FILE)
        foreach(_f polybind.toml pyproject.toml polybind.lock)
            if(EXISTS "${_a_ROOT}/${_f}")
                set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${_a_ROOT}/${_f}")
            endif()
        endforeach()
    endif()
endfunction()

# polybind_deps_names(<out>): the dependency names, in file order.
function(polybind_deps_names out)
    get_property(_json GLOBAL PROPERTY POLYBIND_DEPS_JSON)
    string(JSON _members ERROR_VARIABLE _err LENGTH "${_json}" dependencies)
    set(_names "")
    if(NOT _err AND _members GREATER 0)
        math(EXPR _last "${_members} - 1")
        foreach(_i RANGE ${_last})
            string(JSON _n MEMBER "${_json}" dependencies ${_i})
            list(APPEND _names "${_n}")
        endforeach()
    endif()
    set(${out} "${_names}" PARENT_SCOPE)
endfunction()

# --- python providers (python, uv, pip) ----------------------------------------------------------------------------

function(_polybind_deps_python_env_path target_dir out)
    if(WIN32)
        set(_sep ";")
    else()
        set(_sep ":")
    endif()
    set(_path "${target_dir}")
    if(DEFINED ENV{PYTHONPATH} AND NOT "$ENV{PYTHONPATH}" STREQUAL "")
        set(_path "${target_dir}${_sep}$ENV{PYTHONPATH}")
    endif()
    set(${out} "${_path}" PARENT_SCOPE)
endfunction()

# Runs `<command>` with <target_dir> in front of PYTHONPATH; <out> is the trimmed stdout, empty on failure.
function(_polybind_deps_run out target_dir)
    _polybind_deps_python_env_path("${target_dir}" _pp)
    execute_process(
        COMMAND ${CMAKE_COMMAND} -E env "PYTHONPATH=${_pp}" ${ARGN}
        OUTPUT_VARIABLE _out ERROR_VARIABLE _errout RESULT_VARIABLE _rc
        OUTPUT_STRIP_TRAILING_WHITESPACE)
    if(NOT _rc EQUAL 0)
        set(_out "")
    endif()
    set(${out} "${_out}" PARENT_SCOPE)
endfunction()

# Runs `<python> <args>` with <target_dir> in front of PYTHONPATH; <out> is the trimmed stdout, empty on failure.
function(_polybind_deps_python_run out py target_dir)
    _polybind_deps_run(_out "${target_dir}" "${py}" ${ARGN})
    set(${out} "${_out}" PARENT_SCOPE)
endfunction()

# Runs a `version-command`: a first word of `python` is the interpreter <py>; any other first word is a console script, which
# is looked for in the scripts directory of <target_dir> first (bin, or Scripts on Windows) and then on PATH.
function(_polybind_deps_command_run out py target_dir)
    set(_words ${ARGN})
    list(POP_FRONT _words _first)
    if(_first STREQUAL "python")
        _polybind_deps_python_run(_out "${py}" "${target_dir}" ${_words})
    else()
        set(_exe "${_first}")
        foreach(_dir "${target_dir}/bin" "${target_dir}/Scripts")
            foreach(_name "${_first}" "${_first}.exe")
                if(_exe STREQUAL _first AND EXISTS "${_dir}/${_name}" AND NOT IS_DIRECTORY "${_dir}/${_name}")
                    set(_exe "${_dir}/${_name}")
                endif()
            endforeach()
        endforeach()
        _polybind_deps_run(_out "${target_dir}" "${_exe}" ${_words})
    endif()
    set(${out} "${_out}" PARENT_SCOPE)
endfunction()

# Asks the package for its CMake directory and version. Sets _py_cmake_dir and _py_version in the caller, empty when the
# package is not importable. With module-cli the CMake dir comes from `python -m <module> <cmake_flag>`, without it from
# <module>.<cmake_function>(). The version comes from <version_command> when it is not empty (and prints something) and from
# the package metadata otherwise. The snippets have no semicolon: it would split the argument (CMake list).
function(_polybind_deps_python_query py target_dir module package module_cli cmake_flag version_command cmake_function)
    if(module_cli)
        _polybind_deps_python_run(_cmake_dir "${py}" "${target_dir}" -m ${module} ${cmake_flag})
    else()
        _polybind_deps_python_run(_cmake_dir "${py}" "${target_dir}" -c
            "import sys, importlib\nprint(getattr(importlib.import_module(sys.argv[1]), sys.argv[2])())" ${module} ${cmake_function})
    endif()
    set(_version "")
    if(_cmake_dir AND NOT version_command STREQUAL "")
        _polybind_deps_command_run(_version "${py}" "${target_dir}" ${version_command})
    endif()
    if(_cmake_dir AND _version STREQUAL "")
        _polybind_deps_python_run(_version "${py}" "${target_dir}" -c
            "import sys, importlib.metadata as m\nprint(m.version(sys.argv[1]))" ${package})
    endif()
    set(_py_cmake_dir "${_cmake_dir}" PARENT_SCOPE)
    set(_py_version "${_version}" PARENT_SCOPE)
endfunction()

# Installs <requirement> into <target_dir> with <installer> (uv or pip). Returns without a result: the caller asks again.
function(_polybind_deps_python_install installer py target_dir requirement)
    if(installer STREQUAL "uv")
        find_program(_polybind_uv uv)
        if(NOT _polybind_uv)
            message(STATUS "deps: uv was not found")
            return()
        endif()
        set(_cmd "${_polybind_uv}" pip install --python "${py}" --quiet --no-deps --target "${target_dir}" "${requirement}")
    else()
        set(_cmd "${py}" -m pip install --disable-pip-version-check --quiet --upgrade --no-deps --target "${target_dir}" "${requirement}")
    endif()
    message(STATUS "deps: ${installer} install ${requirement}")
    execute_process(COMMAND ${_cmd} RESULT_VARIABLE _rc OUTPUT_VARIABLE _o ERROR_VARIABLE _e)
    if(NOT _rc EQUAL 0)
        message(STATUS "deps: ${installer} install ${requirement} failed:\n${_o}${_e}")
    endif()
endfunction()

# The caller sets _POLYBIND_INSTALLER (auto, uv or pip). Sets _polybind_dep_ok, _polybind_dep_version, _polybind_dep_cmake_dir,
# _polybind_dep_installer in the caller.
function(_polybind_deps_provider_python name json)
    set(_polybind_dep_ok FALSE PARENT_SCOPE)
    string(JSON _type ERROR_VARIABLE _err TYPE "${json}" dependencies ${name} python)
    if(_err OR NOT _type STREQUAL "OBJECT")
        message(STATUS "deps: ${name}: no python.* settings, so the python provider is off")
        return()
    endif()
    _polybind_json_get(_package "${json}" dependencies ${name} python package)
    _polybind_json_get(_module "${json}" dependencies ${name} python module)
    _polybind_json_get(_module_cli "${json}" dependencies ${name} python module_cli)
    _polybind_json_list(_cmake_flag "${json}" dependencies ${name} python cmake_flag)
    _polybind_json_list(_version_command "${json}" dependencies ${name} python version_command)
    _polybind_json_get(_cmake_function "${json}" dependencies ${name} python cmake_function)
    _polybind_json_get(_spec "${json}" dependencies ${name} version_spec)
    _polybind_json_get(_locked "${json}" dependencies ${name} lock version)

    set(_installer "${_POLYBIND_INSTALLER}")
    if(_installer STREQUAL "auto")
        find_program(_polybind_uv uv)
        if(_polybind_uv)
            set(_installer uv)
        else()
            set(_installer pip)
        endif()
    endif()

    _polybind_deps_interpreter(_py)
    if(_py STREQUAL "")
        message(STATUS "deps: ${name}: no Python interpreter found (set POLYBIND_DEPS_PYTHON)")
        return()
    endif()

    string(TOLOWER "${name}" _lname)
    set(_target_dir "${CMAKE_BINARY_DIR}/polybind-deps/python/${_lname}")

    _polybind_deps_python_query("${_py}" "${_target_dir}" "${_module}" "${_package}" "${_module_cli}" "${_cmake_flag}" "${_version_command}" "${_cmake_function}")
    set(_found_cmake_dir "${_py_cmake_dir}")
    set(_found_version "${_py_version}")

    set(_good FALSE)
    if(_found_cmake_dir)
        polybind_version_satisfies("${_found_version}" "${_spec}" _good)
    endif()
    # Install when nothing usable is there, or when pinned and what is there is not the recorded version. When installing
    # is not allowed, whatever is in range is used (and warned about by polybind_resolve_dep if it is not the recorded one).
    set(_want_install FALSE)
    if(NOT _good)
        set(_want_install TRUE)
    elseif(POLYBIND_DEPS_PIN AND _locked AND NOT _found_version VERSION_EQUAL _locked)
        set(_want_install TRUE)
    endif()
    if(_want_install AND NOT POLYBIND_DEPS_OFFLINE)
        if(POLYBIND_DEPS_PIN AND _locked)
            set(_requirement "${_package}==${_locked}")
        else()
            string(REPLACE " " "" _requirement "${_package}${_spec}")
        endif()
        _polybind_deps_python_install("${_installer}" "${_py}" "${_target_dir}" "${_requirement}")
        _polybind_deps_python_query("${_py}" "${_target_dir}" "${_module}" "${_package}" "${_module_cli}" "${_cmake_flag}" "${_version_command}" "${_cmake_function}")
        set(_found_cmake_dir "${_py_cmake_dir}")
        set(_found_version "${_py_version}")
        set(_good FALSE)
        if(_found_cmake_dir)
            polybind_version_satisfies("${_found_version}" "${_spec}" _good)
        endif()
    endif()

    if(NOT _found_cmake_dir OR NOT _good)
        message(STATUS "deps: ${name}: python package '${_package}' ${_spec} not usable (found: '${_found_version}')")
        return()
    endif()

    _polybind_json_get(_find_package "${json}" dependencies ${name} find_package)
    set(${_find_package}_DIR "${_found_cmake_dir}" CACHE PATH "${name}: CMake directory of the Python package" FORCE)
    find_package(${_find_package} CONFIG QUIET)
    if(NOT ${_find_package}_FOUND)
        message(STATUS "deps: ${name}: find_package(${_find_package}) found nothing in ${_found_cmake_dir}")
        return()
    endif()
    set(_polybind_dep_ok TRUE PARENT_SCOPE)
    set(_polybind_dep_version "${_found_version}" PARENT_SCOPE)
    set(_polybind_dep_cmake_dir "${_found_cmake_dir}" PARENT_SCOPE)
    set(_polybind_dep_installer "${_installer}" PARENT_SCOPE)
endfunction()

# --- fetch provider ------------------------------------------------------------------------------------------------

# tag-format: how a version becomes a tag name, with %s for the version: "v%s" (v2.2.0, the default), "%s" (2.2.0),
# "release-%s". It must contain %s exactly once.
function(_polybind_deps_check_tag_format format)
    string(REGEX MATCHALL "%s" _m "${format}")
    list(LENGTH _m _n)
    if(NOT _n EQUAL 1)
        message(FATAL_ERROR "deps: tag-format '${format}' must contain %s exactly once (for the version)")
    endif()
endfunction()

function(_polybind_deps_tag_name out format version)
    _polybind_deps_check_tag_format("${format}")
    string(REPLACE "%s" "${version}" _tag "${format}")
    set(${out} "${_tag}" PARENT_SCOPE)
endfunction()

# The regex that matches a tag made by <format> and captures the version in group 1 (CMake regex, special characters of the
# text around %s escaped).
function(_polybind_deps_tag_regex out format)
    _polybind_deps_check_tag_format("${format}")
    string(FIND "${format}" "%s" _at)
    string(SUBSTRING "${format}" 0 ${_at} _before)
    math(EXPR _after_at "${_at} + 2")
    string(SUBSTRING "${format}" ${_after_at} -1 _after)
    foreach(_part _before _after)
        string(REGEX REPLACE "([][^$.*+?()|\\\\-])" "\\\\\\1" ${_part} "${${_part}}")
    endforeach()
    set(${out} "^${_before}([0-9][0-9.]*)${_after}$" PARENT_SCOPE)
endfunction()

# The highest tag of <git_url> made by <tag_format> whose version satisfies <spec>; sets <out> to that version.
function(_polybind_deps_latest_tag out git_url tag_format spec)
    set(${out} "" PARENT_SCOPE)
    execute_process(COMMAND git ls-remote --tags --refs "${git_url}"
        OUTPUT_VARIABLE _refs RESULT_VARIABLE _rc ERROR_QUIET)
    if(NOT _rc EQUAL 0)
        return()
    endif()
    _polybind_deps_tag_regex(_regex "${tag_format}")
    string(REPLACE "\n" ";" _lines "${_refs}")
    set(_versions "")
    foreach(_line IN LISTS _lines)
        string(REGEX REPLACE "^[0-9a-f]+[ \t]+refs/tags/" "" _name "${_line}")
        if(_name MATCHES "${_regex}")
            polybind_version_satisfies("${CMAKE_MATCH_1}" "${spec}" _ok)
            if(_ok)
                list(APPEND _versions "${CMAKE_MATCH_1}")
            endif()
        endif()
    endforeach()
    if(_versions)
        list(SORT _versions COMPARE NATURAL ORDER DESCENDING)
        list(GET _versions 0 _best)
        set(${out} "${_best}" PARENT_SCOPE)
    endif()
endfunction()

# Sets _polybind_dep_ok, _polybind_dep_version, _polybind_dep_tag, _polybind_dep_commit in the caller.
function(_polybind_deps_provider_fetch name json)
    set(_polybind_dep_ok FALSE PARENT_SCOPE)
    _polybind_json_get(_url "${json}" dependencies ${name} fetch url)
    if(_url STREQUAL "")
        message(STATUS "deps: ${name}: no fetch.url in the settings, so the fetch provider is off")
        return()
    endif()
    _polybind_json_get_default(_tag_format "v%s" "${json}" dependencies ${name} fetch tag_format)  # v2.2.0; "%s" for 2.2.0
    _polybind_json_get(_spec "${json}" dependencies ${name} version_spec)
    _polybind_json_get(_locked_tag "${json}" dependencies ${name} lock tag)
    _polybind_json_get(_locked_version "${json}" dependencies ${name} lock version)
    _polybind_json_get(_locked_commit "${json}" dependencies ${name} lock commit)

    if(POLYBIND_DEPS_PIN)
        if(_locked_tag STREQUAL "")
            message(STATUS "deps: ${name}: POLYBIND_DEPS_PIN is ON but polybind.lock has no tag for it")
            return()
        endif()
        set(_tag "${_locked_tag}")  # a tag or a commit id, used as it is
        set(_version "${_locked_version}")
    else()
        _polybind_deps_latest_tag(_version "${_url}" "${_tag_format}" "${_spec}")
        if(_version STREQUAL "")
            message(STATUS "deps: ${name}: no tag of ${_url} satisfies '${_spec}'")
            return()
        endif()
        _polybind_deps_tag_name(_tag "${_tag_format}" "${_version}")
    endif()

    # Options of the dependency's own CMake (BUILD_PYTHON=OFF and the like).
    string(JSON _opts ERROR_VARIABLE _e GET "${json}" dependencies ${name} fetch build_options)
    if(NOT _e)
        string(JSON _n LENGTH "${_opts}")
        if(_n GREATER 0)
            math(EXPR _last "${_n} - 1")
            foreach(_i RANGE ${_last})
                string(JSON _key MEMBER "${_opts}" ${_i})
                string(JSON _val GET "${_opts}" "${_key}")
                if(_val MATCHES "^(ON|OFF)$")
                    set(${_key} ${_val} CACHE BOOL "" FORCE)
                else()
                    set(${_key} "${_val}" CACHE STRING "" FORCE)
                endif()
            endforeach()
        endif()
    endif()

    if(_POLYBIND_DEP_OPTIONAL AND NOT POLYBIND_DEPS_OFFLINE)
        # FetchContent aborts the whole configure when a clone fails, so an optional dependency checks first.
        execute_process(COMMAND git ls-remote --exit-code "${_url}" "${_tag}"
            RESULT_VARIABLE _rc OUTPUT_QUIET ERROR_QUIET)
        if(NOT _rc EQUAL 0)
            message(STATUS "deps: ${name}: ${_tag} of ${_url} cannot be reached")
            return()
        endif()
    endif()
    include(FetchContent)
    if(POLYBIND_DEPS_OFFLINE)
        set(FETCHCONTENT_FULLY_DISCONNECTED ON)
    endif()
    # One tag, depth 1, no submodules: no history is downloaded. A commit id cannot be fetched shallow.
    _polybind_deps_tag_regex(_tag_regex "${_tag_format}")
    if(_tag MATCHES "${_tag_regex}")
        set(_shallow TRUE)
    else()
        set(_shallow FALSE)
    endif()
    FetchContent_Declare(${name}
        GIT_REPOSITORY "${_url}"
        GIT_TAG "${_tag}"
        GIT_SHALLOW ${_shallow}
        GIT_SUBMODULES ""
        GIT_PROGRESS FALSE)
    message(STATUS "deps: fetching ${name} ${_tag} (git)")
    FetchContent_MakeAvailable(${name})
    string(TOLOWER "${name}" _lname)
    FetchContent_GetProperties(${name})
    set(_src "${${_lname}_SOURCE_DIR}")

    execute_process(COMMAND git -C "${_src}" rev-parse HEAD
        OUTPUT_VARIABLE _commit OUTPUT_STRIP_TRAILING_WHITESPACE RESULT_VARIABLE _rc ERROR_QUIET)
    if(NOT _rc EQUAL 0)
        set(_commit "")
    endif()
    if(POLYBIND_DEPS_PIN AND _locked_commit AND _commit AND NOT _commit STREQUAL _locked_commit)
        message(FATAL_ERROR "deps: ${name}: ${_tag} is at ${_commit} but polybind.lock recorded ${_locked_commit}.\n"
                            "The tag was moved. Fix the tag, or record the new commit on purpose.")
    endif()
    set(_polybind_dep_ok TRUE PARENT_SCOPE)
    set(_polybind_dep_version "${_version}" PARENT_SCOPE)
    set(_polybind_dep_tag "${_tag}" PARENT_SCOPE)
    set(_polybind_dep_commit "${_commit}" PARENT_SCOPE)
endfunction()

# --- system provider -----------------------------------------------------------------------------------------------

function(_polybind_deps_provider_system name json)
    set(_polybind_dep_ok FALSE PARENT_SCOPE)
    _polybind_json_get(_find_package "${json}" dependencies ${name} find_package)
    _polybind_json_get(_spec "${json}" dependencies ${name} version_spec)
    find_package(${_find_package} CONFIG QUIET)
    if(NOT ${_find_package}_FOUND)
        message(STATUS "deps: ${name}: find_package(${_find_package}) found nothing on the system")
        return()
    endif()
    polybind_version_satisfies("${${_find_package}_VERSION}" "${_spec}" _ok)
    if(NOT _ok)
        message(STATUS "deps: ${name}: the system has ${${_find_package}_VERSION}, which does not satisfy '${_spec}'")
        return()
    endif()
    set(_polybind_dep_ok TRUE PARENT_SCOPE)
    set(_polybind_dep_version "${${_find_package}_VERSION}" PARENT_SCOPE)
    set(_polybind_dep_cmake_dir "${${_find_package}_DIR}" PARENT_SCOPE)
endfunction()

# --- resolving -----------------------------------------------------------------------------------------------------

# The providers to try for one dependency, in order. A forced provider (POLYBIND_DEPS_PROVIDER[_<N>] other than auto) is the
# only one. With auto the default order is python, fetch (system never); the provider recorded in the lock goes first (uv and
# pip count as python); a preferred provider goes before that and keeps the others after it. Preferring uv or pip tries that one
# instead of python.
function(_polybind_deps_provider_order name json out)
    _polybind_deps_uname(_uname "${name}")
    set(_valid "python|uv|pip|fetch|system")
    set(_choice "${POLYBIND_DEPS_PROVIDER}")
    if(DEFINED POLYBIND_DEPS_PROVIDER_${_uname} AND NOT "${POLYBIND_DEPS_PROVIDER_${_uname}}" STREQUAL "")
        set(_choice "${POLYBIND_DEPS_PROVIDER_${_uname}}")
    endif()
    if(_choice MATCHES "^(${_valid})$")
        set(${out} ${_choice} PARENT_SCOPE)
        return()
    elseif(NOT _choice STREQUAL "auto")
        message(FATAL_ERROR "deps: POLYBIND_DEPS_PROVIDER must be auto, python, uv, pip, fetch or system, not '${_choice}'")
    endif()
    set(_order python fetch)
    _polybind_json_get(_locked "${json}" dependencies ${name} lock provider)
    if(_locked STREQUAL "fetch")
        set(_order fetch python)
    endif()
    _polybind_json_get(_prefer "${json}" dependencies ${name} prefer_provider)
    if(NOT "${POLYBIND_DEPS_PREFER_PROVIDER}" STREQUAL "")
        set(_prefer "${POLYBIND_DEPS_PREFER_PROVIDER}")
    endif()
    if(DEFINED POLYBIND_DEPS_PREFER_PROVIDER_${_uname} AND NOT "${POLYBIND_DEPS_PREFER_PROVIDER_${_uname}}" STREQUAL "")
        set(_prefer "${POLYBIND_DEPS_PREFER_PROVIDER_${_uname}}")
    endif()
    if(NOT "${_prefer}" STREQUAL "")
        if(NOT _prefer MATCHES "^(${_valid})$")
            message(FATAL_ERROR "deps: the preferred provider of ${name} must be python, uv, pip, fetch or system, not '${_prefer}'")
        endif()
        if(_prefer STREQUAL "uv" OR _prefer STREQUAL "pip")
            list(REMOVE_ITEM _order python)
        else()
            list(REMOVE_ITEM _order ${_prefer})
        endif()
        set(_order ${_prefer} ${_order})
    endif()
    set(${out} "${_order}" PARENT_SCOPE)
endfunction()

function(polybind_resolve_dep name)
    get_property(_json GLOBAL PROPERTY POLYBIND_DEPS_JSON)
    if(NOT _json)
        message(FATAL_ERROR "deps: call polybind_deps_load() before polybind_resolve_dep()")
    endif()
    string(JSON _entry ERROR_VARIABLE _err GET "${_json}" dependencies ${name})
    if(_err)
        message(FATAL_ERROR "deps: '${name}' is not declared in the settings ([deps.${name}] of polybind.toml)")
    endif()

    _polybind_deps_uname(_uname "${name}")
    string(TOLOWER "${name}" _lname)
    _polybind_json_get(_optional "${_json}" dependencies ${name} optional)
    set(_POLYBIND_DEP_OPTIONAL ${_optional})  # read by the providers
    set(_dir "${CMAKE_BINARY_DIR}/polybind-deps")
    file(MAKE_DIRECTORY "${_dir}")
    # No leftovers of an earlier configure: `polybind deps record` trusts these files.
    file(REMOVE "${_dir}/${_lname}.provider.txt" "${_dir}/${_lname}.version.txt" "${_dir}/${_lname}.tag.txt"
                "${_dir}/${_lname}.commit.txt")
    set(POLYBIND_DEP_${_uname}_FOUND FALSE PARENT_SCOPE)
    if(_optional AND POLYBIND_DEPS_SKIP_${_uname})
        message(STATUS "deps: ${name}: skipped (POLYBIND_DEPS_SKIP_${_uname}), optional")
        file(WRITE "${_dir}/${_lname}.provider.txt" "none\n")
        return()
    endif()
    _polybind_deps_provider_order(${name} "${_json}" _order)

    set(_used "")
    foreach(_provider IN LISTS _order)
        set(_polybind_dep_ok FALSE)
        set(_polybind_dep_version "")
        set(_polybind_dep_tag "")
        set(_polybind_dep_commit "")
        set(_polybind_dep_cmake_dir "")
        set(_polybind_dep_installer "")
        set(_POLYBIND_INSTALLER auto)
        set(_family ${_provider})
        if(_provider STREQUAL "uv" OR _provider STREQUAL "pip")
            set(_POLYBIND_INSTALLER ${_provider})
            set(_family python)
        endif()
        cmake_language(CALL _polybind_deps_provider_${_family} ${name} "${_json}")
        if(_polybind_dep_ok)
            set(_used ${_provider})
            break()
        endif()
    endforeach()
    if(NOT _used AND _optional)
        message(STATUS "deps: ${name}: not found (tried: ${_order}), optional, so it is left out")
        file(WRITE "${_dir}/${_lname}.provider.txt" "none\n")
        return()
    endif()
    if(NOT _used)
        message(FATAL_ERROR "deps: could not get '${name}' (tried: ${_order}). See the lines starting with 'deps:' above.\n"
                            "Options: -DPOLYBIND_DEPS_PROVIDER=python|uv|pip|fetch|system, -D${name}_DIR=<dir with ${name}Config.cmake>.")
    endif()
    # What delivered it: the installer for the python providers, never the word python.
    if(_polybind_dep_installer)
        set(_used "${_polybind_dep_installer}")
    endif()

    # Every target the rest of the build links must exist, whatever provider made it.
    string(JSON _nt ERROR_VARIABLE _e LENGTH "${_entry}" targets)
    if(NOT _e AND _nt GREATER 0)
        math(EXPR _last "${_nt} - 1")
        foreach(_i RANGE ${_last})
            string(JSON _t GET "${_entry}" targets ${_i})
            if(NOT TARGET ${_t})
                message(FATAL_ERROR "deps: ${name} came from the ${_used} provider but target ${_t} does not exist")
            endif()
        endforeach()
    endif()

    # Warn when this is not the version that was tested.
    _polybind_json_get(_locked "${_json}" dependencies ${name} lock version)
    if(_locked AND NOT "${_polybind_dep_version}" VERSION_EQUAL "${_locked}")
        set(_msg "deps: ${name} ${_polybind_dep_version} is not the tested version (${_locked}, polybind.lock)")
        if(POLYBIND_DEPS_STRICT)
            message(FATAL_ERROR "${_msg}")
        endif()
        message(WARNING "${_msg}")
    endif()

    # What was really used, for `polybind deps record` and for people reading the build log. A python provider has no tag
    # of its own: it is the version written the way the fetch provider would name it.
    if(NOT _polybind_dep_tag AND _polybind_dep_version)
        _polybind_json_get_default(_tag_format "v%s" "${_json}" dependencies ${name} fetch tag_format)
        _polybind_deps_tag_name(_polybind_dep_tag "${_tag_format}" "${_polybind_dep_version}")
    endif()
    file(WRITE "${_dir}/${_lname}.provider.txt" "${_used}\n")
    file(WRITE "${_dir}/${_lname}.version.txt" "${_polybind_dep_version}\n")
    file(WRITE "${_dir}/${_lname}.tag.txt" "${_polybind_dep_tag}\n")
    file(WRITE "${_dir}/${_lname}.commit.txt" "${_polybind_dep_commit}\n")
    set(POLYBIND_DEP_${_uname}_FOUND TRUE PARENT_SCOPE)
    set(POLYBIND_DEP_${_uname}_PROVIDER "${_used}" PARENT_SCOPE)
    set(POLYBIND_DEP_${_uname}_VERSION "${_polybind_dep_version}" PARENT_SCOPE)
    message(STATUS "deps: ${name} ${_polybind_dep_version} (${_used})")
endfunction()

# --- recording the tested tag --------------------------------------------------------------------------------------

# polybind_deps_record_target(): adds the target `polybind_deps_record`: after the tests passed, `cmake --build . --target
# polybind_deps_record` writes what this build resolved into polybind.lock. It refuses when POLYBIND_DEPS_PIN was ON.
function(polybind_deps_record_target)
    get_property(_root GLOBAL PROPERTY POLYBIND_DEPS_ROOT)
    get_property(_py GLOBAL PROPERTY POLYBIND_DEPS_PYTHON_EXE)
    if(NOT _root)
        message(FATAL_ERROR "deps: call polybind_deps_load() before polybind_deps_record_target()")
    endif()
    add_custom_target(polybind_deps_record
        COMMAND "${_py}" -m polybind deps record --root "${_root}" --build-dir "${CMAKE_BINARY_DIR}"
        COMMENT "Recording the resolved dependencies in polybind.lock"
        VERBATIM USES_TERMINAL)
endfunction()
