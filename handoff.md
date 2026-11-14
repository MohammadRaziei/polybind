# polybind handoff

For the next chat. Read `spec.md` first (the contract; every rule is tagged done / decided / proposed / open / unverified), then this file.

Repo: `github.com/mohammadraziei/polybind` (cloned in chat, **nothing was pushed**; the project is delivered as a zip without `.git`). Pure Python, stdlib only (+ `tomli` below Python 3.11), `requires-python >= 3.8`, `polybind/cmake/PolybindDeps.cmake` ships inside the wheel.

## Working rules of the owner (keep them)

- Reply in **Persian**; code, commands, file contents in English.
- **Build and run everything through CMake**: `cmake -S . -B /tmp/pb-build && (cd /tmp/pb-build && ctest --output-on-failure)` runs ruff on the whole project and pytest. Do not call pytest/ruff directly in the workflow (calling them while iterating is fine, the final run is ctest).
- **After every step: zip the whole project without `.git` and send it**, without being asked. No diff/patch files.
- Run ruff so there are no lint errors (ruff target is `py38`, `tests/data` is excluded on purpose: fixtures of the stub parser).
- Numbers and results must be measured; say plainly what was not run. Every new test should be mutation-checked (break the code on purpose, see a test fail). Do not reopen decisions listed in `spec.md`.
- Design first, code after: the owner discusses a design, then says "continue".

## What exists (all tests pass: 257 through ctest, ruff clean)

| piece | files | tests |
|---|---|---|
| `wrap`: `polybind wrap INPUT.pyi` (was `gen`) | `polybind/core.py`, `wrap.py` | `tests/test_core.py`, `test_cli.py` |
| the command line: one root parser, `-h` everywhere, exit codes, `PolybindError` | `polybind/cli.py`, `errors.py`, `__main__.py` | `tests/test_cli.py` (`TestCommandLine`) |
| `polybind version ...` (version in a header or pyproject, git tags, `check`) | `polybind/version.py` | `tests/test_version.py` |
| settings: `polybind.toml` + `[tool.polybind]`, `schema = 1`, dashes in keys, unknown-key hints | `polybind/config.py` | `tests/test_config.py` |
| `[deps.<name>]`, `[optional-deps.<group>.<name>]`: model, defaults, validation, requirements, `deps validate|list|requirements|export|record|install` | `polybind/deps.py` | `tests/test_deps.py` |
| `polybind.lock` (tag, provider, optional commit), reader/writer | `polybind/lock.py` | `tests/test_lock.py` |
| `deps install`: pyproject (build, runtime, extras, PEP 735 groups) + `[deps]` into an environment | `polybind/install.py` | `tests/test_install.py` |
| CMake resolver: providers uv, pip, python (= uv else pip), fetch, system; pin; record target | `polybind/cmake/PolybindDeps.cmake` (ported from httpp's `Deps.cmake`) | `tests/test_resolver.py` (real CMake, pip, uv, git) |
| CMake test wiring | `CMakeLists.txt` | ruff + pytest |

Verified in this chat: a real wheel built with `pip wheel .` contains `polybind/cmake/PolybindDeps.cmake` and `polybind cmake-dir` finds it when installed; `polybind version` works unchanged on a libspeech checkout (header detected, bump/tag dry-runs right); the design files `polybind.toml`/`polybind.lock` pass `deps validate`.

## Decisions worth remembering (details in spec.md)

- Scope: hybrid C++/Python projects (httpp, mbedtls, liburlparser, libspeech); pure-Python deps stay in `pyproject.toml`, **no `[python-deps]` section** (read-only by `deps install`).
- Provider is ON only if its table exists (`python.*` / `fetch.url`); `prefer-provider` only reorders `auto` and warns when its table is missing; `POLYBIND_DEPS_PROVIDER` forces one (values `auto|python|uv|pip|fetch|system`); no `INSTALLER` option.
- Lock: separate `polybind.lock`, per dependency `tag` (may be a commit id), `provider` (`fetch|uv|pip`), optional `commit` (fetch only; moved-tag check only when present). Written only by `deps record`, which refuses after a pinned build.
- `runtime` (default false) / `buildtime` (default true) / `bundle` (default false). `bundle` = ship the dependency's shared library inside our wheel (not designed yet); libspeech keeps httpp external (`bundle = false`, `runtime = true`).
- `httpp` has no `python -m httpp --cmake-dir`; it is `python -m httpp py --cmake-dir`, so `python.cmake-flag` accepts a list.
- `python.version-flag` became `python.version-command` (string or list; default `python -m <module> --version`, `""` = package metadata, first word other than `python` = console script found in the install's `bin`/`Scripts`, then PATH). A command that fails or prints nothing falls back to the metadata. Console scripts need `PYTHONPATH=<target>` to run (installed with `--target`), which the resolver sets. Only run on Linux/uv so far.
- M4 done: `polybind/metadata.py` is a scikit-build-core dynamic-metadata provider (entry point `dynamic_metadata.provider` = `polybind.metadata`, used with `[[tool.dynamic-metadata]] provider = "polybind.metadata"`), no backend of ours. Measured with pip/`python -m build`: build requirements land in the isolated env, runtime ones in `Requires-Dist`, resolver finds them offline, sdist works with `sdist.include`. **Release needs a new version**: PyPI already has polybind 0.1.0 without the provider and pip prefers it over a local 0.1.0 (cost me one failed run). Version is now **0.2.0** (set with `polybind version minor +1`), `CHANGELOG.md` is written, dated 2026-11-12; no git tag created, nothing pushed or published. Linux and scikit-build-core 1.1.2 only.
- Dynamic version (spec 4.1): `polybind.version.project_source/project_version` feed the scikit-build-core provider (`version` field) and a hatch version source (`polybind/hatch_plugin.py`, entry point group `hatch`). Pyproject cannot be the source of a dynamic version. `polybind version` itself still works without settings (owner is fine with that). Measured with real isolated builds on Linux, both backends.
- CI (GitHub Actions, Python 3.11, pip only, no uv) caught a test-fixture bug: wheels built by `make_wheel` must record `0o100755` for `.data/scripts/*`, else `pip --target` installs the script without the executable bit (uv does not care) and the console-script test silently fell back to the metadata. To see CI's situation locally, run ctest with `uv` off PATH.
- Key bug found and fixed: a `;` inside a `python -c` argument is split by CMake (list). Snippets use `\n`.

## What is NOT done

1. M4 is done (see the decisions above). Left over: Windows/macOS, scikit-build-core older than 1.1.2 (minimum unknown), no build-time group selection (section 6 option a), and 0.2.0 is not tagged or published yet.
2. **M5 `bundle`**: `install(IMPORTED_RUNTIME_ARTIFACTS ...)` (CMake 3.21), licenses, RPATH `$ORIGIN` / `@loader_path` / Windows DLL directory, interplay with auditwheel/delocate/delvewheel (libspeech disables repair on Windows because delvewheel failed). Unverified.
3. **M6 `[[wrap.stubs]]`** for `wrap` (input, output, module-name).
4. `polybind build`: undecided what it adds to `python -m build`; nothing written.
5. Open questions (spec 14): group build requirements (a) selected at build time vs (b) never; lock format TOML vs JSON (TOML is implemented); `targets` default `<name>::<name>` (my guess); `runtime = true` requires the python provider (my rule); locked provider that cannot deliver: warning (implemented) vs error.
6. Not run anywhere but **Linux, Python 3.12, CMake 4.4, uv 0.11, pip 26**: Windows, macOS, Python 3.8-3.11. A CI matrix is needed. `requires-python >= 3.8` is a claim, not a measurement.
7. `POLYBIND_DEPS_PIN` defaults to ON (as in httpp): a fetch-only dependency in a project without a lock fails to configure until one build with `-DPOLYBIND_DEPS_PIN=OFF` and `polybind_deps_record`. Not yet explained in README.
8. httpp and libspeech are **not** migrated. The plan for libspeech (spec/this chat): delete its copy of `version.py`, `[version]` in `polybind.toml`, `[deps.httpp]` (`version-spec = ">=0.7,<0.8"`, `runtime = true`, `python.cmake-flag = ["py", "--cmake-dir"]`), `polybind_resolve_dep(httpp)` instead of the ~20 lines in its CMakeLists, drop the duplicated spec in `[build-system].requires` / `[project].dependencies`. Needs M4 for the requirements part.

## How to continue

1. `unzip polybind.zip && cd polybind`, then the ctest command above must be green before anything else.
2. Next: M5 `bundle` (item 2), or the CI matrix (item 6); ask the owner.
3. Keep `spec.md` and the code in step: when one changes, change the other in the same step.
