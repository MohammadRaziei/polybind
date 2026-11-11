# polybind specification

Status: draft 1. This document is the contract the code is written against. When code and spec disagree, one of them is a bug and the disagreement is fixed on the spot, in the spec or in the code.

Every rule carries a tag:

| tag | meaning |
|---|---|
| **[done]** | implemented and covered by tests |
| **[decided]** | decided by the project owner, not implemented yet |
| **[proposed]** | proposed while writing this spec, waiting for the owner's yes or no |
| **[open]** | not decided; the options are listed |
| **[unverified]** | rests on documentation or reading, not on a run; must be tested before the code depends on it |

Nothing in this spec has been run on Windows, macOS, or on Python older than 3.12.

## 1. Purpose and scope

polybind is a small toolbox for **hybrid C++/Python projects** (httpp, mbedtls, liburlparser, libspeech): C++ libraries that ship Python wheels and depend on each other. It sits **on top of** nanobind, pybind11, Cython, cython-cmake and scikit-build-core. It does not replace or wrap any of them.

Tools, each independent of the others and each reading the same settings:

| tool | purpose | state |
|---|---|---|
| `gen` | wrapper generation from `.pyi` stubs | exists (`polybind INPUT.pyi`) |
| `version` | one source of truth for the project version, git tags | **[done]** |
| settings | `polybind.toml` / `[tool.polybind]` | **[done]**, being revised (section 3) |
| `deps` | native dependency declaration, resolution, lock | **[decided]**, this spec |
| build plugin | puts the declared dependencies into the build and runtime requirements | **[proposed]** + **[unverified]** |
| `bundle` | ships a dependency's shared library inside our wheel | **[decided]** in principle |

Non-goals:

- Pure-Python dependencies (librosa, numpy, pytest, ...). They belong in `pyproject.toml`. A dependency in polybind is a native library that has a CMake package. **[decided]**
- Low-level build helpers (`find_package` modules for nanobind, pybind11 or Cython). **[decided]**
- Replacing auditwheel, delocate or delvewheel.

## 2. Principles

1. **Python's standard library only**, plus `tomli` on Python older than 3.11. No other runtime dependency. **[decided]**
2. **Tools do not import each other.** They share only the settings loader.
3. **A project that has no polybind settings never needs a TOML reader.** `polybind version` works there as before.
4. **Every option has a default.** A dependency written as `[deps.liburlparser]` alone is valid. **[decided]**
5. **Every file has one writer.** Humans write `polybind.toml`; only `polybind deps record` writes `polybind.lock`.
6. **Errors name the file and the key** and suggest the closest valid key. Warnings are for things that can still work.
7. Python: `requires-python >= 3.8`; on Python < 3.11 `tomli` is installed automatically. **[decided]**

## 3. Files and precedence

| file | written by | content |
|---|---|---|
| `polybind.toml` | a human | settings and dependencies; keys at top level |
| `[tool.polybind]` in `pyproject.toml` | a human | the same keys, one level down |
| `polybind.lock` | `deps record` only | the pinned tag and provider per dependency; a separate file, never inside `pyproject.toml` **[decided]** |

Precedence, later wins, merged key by key: `[tool.polybind]` < `polybind.toml` < command line.

`.polybind` (no extension) is **replaced** by `polybind.toml` (like `ruff.toml`); nothing was published under the old name. **[decided]**

Key names use dashes (`tag-format`, `version-spec`), like pyproject and ruff. **[decided]**

Top level keys: `schema` (integer, must be `1` when present), and the sections `version`, `gen`, `deps`, `optional-deps`. An unknown section or key is an error with a suggestion. **[done]** for the mechanism.

sdist: `pyproject.toml` is always in an sdist; `polybind.toml` and `polybind.lock` must be listed (scikit-build-core: `sdist.include`), otherwise a build from the sdist loses its requirements. Putting everything in `[tool.polybind]` avoids this, except for `polybind.lock`.

## 4. `[version]` and `polybind version` **[done]**

Source of truth, one of:

- a header with `#define <PREFIX>VERSION_MAJOR`, `..._MINOR`, `..._PATCH` (`(N)` accepted), or
- the static `version = "X.Y.Z"` of `[project]` in a `pyproject.toml`.

Keys: `file`, `prefix` (text before `VERSION_MAJOR`; default detected), `tag-format` (default `v%s`).

Detection without settings: one header under `include/` or the root that defines the macros; several is an error; else a static `pyproject.toml` version.

Commands: `polybind version [major|minor|patch [VALUE]]`, `tag`, `tag set`, `tag create [major|minor|patch|X.Y.Z]`, `check`. `VALUE` is a number or an offset (`+`, `-`, `+N`, `-N`, never below 0). Options: `--root`, `--file`, `--prefix`, `--tag-format`, `--dry-run`.

Rules: writing changes only the number, never comments or other tables; `tag create` refuses an existing tag and commits only the version file; `check` exits 1 when the version is not the latest tag; exit code 2 for a user error.

## 5. Dependencies: `[deps.<name>]` **[decided]** unless tagged

A dependency is a native library with a CMake package, usually also a pip package. `<name>` matches `[A-Za-z0-9][A-Za-z0-9._-]*`.

### 5.1 Keys and defaults

| key | type | default | meaning |
|---|---|---|---|
| `description` | string | `""` | free text |
| `version-spec` | string | `""` (any) | PEP 440 range, checked against what the provider found |
| `targets` | list of strings | `["<name>::<name>"]` **[proposed]** | imported targets that must exist after resolving |
| `find-package` | string | the name | argument of `find_package()` |
| `optional` | bool | `false` | may be missing: no error, `POLYBIND_DEP_<NAME>_FOUND` is false, the feature is left out |
| `prefer-provider` | string | `""` | order of `auto` (5.3) |
| `runtime` | bool | `false` | also exported to `[project].dependencies` |
| `buildtime` | bool | `true` | added to the build requirements |
| `bundle` | bool | `false` | section 8 |
| `python.*` | table | absent = provider off | 5.4 |
| `fetch.*` | table | absent = provider off | 5.5 |

Validation (errors unless said otherwise):

1. unknown key, wrong type;
2. `runtime = false` and `buildtime = false` together;
3. `version-spec` must be a comma separated list of `~=`, `==`, `!=`, `<=`, `>=`, `<`, `>` followed by a version;
4. `prefer-provider` is one of `python`, `uv`, `pip`, `fetch`, `system`, or empty;
5. `runtime = true` needs the python provider on, because only a pip package can be a Python requirement **[proposed]**;
6. `bundle = true` together with `runtime = true`: warning, unless the Python import is really needed.

### 5.2 Providers

| provider | what it does |
|---|---|
| `uv` | installs the package with uv into the build tree, never into the user's site-packages |
| `pip` | the same with pip |
| `python` | uv when it is found, else pip |
| `fetch` | `git clone --depth 1` of one tag with FetchContent: no Python, no submodule, no history |
| `system` | `find_package()` on the normal search path; never part of `auto` |

`POLYBIND_DEPS_PYTHON_INSTALLER` does not exist: the installer is part of the provider name. Its old value `none` ("use only what is already importable") is `POLYBIND_DEPS_OFFLINE`.

A provider is **on only when its table exists**: any `python.*` key (or `python = {}`) turns on `python`/`uv`/`pip`; `fetch.url` turns on `fetch`. A dependency with neither can only be found by `system` when it is forced.

### 5.3 Selection

1. `POLYBIND_DEPS_PROVIDER_<NAME>` or `POLYBIND_DEPS_PROVIDER` (not `auto`) forces one provider and nothing else is tried; failing is an error.
2. With `auto`, the order is: `prefer-provider` (own key, then `POLYBIND_DEPS_PREFER_PROVIDER[_<NAME>]`), then the provider recorded in `polybind.lock` **[proposed]**, then `python`, then `fetch`.
3. `prefer-provider = "python"` when `[deps.X.python]` is absent: a warning, and the order goes on without it.
4. A provider that is off is skipped silently, except in step 3.

### 5.4 python provider

| key | default | meaning |
|---|---|---|
| `python.package` | the name | pip name |
| `python.module` | package with `-` replaced by `_` | module name |
| `python.module-cli` | `true` | the module answers `python -m <module> <cmake-flag>` |
| `python.cmake-flag` | `"--cmake-dir"` | string or list of arguments; a list for `["py", "--cmake-dir"]` |
| `python.version-flag` | `"--version"` | `""` reads the version from the package metadata |
| `python.cmake-function` | `"get_cmake_dir"` | only with `module-cli = false` |

`module-cli = false`: setting `cmake-flag` is an error as well; the CMake dir is `python -c "import <module>; print(<module>.<cmake-function>())"` and the version comes from the package metadata, so setting `version-flag` there is an error. Known conventions: httpp and pybind11 use `get_cmake_dir`, nanobind uses `cmake_dir`.

Verified in httpp's source: `python -m httpp --cmake-dir` does not exist; the CMake dir is printed by `python -m httpp py --cmake-dir`. libspeech reads it with `httpp.get_cmake_dir()` today.

### 5.5 fetch provider

| key | default | meaning |
|---|---|---|
| `fetch.url` | none | git url; this is what turns the provider on |
| `fetch.tag-format` | `"v%s"` | tag name for a version; must contain `%s` |
| `fetch.build-options` | `{}` | CMake options of the fetched project; string values |

### 5.6 Version check

After a provider delivers, the version it reports is checked against `version-spec`. A mismatch is a warning and an error with `POLYBIND_DEPS_STRICT`. The exact comparison semantics are taken from httpp's `Deps.cmake` when the resolver is written. **[unverified]**

## 6. Optional groups: `[optional-deps.<group>.<name>]` **[decided]**

Same keys and same rules as `[deps.<name>]`. The group is a pip extra (`pip install pkg[group]`): an entry with `runtime = true` lands in `[project.optional-dependencies].<group>`.

Not the same as `optional = true`: that is a CMake-level "may be absent", an extra is pip metadata.

**[open]** when the `buildtime` requirements of a group are added:

- (a) only when the group is selected at build time (`--config-settings polybind.groups=test` or `POLYBIND_GROUPS=test`); this is what the code assumes until told otherwise;
- (b) never: `buildtime` is rejected inside a group.

## 7. Requirements and the build plugin

### 7.1 Requirements **[decided]**, formatting **[proposed]**

`polybind deps requirements --kind build|runtime|extra:<group>` prints one PEP 508 requirement per line: `<python.package><version-spec>`, for every dependency with the python provider on and the matching flag set. A dependency without the python provider produces none.

### 7.2 Plugin **[proposed]**, **[unverified]**

Goal: write `polybind` in `[build-system].requires` and nothing else, as `hatch-requirements-txt` does for requirements files:

```toml
[build-system]
requires = ["scikit-build-core", "nanobind", "polybind"]
```

Mechanism under study: a dynamic-metadata provider for scikit-build-core that fills `dependencies` (and `optional-dependencies`) from 7.1 and announces the build requirements through `get_requires_for_dynamic_metadata`. The scikit-build-core documentation calls this interface provisional, and it is not known whether the returned list reaches the isolated build environment. **An experiment decides** (milestone M4); fallbacks are `[tool.scikit-build] build.requires` (static) or a thin PEP 517 backend wrapping `scikit_build_core.build`.

polybind never runs `pip install` itself during the build: an isolated build environment may not contain pip.

## 8. `bundle` **[decided]** in principle

`bundle = true`: at CMake install time the dependency's runtime files (`.so`, `.dylib`, `.dll`) and its license files are installed next to our package inside our wheel.

Mechanism **[unverified]**: `install(IMPORTED_RUNTIME_ARTIFACTS <targets> DESTINATION <package>)`, CMake 3.21 or newer.

Constraints:

- Two wheels that each bundle the same library load it twice into one process (duplicated state and symbols). Default is `false`; libspeech keeps httpp external.
- Redistributing a binary means shipping its license.
- Finding the library at runtime needs `$ORIGIN` (Linux), `@loader_path` (macOS), `os.add_dll_directory` (Windows). Not designed yet.
- Overlaps with auditwheel, delocate and delvewheel; they must not conflict. Not designed yet.

## 9. `polybind.lock` **[decided]**

TOML, separate file at the project root, written only by `polybind deps record`, committed:

```toml
schema = 1

[deps.liburlparser]
tag      = "v2.2.0"
provider = "fetch"
commit   = "1cc8355a90720d282e5b66fdb06063396d9194f4"
```

| key | rule |
|---|---|
| `tag` | required; the tag that passed the tests; may be a commit id; no version, date or tested-by |
| `provider` | required; `fetch`, `uv` or `pip`, whichever delivered it; `system` is never recorded |
| `commit` | optional; only with `fetch`; when present, a tag that now points elsewhere is an error (`POLYBIND_DEPS_PIN`), when absent the check is skipped |

Rules:

- `uv` and `pip` count as one when the lock is read.
- With `uv`/`pip`, the version is the tag with `fetch.tag-format` stripped (`v2.2.0` becomes `2.2.0`). A tag that is a commit id cannot name a pip version, so it works with `fetch` only.
- A lock entry whose dependency is unknown is a warning (stale); a `commit` with `uv`/`pip` is an error.
- Whether a provider mismatch is a warning or an error: warning **[proposed]**.
- Whether the file is TOML or JSON: TOML **[open]**.

## 10. CMake options (per machine, not in the files)

`POLYBIND_DEPS_PROVIDER` (`auto|python|uv|pip|fetch|system`), `POLYBIND_DEPS_PROVIDER_<NAME>`, `POLYBIND_DEPS_PREFER_PROVIDER[_<NAME>]`, `POLYBIND_DEPS_PYTHON` (interpreter), `POLYBIND_DEPS_PIN` (ON), `POLYBIND_DEPS_STRICT` (OFF), `POLYBIND_DEPS_OFFLINE` (OFF), `POLYBIND_DEPS_SKIP_<NAME>`. They are httpp's `HTTPP_DEPS_*` options with the prefix changed. **[decided]**

How a project obtains the resolver (`PolybindDeps.cmake`) **[open]**: (a) `include("<output of python -m polybind cmake-dir>/PolybindDeps.cmake")`, since polybind is a build requirement anyway; (b) copy the file into the project. (a) first.

## 11. Command line

| command | state |
|---|---|
| `polybind INPUT.pyi [options]` | exists |
| `polybind --version` | exists |
| `polybind version ...` | **[done]** |
| `polybind config` | **[done]**: effective settings and their sources |
| `polybind deps validate` | M2: settings, dependencies and lock |
| `polybind deps list` | M2: the dependencies with every default filled in |
| `polybind deps requirements --kind K` | M2 |
| `polybind deps record` | M3 |
| `polybind cmake-dir` | M3 |

## 12. Repository and tests

- `polybind/config.py` settings, `polybind/version.py`, `polybind/deps.py` model, validation, requirements, `polybind/lock.py`, `cmake/PolybindDeps.cmake` (M3).
- Everything is built and run through CMake: `cmake -S . -B build && ctest` runs ruff and pytest. No direct pytest or ruff calls in the workflow.
- Every rule above that says "error" has a test that triggers it.

## 13. Milestones

| | content |
|---|---|
| M1 | version and settings **[done]** |
| M2 | settings revision (`polybind.toml`, dashes, `schema`), dependency model and validation, lock reading and writing, `deps validate|list|requirements` |
| M3 | CMake resolver with the python and fetch providers, `deps record`, `cmake-dir`; tests with local fake packages and local git repositories |
| M4 | experiment, then the build plugin |
| M5 | `bundle` |
| M6 | `[[gen.stubs]]` for `gen` |

## 14. Open questions

1. Group build requirements: (a) or (b), section 6.
2. Lock format: TOML or JSON, section 9.
3. Resolver distribution: (a) or (b), section 10.
4. `targets` default `<name>::<name>`, section 5.1.
5. `runtime = true` requires the python provider, section 5.1 rule 5.
6. A locked provider that cannot deliver: warning or error, section 9.
7. Does the plugin mechanism of section 7.2 work at all.
