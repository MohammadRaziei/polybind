"""tests/test_deps.py: ``[deps.<name>]``, ``[optional-deps.<group>.<name>]``, requirements, lock check, CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from polybind.cli import main as cli_main
from polybind.config import ConfigError, load_config
from polybind.deps import (
    Dependencies,
    check_lock,
    load_dependencies,
    parse_dependency,
    requirement,
    requirements,
)
from polybind.lock import Lock, LockEntry

REPO_ROOT = Path(__file__).resolve().parent.parent

FULL = """\
schema = 1

[deps.liburlparser]
description  = "URL parsing"
version-spec = ">=2.2.0, <3"
targets      = ["liburlparser::urlparser"]
find-package = "liburlparser"
python.module        = "liburlparser"
python.module-cli    = true
python.cmake-flag    = "--cmake-dir"
python.version-command = "python -m {module} --version"
fetch.url            = "https://github.com/mohammadraziei/liburlparser.git"
fetch.tag-format     = "v%s"
fetch.build-options  = { BUILD_PYTHON = "OFF" }

[deps.httpp]
version-spec      = ">=0.7,<0.8"
targets           = ["httpp::httpp_core"]
runtime           = true
prefer-provider   = "python"
python.cmake-flag = ["py", "--cmake-dir"]

[deps.mbedtls]
version-spec = ">=3.6,<4"
fetch.url    = "https://example.invalid/mbedtls.git"
bundle       = true

[optional-deps.test.liburlparser]
runtime   = true
buildtime = false
python    = {}
"""


def load(tmp_path: Path, text: str) -> Dependencies:
    (tmp_path / "polybind.toml").write_text(text)
    return load_dependencies(load_config(tmp_path))


def parse(table: object, name: str = "x") -> tuple[object, list[str]]:
    warnings: list[str] = []
    return parse_dependency(name, table, "t", warnings), warnings


# --------------------------------------------------------------------------- defaults


def test_everything_has_a_default() -> None:
    dep, warnings = parse({})
    assert dep.name == "x"  # type: ignore[attr-defined]
    assert (dep.description, dep.version_spec, dep.targets) == ("", "", ["x::x"])  # type: ignore[attr-defined]
    assert dep.find_package == "x" and dep.prefer_provider == ""  # type: ignore[attr-defined]
    assert (dep.optional, dep.runtime, dep.buildtime, dep.bundle) == (False, False, True, False)  # type: ignore[attr-defined]
    assert dep.python is None and dep.fetch is None and dep.providers == []  # type: ignore[attr-defined]
    assert warnings == []


def test_empty_python_table_turns_the_provider_on_with_defaults() -> None:
    dep, _ = parse({"python": {}}, "my-lib")
    py = dep.python  # type: ignore[attr-defined]
    assert (py.package, py.module, py.module_cli) == ("my-lib", "my_lib", True)
    assert (py.cmake_flag, py.cmake_function) == (["--cmake-dir"], "")
    assert py.version_command == ["python", "-m", "my_lib", "--version"]
    assert dep.providers == ["python"]  # type: ignore[attr-defined]


def test_any_python_key_turns_it_on() -> None:
    dep, _ = parse({"python": {"module": "m"}})
    assert dep.python.module == "m"  # type: ignore[attr-defined]


def test_fetch_defaults() -> None:
    dep, _ = parse({"fetch": {"url": "u"}})
    assert (dep.fetch.tag_format, dep.fetch.build_options) == ("v%s", {})  # type: ignore[attr-defined]
    assert dep.tag_format == "v%s"  # type: ignore[attr-defined]


def test_module_cli_false_is_the_import_mode() -> None:
    dep, _ = parse({"python": {"module-cli": False}})
    py = dep.python  # type: ignore[attr-defined]
    assert (py.cmake_flag, py.version_command, py.cmake_function) == ([], [], "get_cmake_dir")
    dep, _ = parse({"python": {"module-cli": False, "cmake-function": "cmake_dir"}})
    assert dep.python.cmake_function == "cmake_dir"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("python", "expected"),
    [
        ({"module": "m"}, ["python", "-m", "m", "--version"]),
        ({"module": "m", "version-command": ""}, []),
        ({"module": "m", "version-command": "mbedtls --version"}, ["mbedtls", "--version"]),
        ({"module": "m", "version-command": ["mbedtls-cli", "--version"]}, ["mbedtls-cli", "--version"]),
        ({"module": "m", "version-command": "python -m {module} py --version"}, ["python", "-m", "m", "py", "--version"]),
        ({"module": "m", "version-command": "'my tool' -V"}, ["my tool", "-V"]),
        ({"module": "m", "module-cli": False}, []),
        ({"module": "m", "module-cli": False, "version-command": "m-cli --version"}, ["m-cli", "--version"]),
    ],
)
def test_version_command(python: dict[str, object], expected: list[str]) -> None:
    dep, _ = parse({"python": python})
    assert dep.python.version_command == expected  # type: ignore[attr-defined]


def test_full_file(tmp_path: Path) -> None:
    deps = load(tmp_path, FULL)
    assert list(deps.deps) == ["liburlparser", "httpp", "mbedtls"]
    url = deps.deps["liburlparser"]
    assert url.version_spec == ">=2.2.0,<3"  # blanks removed
    assert url.fetch is not None and url.fetch.build_options == {"BUILD_PYTHON": "OFF"}
    assert url.providers == ["python", "fetch"]
    assert deps.deps["httpp"].python.cmake_flag == ["py", "--cmake-dir"]  # type: ignore[union-attr]
    assert deps.deps["mbedtls"].bundle and deps.deps["mbedtls"].providers == ["fetch"]
    assert list(deps.groups["test"]) == ["liburlparser"]
    assert deps.groups["test"]["liburlparser"].group == "test"
    assert deps.warnings == []


# --------------------------------------------------------------------------- errors


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ({"versoin-spec": ">=1"}, "did you mean 'deps.x.version-spec'"),
        ({"version-spec": 3}, "version-spec must be a string"),
        ({"version-spec": "2.2"}, "invalid item '2.2'"),
        ({"version-spec": ">=1,,<2"}, "invalid item ''"),
        ({"targets": []}, "list of non-empty strings"),
        ({"targets": "a::b"}, "list of non-empty strings"),
        ({"optional": "yes"}, "optional must be true or false"),
        ({"prefer-provider": "conda"}, "expected one of python, uv, pip, fetch, system"),
        ({"runtime": False, "buildtime": False}, "both false"),
        ({"runtime": True}, "runtime needs the python provider"),
        ({"python": False}, "python must be a table"),
        ({"python": {"modul": "m"}}, "did you mean 'deps.x.python.module'"),
        ({"python": {"package": ""}}, "must not be empty"),
        ({"python": {"cmake-flag": []}}, "list of non-empty strings"),
        ({"python": {"cmake-function": "f"}}, "needs module-cli = false"),
        ({"python": {"module-cli": False, "cmake-flag": "--x"}}, "cmake-flag needs module-cli = true"),
        ({"python": {"version-flag": "--v"}}, "did you mean 'deps.x.python.version-command'"),
        ({"python": {"version-command": []}}, "must be a command"),
        ({"python": {"version-command": 3}}, "must be a command"),
        ({"python": {"version-command": ["tool", ""]}}, "must be a command"),
        ({"python": {"version-command": "tool 'unclosed"}}, "is not a command"),
        ({"fetch": {}}, "fetch.url is required"),
        ({"fetch": {"url": ""}}, "must not be empty"),
        ({"fetch": {"url": "u", "tag-format": "v"}}, "must contain %s"),
        ({"fetch": {"url": "u", "build-options": {"A": 1}}}, "table of strings"),
        ({"fetch": {"url": "u", "options": {}}}, "did you mean 'deps.x.fetch.build-options'"),
        ({"fetch": "u"}, "fetch must be a table"),
    ],
)
def test_invalid_dependency(table: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_dependency("x", table, "t", [])


def test_invalid_names_and_shapes(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid dependency name"):
        parse_dependency("-x", {}, "t", [])
    with pytest.raises(ConfigError, match="must be a table"):
        parse_dependency("x", 3, "t", [])
    with pytest.raises(ConfigError, match="invalid group name"):
        load(tmp_path, "[optional-deps.'bad group'.x]\n")
    with pytest.raises(ConfigError, match="must be a table of dependencies"):
        load(tmp_path, "[optional-deps]\ntest = 3\n")


def test_error_names_the_file_and_key(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"polybind\.toml: deps\.x\.runtime needs the python provider"):
        load(tmp_path, "[deps.x]\nruntime = true\n")


# --------------------------------------------------------------------------- warnings


def test_prefer_provider_without_its_table_warns() -> None:
    _, warnings = parse({"prefer-provider": "python"})
    assert warnings == ["t: deps.x: prefer-provider = python, but [deps.x.python] is not set"]
    _, warnings = parse({"prefer-provider": "uv"})
    assert "prefer-provider = uv" in warnings[0]
    _, warnings = parse({"prefer-provider": "fetch"})
    assert "deps.x.fetch.url is not set" in warnings[0]
    _, warnings = parse({"prefer-provider": "python", "python": {}})
    assert warnings == []
    _, warnings = parse({"prefer-provider": "system"})
    assert warnings == []


def test_bundle_with_runtime_warns() -> None:
    _, warnings = parse({"bundle": True, "runtime": True, "python": {}})
    assert len(warnings) == 1 and "bundle and runtime are both true" in warnings[0]


# --------------------------------------------------------------------------- requirements


def test_requirement_string() -> None:
    dep, _ = parse({"version-spec": ">=2.2.0, <3", "python": {"package": "liburlparser"}})
    assert requirement(dep) == "liburlparser>=2.2.0,<3"  # type: ignore[arg-type]
    dep, _ = parse({"python": {}}, "plain")
    assert requirement(dep) == "plain"  # type: ignore[arg-type]
    dep, _ = parse({"fetch": {"url": "u"}})
    assert requirement(dep) is None  # type: ignore[arg-type]


def test_requirement_kinds(tmp_path: Path) -> None:
    deps = load(tmp_path, FULL)
    # liburlparser: build (default) ; httpp: build + runtime ; mbedtls: fetch only -> no pip requirement
    assert requirements(deps, "build") == ["liburlparser>=2.2.0,<3", "httpp>=0.7,<0.8"]
    assert requirements(deps, "runtime") == ["httpp>=0.7,<0.8"]
    assert requirements(deps, "extra:test") == ["liburlparser"]
    # the group's buildtime entries join `build` only when the group is selected
    assert requirements(deps, "build", ("test",)) == ["liburlparser>=2.2.0,<3", "httpp>=0.7,<0.8"]


def test_selected_group_adds_its_buildtime_entries(tmp_path: Path) -> None:
    text = FULL + '\n[optional-deps.test.mbedtls]\nversion-spec = ">=3"\npython = {}\nbuildtime = true\nruntime = true\n'
    deps = load(tmp_path, text)
    assert requirements(deps, "build") == ["liburlparser>=2.2.0,<3", "httpp>=0.7,<0.8"]
    assert requirements(deps, "build", ("test",)) == [
        "liburlparser>=2.2.0,<3",
        "httpp>=0.7,<0.8",
        "mbedtls>=3",
    ]


def test_requirement_errors(tmp_path: Path) -> None:
    deps = load(tmp_path, FULL)
    with pytest.raises(ConfigError, match="unknown group 'nope'"):
        requirements(deps, "extra:nope")
    with pytest.raises(ConfigError, match="unknown group 'nope'"):
        requirements(deps, "build", ("nope",))
    with pytest.raises(ConfigError, match="unknown kind"):
        requirements(deps, "dev")


def test_no_duplicates(tmp_path: Path) -> None:
    text = '[deps.a]\npython = {}\nruntime = true\n[deps.b]\npython = {package = "a"}\nruntime = true\n'
    assert requirements(load(tmp_path, text), "runtime") == ["a"]


# --------------------------------------------------------------------------- lock check


def test_check_lock_clean(tmp_path: Path) -> None:
    deps = load(tmp_path, FULL)
    lock = Lock(
        {
            "liburlparser": LockEntry("v2.2.0", "uv"),
            "mbedtls": LockEntry("v3.6.1", "fetch", "a" * 40),
        }
    )
    assert check_lock(deps, lock) == ([], [])


def test_check_lock_warnings_and_errors(tmp_path: Path) -> None:
    deps = load(tmp_path, FULL)
    lock = Lock(
        {
            "gone": LockEntry("v1.0.0", "fetch"),  # not declared
            "httpp": LockEntry("v0.7.1", "fetch"),  # httpp has no fetch.url
            "mbedtls": LockEntry("v3.6.1", "pip"),  # mbedtls has no python provider
            "liburlparser": LockEntry("1cc8355a90", "pip"),  # a commit id cannot name a pip version
        }
    )
    warnings, errors = check_lock(deps, lock)
    assert any("[deps.gone]" in w and "stale" in w for w in warnings)
    assert any("[deps.httpp]" in w and "no fetch.url" in w for w in warnings)
    assert any("[deps.mbedtls]" in w and "no python provider" in w for w in warnings)
    # mbedtls: tag "v3.6.1" does name a pip version, so only the warning; liburlparser: a commit id cannot
    assert len(errors) == 1
    assert any("[deps.liburlparser]" in e and "commit id works with fetch only" in e for e in errors)


# --------------------------------------------------------------------------- command line


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "polybind.toml").write_text(FULL)
    return tmp_path


def run(root: Path, *args: str) -> int:
    return cli_main(["deps", *args[:1], "--root", str(root), *args[1:]])


def test_cli_validate_ok(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(project, "validate") == 0
    assert "ok: 4 dependencies, 0 warning(s)" in capsys.readouterr().out


def test_cli_validate_reports_lock_problems(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / "polybind.lock").write_text(
        'schema = 1\n[deps.gone]\ntag = "v1"\nprovider = "fetch"\n'
        '[deps.liburlparser]\ntag = "1cc8355a90"\nprovider = "uv"\n'
    )
    assert run(project, "validate") == 2
    err = capsys.readouterr().err
    assert "warning: polybind.lock: [deps.gone] is not declared" in err
    assert "error: polybind.lock: [deps.liburlparser] tag '1cc8355a90' cannot name a pip version" in err


def test_cli_validate_bad_settings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "polybind.toml").write_text("[deps.x]\nruntime = true\n")
    assert run(tmp_path, "validate") == 2
    assert "polybind: error:" in capsys.readouterr().err


def test_cli_validate_empty_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(tmp_path, "validate") == 0
    assert "ok: 0 dependencies" in capsys.readouterr().out


def test_cli_list(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(project, "list") == 0
    out = capsys.readouterr().out
    assert "liburlparser" in out and "providers: python, fetch" in out
    assert "test/liburlparser" in out
    assert "bundle" in out


def test_cli_list_json(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(project, "list", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert [d["name"] for d in data] == ["liburlparser", "httpp", "mbedtls", "liburlparser"]
    assert data[1]["python"]["cmake_flag"] == ["py", "--cmake-dir"]
    assert data[2]["python"] is None and data[0]["fetch"]["build_options"] == {"BUILD_PYTHON": "OFF"}


def test_cli_requirements(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(project, "requirements", "--kind", "build") == 0
    assert capsys.readouterr().out.splitlines() == ["liburlparser>=2.2.0,<3", "httpp>=0.7,<0.8"]
    assert run(project, "requirements", "--kind", "runtime") == 0
    assert capsys.readouterr().out.splitlines() == ["httpp>=0.7,<0.8"]
    assert run(project, "requirements", "--kind", "extra:test") == 0
    assert capsys.readouterr().out.splitlines() == ["liburlparser"]
    assert run(project, "requirements", "--kind", "extra:nope") == 2


def test_cli_through_the_main_entry(project: Path) -> None:
    out = subprocess.run(
        [sys.executable, "-m", "polybind", "deps", "requirements", "--kind", "runtime", "--root", str(project)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert out.returncode == 0
    assert out.stdout.strip() == "httpp>=0.7,<0.8"
