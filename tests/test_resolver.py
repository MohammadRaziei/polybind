"""tests/test_resolver.py: cmake/PolybindDeps.cmake and `polybind deps record`, run with real CMake, pip, uv and git."""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from polybind.deps import CMAKE_DIR
from polybind.lock import Lock, LockEntry, read_lock, write_lock

REPO_ROOT = Path(__file__).resolve().parent.parent
CMAKE = shutil.which("cmake")
GIT = shutil.which("git")
UV = shutil.which("uv")

pytestmark = pytest.mark.skipif(CMAKE is None or GIT is None, reason="needs cmake and git")
needs_uv = pytest.mark.skipif(UV is None, reason="needs uv")

MAIN = """\
import sys
from pathlib import Path

if "--cmake-dir" in sys.argv:
    print(Path(__file__).parent / "cmake")
elif "--version" in sys.argv:
    print("@VERSION@")
else:
    sys.exit(2)
"""


# --------------------------------------------------------------------------- fixtures


def make_wheel(
    dest: Path, name: str, version: str, *, cli: bool = True, reported: str | None = None, script: str | None = None
) -> Path:
    """A pure Python wheel with a CMake package inside, like liburlparser or httpp ship.

    `reported` is what the CLI and the console script `script` print for --version when it is not the metadata version.
    """
    dist = f"{name}-{version}.dist-info"
    files = {
        f"{name}/__init__.py": "from pathlib import Path\n\n\ndef get_cmake_dir():\n    return str(Path(__file__).parent / 'cmake')\n",
        f"{name}/cmake/{name}Config.cmake": f"if(NOT TARGET {name}::{name})\n    add_library({name}::{name} INTERFACE IMPORTED)\nendif()\n",
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        f"{dist}/WHEEL": "Wheel-Version: 1.0\nGenerator: polybind-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    if cli:
        files[f"{name}/__main__.py"] = MAIN.replace("@VERSION@", reported or version)
    if script:
        files[f"{dist[:-10]}.data/scripts/{script}"] = f"#!python\nprint({(reported or version)!r})\n"
    record = ""
    for path, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=").decode()
        record += f"{path},sha256={digest},{len(text.encode())}\n"
    files[f"{dist}/RECORD"] = record + f"{dist}/RECORD,,\n"
    dest.mkdir(parents=True, exist_ok=True)
    wheel = dest / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return wheel


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout.strip()


def make_repo(path: Path, name: str, tags: list[str]) -> Path:
    """A git repository with one commit per tag; its CMake project defines <name>::<name>."""
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    (path / "CMakeLists.txt").write_text(
        f"cmake_minimum_required(VERSION 3.21)\nproject({name} LANGUAGES NONE)\n"
        f"message(STATUS \"{name} opt=${{{name.upper()}_OPT}}\")\n"
        f"add_library({name} INTERFACE)\nadd_library({name}::{name} ALIAS {name})\n"
    )
    for tag in tags:
        (path / "TAG").write_text(tag)
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", tag)
        git(path, "tag", tag)
    return path


def run_cmake(*args: str, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), **(env or {})}
    out = subprocess.run([str(CMAKE), *args], env=full_env, capture_output=True, text=True, check=False)
    out.stdout = " ".join((out.stdout + out.stderr).split())  # CMake wraps long messages: compare without line breaks
    if check and out.returncode != 0:
        raise AssertionError(f"cmake {' '.join(args)} failed:\n{out.stdout}")
    return out


class Project:
    """A tiny CMake project that includes PolybindDeps.cmake, with a wheelhouse next to it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.src = root / "src"
        self.src.mkdir()
        self.wheels = root / "wheels"
        self.wheels.mkdir()
        self.build = root / "build"

    def write(self, toml: str, body: str = "polybind_resolve_dep(fakelib)") -> None:
        (self.src / "polybind.toml").write_text(toml)
        (self.src / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.21)\nproject(t LANGUAGES NONE)\n"
            f'include("{CMAKE_DIR.as_posix()}/PolybindDeps.cmake")\n'
            f"polybind_deps_load()\n{body}\npolybind_deps_record_target()\n"
        )

    @property
    def env(self) -> dict[str, str]:
        links = str(self.wheels)
        return {"PIP_NO_INDEX": "1", "PIP_FIND_LINKS": links, "UV_NO_INDEX": "1", "UV_FIND_LINKS": links,
                "PIP_NO_CACHE_DIR": "1"}

    def configure(self, *defs: str, check: bool = True, build: Path | None = None) -> subprocess.CompletedProcess[str]:
        return run_cmake("-S", str(self.src), "-B", str(build or self.build),
                         f"-DPOLYBIND_DEPS_PYTHON={sys.executable}", *defs, env=self.env, check=check)

    def record(self, check: bool = True) -> subprocess.CompletedProcess[str]:
        return run_cmake("--build", str(self.build), "--target", "polybind_deps_record", env=self.env, check=check)

    def state(self, name: str, what: str) -> str:
        return (self.build / "polybind-deps" / f"{name}.{what}.txt").read_text().strip()

    @property
    def lock(self) -> Lock:
        return read_lock(self.src / "polybind.lock")


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project(tmp_path)


FAKELIB = '[deps.fakelib]\nversion-spec = ">=2,<3"\npython = {}\n'


# --------------------------------------------------------------------------- version ranges


def satisfies(version: str, spec: str) -> bool | None:
    script = Path(os.environ.get("TMPDIR", "/tmp")) / "polybind_satisfies.cmake"
    script.write_text(
        f'include("{CMAKE_DIR.as_posix()}/PolybindDeps.cmake")\n'
        'polybind_version_satisfies("${V}" "${S}" R)\nmessage("RESULT=${R}")\n'
    )
    out = run_cmake(f"-DV={version}", f"-DS={spec}", "-P", str(script), check=False)
    if out.returncode != 0:
        return None
    return "RESULT=TRUE" in out.stdout


@pytest.mark.parametrize(
    ("version", "spec", "expected"),
    [
        ("2.2.0", ">=2.2.0,<3", True),
        ("2.9.9", ">=2.2.0,<3", True),
        ("3.0.0", ">=2.2.0,<3", False),
        ("2.1.9", ">=2.2.0,<3", False),
        ("2.2.0", ">2.2.0", False),
        ("2.2.1", ">2.2.0", True),
        ("2.2.0", "<=2.2.0", True),
        ("1.0", "==1.0.0", True),
        ("1.1", "!=1.1", False),
        ("2.2.0", "", True),
        ("2.2.0", " >= 2.2.0 , < 3 ", True),
        ("0.0.0+unknown", ">=0", False),
        ("2.2.5", "~=2.2.1", True),
        ("2.2.0", "~=2.2.1", False),
        ("2.3.0", "~=2.2.1", False),
        ("2.9", "~=2.2", True),
        ("3.0", "~=2.2", False),
    ],
)
def test_version_satisfies(version: str, spec: str, expected: bool) -> None:
    assert satisfies(version, spec) is expected


@pytest.mark.parametrize("spec", ["2.2", ">=2.2rc1", "=2.2", "~=2", ">=2.*"])
def test_version_satisfies_rejects_what_it_cannot_do(spec: str) -> None:
    assert satisfies("2.2.0", spec) is None


# --------------------------------------------------------------------------- python providers


@pytest.mark.parametrize("installer", ["pip", pytest.param("uv", marks=needs_uv)])
def test_python_provider_installs_and_records(project: Project, installer: str) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0")
    make_wheel(project.wheels, "fakelib", "2.5.0")
    project.write(FAKELIB)
    out = project.configure("-DPOLYBIND_DEPS_PIN=OFF", f"-DPOLYBIND_DEPS_PROVIDER={installer}")
    assert f"deps: fakelib 2.5.0 ({installer})" in out.stdout  # the highest in range
    assert project.state("fakelib", "provider") == installer
    assert project.state("fakelib", "tag") == "v2.5.0"
    assert project.state("fakelib", "commit") == ""

    project.record()
    assert project.lock.entries == {"fakelib": LockEntry("v2.5.0", installer)}


def test_pinned_python_provider_installs_the_recorded_version(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0")
    make_wheel(project.wheels, "fakelib", "2.5.0")
    project.write(FAKELIB)
    write_lock(project.src / "polybind.lock", Lock({"fakelib": LockEntry("v2.2.0", "pip")}))
    out = project.configure()  # POLYBIND_DEPS_PIN is ON
    assert "deps: fakelib 2.2.0 (" in out.stdout
    assert "not the tested version" not in out.stdout


def test_pinned_python_provider_replaces_an_installed_version_that_is_not_the_recorded_one(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0")
    make_wheel(project.wheels, "fakelib", "2.5.0")
    project.write(FAKELIB)
    assert "deps: fakelib 2.5.0 (" in project.configure("-DPOLYBIND_DEPS_PIN=OFF").stdout  # now 2.5.0 is in the build tree
    write_lock(project.src / "polybind.lock", Lock({"fakelib": LockEntry("v2.2.0", "pip")}))
    out = project.configure("-DPOLYBIND_DEPS_PIN=ON")  # same build tree, in range, but not the recorded version
    assert "deps: fakelib 2.2.0 (" in out.stdout
    assert "not the tested version" not in out.stdout


def test_python_provider_without_a_lock_takes_the_newest(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0")
    make_wheel(project.wheels, "fakelib", "2.5.0")
    project.write(FAKELIB)
    assert "deps: fakelib 2.5.0 (" in project.configure().stdout


def test_python_alias_is_uv_when_found_else_pip(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0")
    project.write(FAKELIB)
    project.configure("-DPOLYBIND_DEPS_PROVIDER=python")
    assert project.state("fakelib", "provider") == ("uv" if UV else "pip")


def test_module_without_a_command_line(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0", cli=False)
    project.write('[deps.fakelib]\nversion-spec = ">=2"\npython.module-cli = false\n')
    out = project.configure()
    assert "deps: fakelib 2.2.0 (" in out.stdout  # the version comes from the package metadata


def test_version_command_defaults_to_the_module_cli(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.0.0", reported="2.5.0")  # the CLI says 2.5.0, the metadata 2.0.0
    project.write('[deps.fakelib]\nversion-spec = ">=2,<3"\npython = {}\n')
    assert "deps: fakelib 2.5.0 (" in project.configure().stdout


def test_empty_version_command_reads_the_metadata(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.0.0", reported="2.5.0")
    project.write('[deps.fakelib]\nversion-spec = ">=2,<3"\npython.version-command = ""\n')
    assert "deps: fakelib 2.0.0 (" in project.configure().stdout


def test_version_command_can_be_a_console_script(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.0.0", cli=False, reported="2.5.0", script="fakelib-tool")
    project.write('[deps.fakelib]\nversion-spec = ">=2,<3"\npython.module-cli = false\npython.version-command = "fakelib-tool --version"\n')
    assert "deps: fakelib 2.5.0 (" in project.configure().stdout


def test_version_command_can_be_a_list_with_the_module_in_it(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.0.0", reported="2.5.0")
    project.write('[deps.fakelib]\nversion-spec = ">=2,<3"\npython.version-command = ["python", "-m", "{module}", "--version"]\n')
    assert "deps: fakelib 2.5.0 (" in project.configure().stdout


def test_a_version_command_that_fails_falls_back_to_the_metadata(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0")
    project.write('[deps.fakelib]\nversion-spec = ">=2"\npython.version-command = "no-such-tool --version"\n')
    assert "deps: fakelib 2.2.0 (" in project.configure().stdout


def test_a_wrong_version_is_not_used(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0")
    project.write('[deps.fakelib]\nversion-spec = ">=3"\npython = {}\n')
    out = project.configure(check=False)
    assert out.returncode != 0
    assert "could not get 'fakelib'" in out.stdout
    assert "not usable" in out.stdout


def test_offline_installs_nothing(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0")
    project.write(FAKELIB)
    out = project.configure("-DPOLYBIND_DEPS_OFFLINE=ON", check=False)
    assert out.returncode != 0 and "could not get 'fakelib'" in out.stdout


def test_optional_dependency_can_be_missing(project: Project) -> None:
    project.write('[deps.fakelib]\nversion-spec = ">=2"\npython = {}\noptional = true\n',
                  'polybind_resolve_dep(fakelib)\nmessage(STATUS "FOUND=${POLYBIND_DEP_FAKELIB_FOUND}")')
    out = project.configure()
    assert "optional, so it is left out" in out.stdout and "FOUND=FALSE" in out.stdout
    assert project.state("fakelib", "provider") == "none"


def test_skipped_optional_dependency(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0")
    project.write('[deps.fakelib]\npython = {}\noptional = true\n')
    out = project.configure("-DPOLYBIND_DEPS_SKIP_FAKELIB=ON")
    assert "skipped (POLYBIND_DEPS_SKIP_FAKELIB)" in out.stdout


def test_missing_target_is_an_error(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0")
    project.write('[deps.fakelib]\ntargets = ["fakelib::nope"]\npython = {}\n')
    out = project.configure(check=False)
    assert out.returncode != 0 and "target fakelib::nope does not exist" in out.stdout


def test_found_variables(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0")
    project.write(FAKELIB, "polybind_resolve_dep(fakelib)\n"
                  'message(STATUS "R=${POLYBIND_DEP_FAKELIB_FOUND}/${POLYBIND_DEP_FAKELIB_PROVIDER}/${POLYBIND_DEP_FAKELIB_VERSION}")')
    out = project.configure("-DPOLYBIND_DEPS_PROVIDER=pip")
    assert "R=TRUE/pip/2.2.0" in out.stdout


# --------------------------------------------------------------------------- fetch provider


def fetch_toml(repo: Path, extra: str = "") -> str:
    return f'[deps.fetchlib]\nversion-spec = ">=1,<3"\nfetch.url = "{repo.as_posix()}"\n{extra}'


FETCHLIB = "polybind_resolve_dep(fetchlib)"


def test_fetch_provider_picks_the_newest_tag_in_range_and_records_the_commit(project: Project) -> None:
    repo = make_repo(project.root / "fetchlib", "fetchlib", ["v1.0.0", "v2.2.0", "v3.0.0"])
    project.write(fetch_toml(repo), FETCHLIB)
    out = project.configure("-DPOLYBIND_DEPS_PIN=OFF")
    assert "deps: fetchlib 2.2.0 (fetch)" in out.stdout
    commit = git(repo, "rev-parse", "v2.2.0^{commit}")
    assert project.state("fetchlib", "commit") == commit

    project.record()
    assert project.lock.entries == {"fetchlib": LockEntry("v2.2.0", "fetch", commit)}


def test_pinned_fetch_uses_the_recorded_tag(project: Project) -> None:
    repo = make_repo(project.root / "fetchlib", "fetchlib", ["v1.0.0", "v2.2.0"])
    project.write(fetch_toml(repo), FETCHLIB)
    commit = git(repo, "rev-parse", "v1.0.0^{commit}")
    write_lock(project.src / "polybind.lock", Lock({"fetchlib": LockEntry("v1.0.0", "fetch", commit)}))
    out = project.configure()
    assert "deps: fetchlib 1.0.0 (fetch)" in out.stdout


def test_pinned_fetch_needs_a_lock(project: Project) -> None:
    repo = make_repo(project.root / "fetchlib", "fetchlib", ["v1.0.0"])
    project.write(fetch_toml(repo), FETCHLIB)
    out = project.configure(check=False)
    assert out.returncode != 0 and "polybind.lock has no tag for it" in out.stdout


def test_a_moved_tag_is_caught_only_when_the_lock_has_a_commit(project: Project) -> None:
    repo = make_repo(project.root / "fetchlib", "fetchlib", ["v1.0.0"])
    project.write(fetch_toml(repo), FETCHLIB)
    write_lock(project.src / "polybind.lock", Lock({"fetchlib": LockEntry("v1.0.0", "fetch", "0" * 40)}))
    out = project.configure(check=False)
    assert out.returncode != 0 and "The tag was moved" in out.stdout

    write_lock(project.src / "polybind.lock", Lock({"fetchlib": LockEntry("v1.0.0", "fetch")}))
    assert "deps: fetchlib 1.0.0 (fetch)" in project.configure(build=project.root / "build2").stdout


def test_a_commit_id_works_as_the_tag(project: Project) -> None:
    repo = make_repo(project.root / "fetchlib", "fetchlib", ["v1.0.0", "v2.0.0"])
    project.write(fetch_toml(repo), FETCHLIB)
    first = git(repo, "rev-parse", "v1.0.0^{commit}")
    write_lock(project.src / "polybind.lock", Lock({"fetchlib": LockEntry(first, "fetch")}))
    out = project.configure()
    assert "(fetch)" in out.stdout
    assert project.state("fetchlib", "commit") == first
    assert project.state("fetchlib", "tag") == first


def test_fetch_build_options_and_tag_format(project: Project) -> None:
    repo = make_repo(project.root / "fetchlib", "fetchlib", ["rel-1.0.0", "rel-2.0.0"])
    project.write(fetch_toml(repo, 'fetch.tag-format = "rel-%s"\nfetch.build-options = { FETCHLIB_OPT = "ON" }\n'), FETCHLIB)
    out = project.configure("-DPOLYBIND_DEPS_PIN=OFF")
    assert "deps: fetchlib 2.0.0 (fetch)" in out.stdout
    assert "fetchlib opt=ON" in out.stdout
    assert project.state("fetchlib", "tag") == "rel-2.0.0"


def test_no_tag_in_range(project: Project) -> None:
    repo = make_repo(project.root / "fetchlib", "fetchlib", ["v5.0.0"])
    project.write(fetch_toml(repo), FETCHLIB)
    out = project.configure("-DPOLYBIND_DEPS_PIN=OFF", check=False)
    assert out.returncode != 0 and "no tag of" in out.stdout


# --------------------------------------------------------------------------- provider order


BOTH = '[deps.fakelib]\nversion-spec = ">=1,<3"\npython = {}\nfetch.url = "@URL@"\n'


@pytest.fixture
def both(project: Project) -> Project:
    make_wheel(project.wheels, "fakelib", "2.1.0")  # both providers can deliver v2.1.0, so only the order decides
    make_wheel(project.wheels, "fakelib", "2.2.0")
    repo = make_repo(project.root / "fakelib-repo", "fakelib", ["v1.0.0", "v2.1.0"])
    project.repo = repo  # type: ignore[attr-defined]
    return project


def both_toml(project: Project, extra: str = "") -> str:
    return BOTH.replace("@URL@", project.repo.as_posix()) + extra  # type: ignore[attr-defined]


def used(project: Project) -> str:
    return project.state("fakelib", "provider")


def test_auto_tries_python_first(both: Project) -> None:
    both.write(both_toml(both))
    both.configure("-DPOLYBIND_DEPS_PIN=OFF")
    assert used(both) in ("uv", "pip")


def test_prefer_provider_in_the_settings(both: Project) -> None:
    both.write(both_toml(both, 'prefer-provider = "fetch"\n'))
    both.configure("-DPOLYBIND_DEPS_PIN=OFF")
    assert used(both) == "fetch"


def test_prefer_variables_win_over_the_settings(both: Project) -> None:
    both.write(both_toml(both, 'prefer-provider = "fetch"\n'))
    both.configure("-DPOLYBIND_DEPS_PIN=OFF", "-DPOLYBIND_DEPS_PREFER_PROVIDER=python")
    assert used(both) in ("uv", "pip")
    both.configure("-DPOLYBIND_DEPS_PIN=OFF", "-DPOLYBIND_DEPS_PREFER_PROVIDER=python",
                   "-DPOLYBIND_DEPS_PREFER_PROVIDER_FAKELIB=fetch", build=both.root / "b2")
    assert (both.root / "b2" / "polybind-deps" / "fakelib.provider.txt").read_text().strip() == "fetch"


def test_the_provider_in_the_lock_goes_first(both: Project) -> None:
    both.write(both_toml(both))
    commit = git(both.repo, "rev-parse", "v2.1.0^{commit}")  # type: ignore[attr-defined]
    write_lock(both.src / "polybind.lock", Lock({"fakelib": LockEntry("v2.1.0", "fetch", commit)}))
    both.configure()
    assert used(both) == "fetch"
    write_lock(both.src / "polybind.lock", Lock({"fakelib": LockEntry("v2.2.0", "pip")}))
    both.configure(build=both.root / "b2")
    assert (both.root / "b2" / "polybind-deps" / "fakelib.provider.txt").read_text().strip() in ("uv", "pip")


def test_prefer_beats_the_lock(both: Project) -> None:
    both.write(both_toml(both, 'prefer-provider = "fetch"\n'))
    write_lock(both.src / "polybind.lock", Lock({"fakelib": LockEntry("v2.2.0", "pip")}))
    commit = git(both.repo, "rev-parse", "v2.1.0^{commit}")  # type: ignore[attr-defined]
    write_lock(both.src / "polybind.lock", Lock({"fakelib": LockEntry("v2.1.0", "pip")}))
    both.configure("-DPOLYBIND_DEPS_PIN=OFF")
    assert used(both) == "fetch"
    assert both.state("fakelib", "commit") == commit


def test_a_forced_provider_is_the_only_one(both: Project) -> None:
    both.write(both_toml(both))
    both.configure("-DPOLYBIND_DEPS_PIN=OFF", "-DPOLYBIND_DEPS_PROVIDER_FAKELIB=fetch")
    assert used(both) == "fetch"
    both.write('[deps.fakelib]\nversion-spec = ">=1,<3"\npython = {}\n')  # fetch is off
    out = both.configure("-DPOLYBIND_DEPS_PROVIDER=fetch", "-DPOLYBIND_DEPS_PIN=OFF", build=both.root / "b2", check=False)
    assert out.returncode != 0 and "no fetch.url in the settings" in out.stdout


def test_prefer_uv_tries_uv_not_python(both: Project) -> None:
    both.write(both_toml(both, 'prefer-provider = "pip"\n'))
    both.configure("-DPOLYBIND_DEPS_PIN=OFF")
    assert used(both) == "pip"


@pytest.mark.parametrize("variable", ["POLYBIND_DEPS_PROVIDER", "POLYBIND_DEPS_PREFER_PROVIDER"])
def test_an_unknown_provider_is_an_error(both: Project, variable: str) -> None:
    both.write(both_toml(both))
    out = both.configure(f"-D{variable}=conda", check=False)
    assert out.returncode != 0 and "conda" in out.stdout


def test_the_system_provider(project: Project) -> None:
    prefix = project.root / "prefix" / "fakelib"
    prefix.mkdir(parents=True)
    (prefix / "fakelibConfig.cmake").write_text("add_library(fakelib::fakelib INTERFACE IMPORTED)\n")
    (prefix / "fakelibConfigVersion.cmake").write_text(
        'set(PACKAGE_VERSION "2.0.0")\nset(PACKAGE_VERSION_COMPATIBLE TRUE)\n')
    project.write(FAKELIB)
    out = project.configure("-DPOLYBIND_DEPS_PROVIDER=system", f"-DCMAKE_PREFIX_PATH={prefix.parent}")
    assert "deps: fakelib 2.0.0 (system)" in out.stdout
    project.write(FAKELIB.replace("<3", "<2"))
    assert project.configure("-DPOLYBIND_DEPS_PROVIDER=system", f"-DCMAKE_PREFIX_PATH={prefix.parent}",
                             build=project.root / "b2", check=False).returncode != 0


# --------------------------------------------------------------------------- warnings and strict


def test_a_version_that_is_not_the_recorded_one_warns_or_fails(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0")
    project.write(FAKELIB)
    write_lock(project.src / "polybind.lock", Lock({"fakelib": LockEntry("v2.1.0", "pip")}))
    # Offline, so the recorded 2.1.0 cannot be installed and 2.2.0 (in range) is used.
    out = project.configure("-DPOLYBIND_DEPS_OFFLINE=ON", check=False)
    assert out.returncode != 0  # nothing importable offline
    out = project.configure("-DPOLYBIND_DEPS_PIN=OFF", build=project.root / "b2")  # now install, no lock use
    assert "deps: fakelib 2.2.0" in out.stdout
    out = project.configure("-DPOLYBIND_DEPS_PIN=ON", "-DPOLYBIND_DEPS_OFFLINE=ON", build=project.root / "b2", check=False)
    assert "not the tested version" in out.stdout
    strict = project.configure("-DPOLYBIND_DEPS_PIN=ON", "-DPOLYBIND_DEPS_OFFLINE=ON", "-DPOLYBIND_DEPS_STRICT=ON",
                               build=project.root / "b2", check=False)
    assert strict.returncode != 0 and "not the tested version" in strict.stdout


# --------------------------------------------------------------------------- record


def test_record_refuses_when_the_build_was_pinned(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0")
    project.write(FAKELIB)
    project.configure()  # PIN is ON
    out = project.record(check=False)
    assert out.returncode != 0 and "POLYBIND_DEPS_PIN is ON" in out.stdout
    assert not (project.src / "polybind.lock").exists()


def test_record_needs_a_resolved_build(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0")
    project.write(FAKELIB + '[deps.other]\npython = {}\n')  # never resolved by the CMakeLists
    project.configure("-DPOLYBIND_DEPS_PIN=OFF")
    out = project.record(check=False)
    assert out.returncode != 0 and "the build did not resolve other" in out.stdout


def test_record_keeps_what_this_build_did_not_use(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0")
    project.write(FAKELIB + '[deps.maybe]\noptional = true\npython = {}\n',
                  "polybind_resolve_dep(fakelib)\npolybind_resolve_dep(maybe)")
    write_lock(project.src / "polybind.lock", Lock({
        "maybe": LockEntry("v1.0.0", "pip"),
        "gone": LockEntry("v9.9.9", "fetch"),  # not declared any more: kept, validate warns about it
    }))
    project.configure("-DPOLYBIND_DEPS_PIN=OFF")
    project.record()
    entries = project.lock.entries
    assert entries["fakelib"].tag == "v2.2.0"
    assert entries["maybe"] == LockEntry("v1.0.0", "pip")  # optional, left out by this build: unchanged
    assert entries["gone"] == LockEntry("v9.9.9", "fetch")


def test_record_does_not_record_the_system(project: Project) -> None:
    prefix = project.root / "prefix" / "fakelib"
    prefix.mkdir(parents=True)
    (prefix / "fakelibConfig.cmake").write_text("add_library(fakelib::fakelib INTERFACE IMPORTED)\n")
    (prefix / "fakelibConfigVersion.cmake").write_text('set(PACKAGE_VERSION "2.0.0")\nset(PACKAGE_VERSION_COMPATIBLE TRUE)\n')
    project.write(FAKELIB)
    project.configure("-DPOLYBIND_DEPS_PIN=OFF", "-DPOLYBIND_DEPS_PROVIDER=system", f"-DCMAKE_PREFIX_PATH={prefix.parent}")
    out = project.record()
    assert "came from the system" in out.stdout
    assert project.lock.entries == {}


def test_the_lock_is_a_configure_dependency_and_the_roundtrip_is_stable(project: Project) -> None:
    make_wheel(project.wheels, "fakelib", "2.2.0")
    project.write(FAKELIB)
    project.configure("-DPOLYBIND_DEPS_PIN=OFF")
    project.record()
    first = (project.src / "polybind.lock").read_text()
    project.configure("-DPOLYBIND_DEPS_PIN=OFF")
    project.record()
    assert (project.src / "polybind.lock").read_text() == first


# --------------------------------------------------------------------------- errors of the loader


def test_load_reports_bad_settings(project: Project) -> None:
    project.write("[deps.fakelib]\nruntime = true\n")
    out = project.configure(check=False)
    assert out.returncode != 0 and "deps.fakelib.runtime needs the python provider" in out.stdout


def test_resolve_needs_load_and_a_declared_dependency(project: Project) -> None:
    project.write(FAKELIB, "polybind_resolve_dep(unknown)")
    out = project.configure(check=False)
    assert out.returncode != 0 and "'unknown' is not declared" in out.stdout


def test_cmake_dir_command() -> None:
    out = subprocess.run([sys.executable, "-m", "polybind", "cmake-dir"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=False)
    assert out.returncode == 0
    assert (Path(out.stdout.strip()) / "PolybindDeps.cmake").is_file()
