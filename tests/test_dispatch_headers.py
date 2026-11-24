"""
tests/test_dispatch_headers.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The C++ dispatch helpers (``polybind/dispatch.hpp``, ``polybind/nanobind.hpp``) and the CMake target ``polybind::polybind``.

* The target is checked by a CMake configure only (fast): from the installed package (``find_package``) and from a copy made by
  ``polybind vendor`` (``include``), with ``POLYBIND_LINT`` on and off.
* The behaviour is checked on a toy module (``tests/data/dispatch_module.cpp``) that is compiled through CMake against the target
  and run by ``tests/data/dispatch_checks.py``: marked ``native``, it needs CMake, a C++ compiler, nanobind and numpy.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from polybind import vendor
from polybind.deps import CMAKE_DIR

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = Path(__file__).resolve().parent / "data"

CMAKE = shutil.which("cmake")
needs_cmake = pytest.mark.skipif(CMAKE is None, reason="cmake is not installed")


def _cmake(*args: str | Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(CMAKE), *map(str, args)], capture_output=True, text=True, cwd=cwd)


# ---------------------------------------------------------------------------
# the CMake target (configure only)
# ---------------------------------------------------------------------------

PROBE = """\
cmake_minimum_required(VERSION 3.20)
project(probe LANGUAGES NONE)
{setup}
if(NOT TARGET polybind::polybind)
    message(FATAL_ERROR "no target")
endif()
get_target_property(_inc polybind::polybind INTERFACE_INCLUDE_DIRECTORIES)
get_target_property(_def polybind::polybind INTERFACE_COMPILE_DEFINITIONS)
message(STATUS "INCLUDE=${{_inc}}")
message(STATUS "DEFINITIONS=${{_def}}")
"""


def _probe(tmp_path: Path, setup: str, *flags: str) -> dict[str, str]:
    src = tmp_path / "probe"
    src.mkdir()
    (src / "CMakeLists.txt").write_text(PROBE.format(setup=setup), encoding="utf-8")
    out = _cmake("-S", src, "-B", tmp_path / "build", *flags)
    assert out.returncode == 0, out.stdout + out.stderr
    found = {}
    for line in out.stdout.splitlines():
        for key in ("INCLUDE", "DEFINITIONS"):
            if line.startswith(f"-- {key}="):
                found[key] = line.split("=", 1)[1]
    return found


def _find_package_setup() -> str:
    return f'find_package(polybind CONFIG REQUIRED PATHS "{CMAKE_DIR.as_posix()}" NO_DEFAULT_PATH)'


@needs_cmake
def test_the_target_of_the_installed_package_adds_the_include_path(tmp_path: Path) -> None:
    found = _probe(tmp_path, _find_package_setup())
    assert Path(found["INCLUDE"]) == (REPO_ROOT / "polybind" / "include").resolve()
    assert (Path(found["INCLUDE"]) / "polybind" / "dispatch.hpp").is_file()
    assert found["DEFINITIONS"].endswith("NOTFOUND")  # lint is on: nothing is defined


@needs_cmake
def test_the_target_of_a_vendored_copy_adds_the_directory_that_holds_polybind(tmp_path: Path) -> None:
    vendor.vendor(tmp_path / "third_party")
    config = (tmp_path / "third_party" / "polybind" / "polybind-config.cmake").as_posix()
    found = _probe(tmp_path, f'include("{config}")')
    assert Path(found["INCLUDE"]) == (tmp_path / "third_party").resolve()
    assert (Path(found["INCLUDE"]) / "polybind" / "nanobind.hpp").is_file()


@needs_cmake
@pytest.mark.parametrize("mode", ["installed", "vendored"])
def test_polybind_lint_off_defines_it_for_whatever_links_the_target(tmp_path: Path, mode: str) -> None:
    if mode == "installed":
        setup = _find_package_setup()
    else:
        vendor.vendor(tmp_path / "third_party")
        setup = f'include("{(tmp_path / "third_party" / "polybind" / "polybind-config.cmake").as_posix()}")'
    assert _probe(tmp_path, setup, "-DPOLYBIND_LINT=OFF")["DEFINITIONS"] == "POLYBIND_LINT=0"


@needs_cmake
def test_the_target_finds_no_binding_library_and_links_none(tmp_path: Path) -> None:
    # polybind::polybind is only an include path: the project's own find_package(nanobind) is not needed to configure it
    found = _probe(tmp_path, _find_package_setup())
    assert "nanobind" not in found["INCLUDE"]
    text = (REPO_ROOT / "polybind" / "cmake" / "polybind-config.cmake").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))  # the usage in the comments is not code
    assert "find_package(nanobind" not in code and "target_link_libraries" not in code


@needs_cmake
def test_a_config_without_headers_is_not_found(tmp_path: Path) -> None:
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    shutil.copy(REPO_ROOT / "polybind" / "cmake" / "polybind-config.cmake", lonely)
    src = tmp_path / "probe"
    src.mkdir()
    (src / "CMakeLists.txt").write_text(
        'cmake_minimum_required(VERSION 3.20)\nproject(p LANGUAGES NONE)\n'
        f'find_package(polybind CONFIG QUIET PATHS "{lonely.as_posix()}" NO_DEFAULT_PATH)\n'
        'if(polybind_FOUND)\n    message(FATAL_ERROR "found")\nendif()\n',
        encoding="utf-8",
    )
    out = _cmake("-S", src, "-B", tmp_path / "build")
    assert out.returncode == 0, out.stdout + out.stderr


# ---------------------------------------------------------------------------
# the behaviour (a compiled toy module)
# ---------------------------------------------------------------------------

LISTS = """\
cmake_minimum_required(VERSION 3.20)
project(dispatch_module LANGUAGES CXX)
find_package(Python 3 COMPONENTS Interpreter Development.Module REQUIRED)
execute_process(COMMAND "${{Python_EXECUTABLE}}" -m nanobind --cmake_dir OUTPUT_VARIABLE NB_DIR OUTPUT_STRIP_TRAILING_WHITESPACE)
find_package(nanobind CONFIG REQUIRED PATHS "${{NB_DIR}}" NO_DEFAULT_PATH)
{setup}
nanobind_add_module(dispatch_module NB_STATIC dispatch_module.cpp)
target_link_libraries(dispatch_module PRIVATE polybind::polybind)
set_target_properties(dispatch_module PROPERTIES CXX_STANDARD 17 CXX_STANDARD_REQUIRED ON)
"""


def _why_not_native() -> str | None:
    if CMAKE is None:
        return "cmake is not installed"
    if not any(shutil.which(c) for c in ("c++", "g++", "clang++", "cl")):
        return "no C++ compiler"
    for module in ("nanobind", "numpy"):
        if importlib.util.find_spec(module) is None:
            return f"{module} is not installed"
    return None


def _build(root: Path, setup: str, *flags: str) -> Path:
    src = root / "src"
    src.mkdir()
    (src / "CMakeLists.txt").write_text(LISTS.format(setup=setup), encoding="utf-8")
    shutil.copy(DATA / "dispatch_module.cpp", src)
    generator = ["-G", "Ninja"] if shutil.which("ninja") else []
    out = _cmake("-S", src, "-B", root / "build", "-DCMAKE_BUILD_TYPE=Release", f"-DPython_EXECUTABLE={sys.executable}", *generator, *flags)
    assert out.returncode == 0, out.stdout + out.stderr
    out = _cmake("--build", root / "build", "--config", "Release")
    assert out.returncode == 0, out.stdout + out.stderr
    modules = [p for p in (root / "build").rglob("dispatch_module*") if p.suffix in {".so", ".pyd", ".dylib"}]
    assert modules, "the module was not built"
    return modules[0].parent


def _run_checks(module_dir: Path) -> dict:
    env = {**os.environ, "PYTHONPATH": str(module_dir)}
    out = subprocess.run([sys.executable, str(DATA / "dispatch_checks.py")], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def installed(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    """The toy module built against the installed package (find_package), lint on."""
    reason = _why_not_native()
    if reason:
        pytest.skip(reason)
    root = tmp_path_factory.mktemp("installed")
    module_dir = _build(root, _find_package_setup())
    return module_dir, _run_checks(module_dir)


@pytest.fixture(scope="module")
def vendored(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    """The toy module built against a copy made by `polybind vendor` (include), lint off."""
    reason = _why_not_native()
    if reason:
        pytest.skip(reason)
    root = tmp_path_factory.mktemp("vendored")
    vendor.vendor(root / "third_party")
    config = (root / "third_party" / "polybind" / "polybind-config.cmake").as_posix()
    module_dir = _build(root, f'include("{config}")', "-DPOLYBIND_LINT=OFF")
    return module_dir, _run_checks(module_dir)


@pytest.mark.native
@pytest.mark.parametrize("which", ["installed", "vendored"])
class TestBehaviour:
    @staticmethod
    def results(request: pytest.FixtureRequest, which: str) -> dict:
        return request.getfixturevalue(which)[1]

    def test_a_function_that_changes_its_argument_works_on_every_kind_and_gives_it_back(self, request, which) -> None:
        r = self.results(request, which)
        assert r["same_object"] == [True, True, True]
        assert r["values"] == [[2, 3, 4], 1, 2.0]  # the float array was changed twice (add_inplace, then add2_inplace)
        assert r["returning_lambda_same_object"] is True

    def test_an_enum_is_given_by_name_or_as_itself_and_a_bad_name_is_a_value_error(self, request, which) -> None:
        r = self.results(request, which)
        assert r["enum_name"] is True and r["enum_member"] is True
        assert r["enum_bad_name"] == 'ValueError: unknown mode "bad": use luma or each'
        assert r["enum_wrong_type"].startswith("TypeError")

    def test_nothing_is_converted_silently(self, request, which) -> None:
        refused = self.results(request, which)["refused"]
        assert set(refused) == {"float64", "int32", "read_only", "non_contiguous", "list"}
        for case, outcome in refused.items():
            assert outcome.startswith("TypeError"), (case, outcome)

    def test_a_second_value_of_the_same_kind(self, request, which) -> None:
        r = self.results(request, which)
        assert r["peer_ok"] == [True, 6.0]
        for case, outcome in r["peer_refused"].items():
            assert outcome.startswith("TypeError"), (case, outcome)

    def test_the_method_of_the_class_chains(self, request, which) -> None:
        assert self.results(request, which)["chain"] == [4, 5, 6]

    def test_a_lambda_that_returns_another_object_is_caught_when_it_runs(self, request, which) -> None:
        r = self.results(request, which)
        assert r["bad_inplace_pic"].startswith("RuntimeError")
        assert r["bad_inplace_array"] is True  # for the array kinds the same lambda does return its argument


    def test_a_function_that_makes_a_new_value_works_on_every_kind(self, request, which) -> None:
        new = self.results(request, which)["new"]
        assert new["u8"] == ["uint8", [2, 3], [1, 3, 5, 7, 9, 11], False, False]  # a new array of the same dtype
        assert new["f32"] == ["float32", [2, 3], [0.0, 2.0, 4.0, 6.0, 8.0, 10.0], False, False]
        assert new["input_unchanged"] == [[0, 1, 2, 3, 4, 5], [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]]
        assert new["pic"] == [[2, 4, 6], False, [1, 2, 3], "Pic"]
        assert new["chain"] == [6, 12, 18]
        assert new["enum"][0] == [1, 1, 1, 1, 1, 1]  # n = 0, mode "EACH" (= 1)
        assert new["enum"][1] == 'ValueError: unknown mode "bad": use luma or each'

    def test_the_input_of_such_a_function_may_be_read_only_but_is_never_converted(self, request, which) -> None:
        new = self.results(request, which)["new"]
        assert new["read_only_input"] == [0, 1, 2, 3, 4, 5]
        assert set(new["refused"]) == {"float64", "int32", "non_contiguous", "list"}
        for case, outcome in new["refused"].items():
            assert outcome.startswith("TypeError"), (case, outcome)

    def test_the_result_may_be_of_another_kind_than_the_argument(self, request, which) -> None:
        assert self.results(request, which)["new"]["other_kind"] == ["uint8", [0, 1, 2, 3, 4, 5], "uint8", [101, 102, 103]]

    def test_a_second_value_of_the_same_kind_for_a_function_that_makes_a_new_value(self, request, which) -> None:
        new = self.results(request, which)["new"]
        assert new["blended"] == [[0.0, 2.0, 4.0, 6.0, 8.0, 10.0], "uint8", [2, 4, 6]]
        assert all(o.startswith("TypeError") for o in new["blended_refused"]), new["blended_refused"]

    def test_the_memory_of_a_result_belongs_to_the_array(self, request, which) -> None:
        assert self.results(request, which)["new"]["owned_memory"] is True


@pytest.mark.native
def test_lint_warns_when_the_module_is_imported(installed: tuple[Path, dict]) -> None:
    warnings = installed[1]["import_warnings"]
    assert len(warnings) == 2
    assert warnings[0].startswith("RuntimeWarning: polybind: 'add3' changes its first parameter")
    assert "does not end with _inplace" in warnings[0]
    assert warnings[1].startswith("RuntimeWarning: polybind: 'peek_inplace' ends with _inplace")
    assert "takes the first parameter as const" in warnings[1]


@pytest.mark.native
def test_lint_is_an_error_under_w_error(installed: tuple[Path, dict]) -> None:
    env = {**os.environ, "PYTHONPATH": str(installed[0])}
    out = subprocess.run([sys.executable, "-W", "error::RuntimeWarning", "-c", "import dispatch_module"], capture_output=True, text=True, env=env)
    assert out.returncode != 0
    assert "polybind: 'add3'" in out.stderr


@pytest.mark.native
def test_lint_off_makes_no_warning(vendored: tuple[Path, dict]) -> None:
    assert vendored[1]["import_warnings"] == []
