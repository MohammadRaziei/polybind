# Changelog

All notable changes to polybind. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
version numbers follow [Semantic Versioning](https://semver.org/) (while the major version is 0, a minor release may change
the settings files).

## [Unreleased]

### Changed (breaking)

- **`polybind gen` is now `polybind wrap`.** `gen` did not say what it makes: the input is a `.pyi` stub, the output is one
  Python class that stands for the C++ template variants (`Box__int32`, `Box__float64` become `Box`). The module is
  `polybind/wrap.py` (was `gen.py`) and the settings section is `[wrap]` (was `[gen]`, reserved, no keys yet). To migrate,
  write `polybind wrap INPUT.pyi` where you wrote `polybind gen INPUT.pyi`. `polybind INPUT.pyi` still gets the hint, now
  pointing at `wrap`. The Python API (`PolybindGenerator`) is unchanged.
- The one-line description in `polybind -h` and in `pyproject.toml` no longer says "wrappers for nanobind, pybind11 and
  Cython" first: polybind is a helper for hybrid C++/Python projects (version, native dependencies) and `wrap` is one of its
  commands. `polybind -h` and the README now list `version`, `deps`, `config`, `cmake-dir` first and `wrap` last.

## [0.3.0] - 2026-11-13

The command line is reorganised into real sub-commands and the package gets a working Python API. Both change how polybind
is called, hence the minor release (while the major version is 0, a minor release may break). To migrate, write
`polybind gen INPUT.pyi` where you wrote `polybind INPUT.pyi`; the dependency and settings files are unchanged.

### Changed (breaking)

- **The command line is one parser with real sub-commands.** `polybind INPUT.pyi` is now `polybind gen INPUT.pyi`; the old
  form is an error that says what to type. `polybind -h` lists `gen`, `version`, `deps`, `config` and `cmake-dir` (it showed
  only `gen` before), and every command and sub-command has its own `-h`. `polybind` alone prints the help.
- Problems the user can fix are one exception, `PolybindError`, printed as `polybind: error: ...` with exit 2. `gen` used
  to exit 1 for a missing file; it exits 2 now. A mistyped command is suggested (`did you mean 'version'?`).
- The tools no longer have their own `main()` / `python -m polybind.version`; each registers with `add_parser`.

### Fixed

- **`import polybind` had no API.** The package's `__init__.py` was empty; the one with `PolybindGenerator` and
  `__version__` sat in the repository root, outside the wheel (and made the root look like a package, so `python -m polybind`
  failed from the parent directory). The API is now in `polybind/__init__.py`, imported on first use so that
  `import polybind` stays cheap. The root `__init__.py` and the unused `requirements.txt` are gone; the test tools are the
  `test` extra of `pyproject.toml`, and CI runs `ctest` like a local checkout does.
- **`python.version-command` may print more than the number.** `python -m httpp --version` prints `httpp 0.8.1`, which the
  resolver took as the version and refused (`>=0.8,<0.9` not satisfied). The version is now the first plain number of the
  first line.

### Notes

- Checked on Linux with Python 3.12 and CMake 3.21, 3.28, 3.31 and 4.4 (the resolver runs on all of them). Windows, macOS and
  Python 3.8 to 3.11 have not been run locally; CI runs Python 3.11 on Linux.
- `bundle` and `[[gen.stubs]]` are still not done (see 0.2.0).

## [0.2.0] - 2026-11-12

polybind grows from a stub generator into a helper for hybrid C++/Python projects: one version, the native
dependencies, and the requirements of a project in one place. The design is in `spec.md`.

### Added

- **`polybind version`** (`major|minor|patch [VALUE]`, `tag`, `tag set`, `tag create`, `check`): the version comes from a
  `*VERSION_MAJOR/MINOR/PATCH` header or from a static `version` in `pyproject.toml`. Writing changes only the number.
  `check` exits 1 when the version is not the latest tag.
- **Settings files**: `polybind.toml` and `[tool.polybind]` in `pyproject.toml` (the first wins, key by key), with a
  `schema` key, dashes in key names, and errors that suggest the key you meant. `polybind config` shows the effective
  settings and where each came from.
- **Native dependencies**, `[deps.<name>]` and `[optional-deps.<group>.<name>]`: `version-spec`, `targets`, `find-package`,
  `optional`, `prefer-provider`, `runtime` / `buildtime`, `bundle` (recorded, not implemented yet), and the provider tables
  `python.*` and `fetch.*`.
- **`polybind.lock`**: the pinned tag, provider and commit per dependency, written only by `polybind deps record`.
- **CMake resolver** (`PolybindDeps.cmake`, found with `polybind cmake-dir`): `polybind_deps_load()`,
  `polybind_resolve_dep(<name>)` and the `polybind_deps_record` target. Providers: `python` (installed with uv or pip),
  `fetch` (git, newest tag in range, moved-tag check by commit) and `system`. Options: `POLYBIND_DEPS_PROVIDER`,
  `POLYBIND_DEPS_PREFER_PROVIDER`, `POLYBIND_DEPS_PYTHON`, `POLYBIND_DEPS_PIN`, `POLYBIND_DEPS_STRICT`,
  `POLYBIND_DEPS_OFFLINE`. After a resolve, `POLYBIND_DEP_<N>_FOUND`, `_PROVIDER` and `_VERSION` are set.
- **`polybind deps`**: `validate`, `list`, `requirements`, `install`, `export` and `record`.
- **`python.version-command`**: how the version of a Python dependency is found. By default `python -m <module>
  --version`; a string or a list; `""` reads the package metadata; a first word other than `python` runs a console script
  of the package. A command that fails falls back to the package metadata.
- **Build plugin for scikit-build-core**: a dynamic-metadata provider, so that with `polybind` in
  `[build-system].requires` and `[[tool.dynamic-metadata]] provider = "polybind.metadata"`, the build requirements of
  `polybind.toml` are installed into the isolated build environment and the runtime ones (and extras) go into the wheel
  metadata. See `spec.md` 7.2 and the README.
- **Dynamic version**: with `dynamic = ["version"]` the version is read from the header, by the scikit-build-core provider
  and by a hatch version source (`[tool.hatch.version] source = "polybind"`, `hatch version X.Y.Z` writes the header). A
  `pyproject.toml` cannot be the source of a dynamic version.
- Tests that run the real CMake, pip, uv and git, and a real scikit-build-core build (`tests/test_resolver.py`,
  `tests/test_metadata.py`).

### Changed

- `.polybind` is replaced by `polybind.toml`.
- The tests no longer hard-code the repository's own version.

### Notes

- Checked on Linux with Python 3.12, CMake 4.4, uv 0.11, pip 26 and scikit-build-core 1.1.2 only. Windows, macOS, Python
  3.8 to 3.11 and older scikit-build-core releases have not been run, so `requires-python >= 3.8` is still a claim, not
  a measurement.
- The build plugin takes no settings: groups cannot be selected at build time.
- `bundle` (shipping a dependency's shared library inside the wheel) and `[[gen.stubs]]` are not done.
- 0.1.0 on PyPI has no build plugin: use 0.2.0 or later in `[build-system].requires`.

[0.3.0]: https://github.com/mohammadraziei/polybind/releases/tag/v0.3.0
[0.2.0]: https://github.com/mohammadraziei/polybind/releases/tag/v0.2.0
