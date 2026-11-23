# polybind-config.cmake: the target polybind::polybind, the C++ dispatch helpers (header-only):
#
#     #include <polybind/dispatch.hpp>     # one Python name for a function on several kinds of data
#     #include <polybind/nanobind.hpp>     # the nanobind backend of it
#
# The target only adds the include path. It finds no binding library and links none: nanobind (or whichever backend
# header is included) is the project's own business.
#
# One file, two places it can be:
#   - the installed package: the headers are in ../include (find_package, with `polybind cmake-dir` as the path);
#   - a copy made by `polybind vendor DIR`: the headers are next to this file (include() it, or find_package it).
#
#     execute_process(COMMAND "${Python_EXECUTABLE}" -m polybind cmake-dir OUTPUT_VARIABLE _d OUTPUT_STRIP_TRAILING_WHITESPACE)
#     find_package(polybind CONFIG REQUIRED PATHS "${_d}" NO_DEFAULT_PATH)
#     target_link_libraries(my_module PRIVATE polybind::polybind)
#
# Option POLYBIND_LINT (ON): the check of names and signatures that runs when the module is imported (a name that ends
# with _inplace must belong to a function that changes its argument, and the other way round). OFF defines
# POLYBIND_LINT=0 for what links the target. Set it per project (a cache variable), before this file is read.
if(TARGET polybind::polybind)
    return()
endif()

get_filename_component(_polybind_here "${CMAKE_CURRENT_LIST_DIR}" ABSOLUTE)
if(EXISTS "${_polybind_here}/../include/polybind/dispatch.hpp")
    get_filename_component(_polybind_include "${_polybind_here}/../include" ABSOLUTE)
elseif(EXISTS "${_polybind_here}/dispatch.hpp")
    get_filename_component(_polybind_include "${_polybind_here}/.." ABSOLUTE)
else()
    set(polybind_FOUND FALSE)
    set(polybind_NOT_FOUND_MESSAGE "polybind-config.cmake: no polybind/dispatch.hpp next to ${_polybind_here} or in ../include")
    return()
endif()

option(POLYBIND_LINT "Check the names and signatures of the functions bound through polybind when the module is imported" ON)

add_library(polybind::polybind INTERFACE IMPORTED GLOBAL)
set_target_properties(polybind::polybind PROPERTIES INTERFACE_INCLUDE_DIRECTORIES "${_polybind_include}")
if(NOT POLYBIND_LINT)
    set_property(TARGET polybind::polybind APPEND PROPERTY INTERFACE_COMPILE_DEFINITIONS "POLYBIND_LINT=0")
endif()
unset(_polybind_here)
unset(_polybind_include)
set(polybind_FOUND TRUE)
