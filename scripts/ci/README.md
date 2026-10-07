# Native validation runner

`.github/workflows/native-tests.yml` defines exactly three native GitHub-hosted
jobs: `ubuntu-latest`, `macos-latest`, and `windows-latest`, all on Python 3.14.
Each runs the complete core suite and the real Hermes integration suite. This
workflow is test infrastructure; its existence is not evidence that any job ran.
It is manually triggered (`workflow_dispatch`) so that publishing this file does
not itself consume Actions minutes before execution and billing are authorized.

The public Hermes checkout is pinned to
`0dbaf33f67acf1f6d8e8e6c6efe8042ef8db98c4`. Preflight verifies that exact Git
revision, rejects tracked modifications, checks the native OS, and requires
Python 3.14. CI installs Hermes in editable source mode with its base dependencies
from that checkout, without optional extras. Hermes' upstream setup guard does
not support ordinary wheel installation. The Ledger uses PyYAML for explicitly approved local skill frontmatter. Its
profile copier remains standard-library-only; Hermes dependencies are test-host
dependencies.

To run the same validation locally with dependencies already installed:

```sh
python scripts/ci/run_native_tests.py --hermes-source /path/to/hermes-agent
```

No shell-specific environment assignment, activation, or Unix command is needed.
The harness sets `HERMES_SOURCE_DIR` and `HERMES_INTEGRATION_REQUIRED` itself.
Actual Hermes imports run in isolated child processes with temporary OS homes,
profile homes, XDG/AppData locations, and temporary directories. `-X utf8` is
explicit because isolated Python ignores `PYTHON*` environment settings. Loader,
registry, model-tool dispatch, operator CLI, and skill serving are real. Only
GitHub HTTP responses are synthetic; no OS or host API is mocked.

The runner rejects failures, collection errors, skipped cases (including xfail),
missing required integration cases, and zero core cases. It never turns a missing
Hermes checkout into a green integration result. Running ordinary pytest without
`HERMES_SOURCE_DIR` still visibly skips opt-in Hermes tests for local core-only
work; that is not host compatibility evidence.

Each job retains a 14-day artifact containing:

- `runtime.json`: actual native OS, release and architecture, Python and SQLite
  versions, installed package versions, source revision, status, and exact test
  counts
- `junit.xml`: per-case outcome evidence after pytest ran
- `pytest.log`: complete synthetic test output after pytest started

If installation fails, the preflight `runtime.json` says `not_run` and has no
counts. The job remains failed. Missing reports are an artifact-upload error,
not a passing test claim. Each OS has its own artifact; Linux evidence says
nothing about macOS or Windows. Local runs using a smaller host-API dependency
set likewise do not validate CI's full base-dependency installation.

The workflow requests only `contents: read`, does not persist Git credentials,
does not receive application secrets, and has no deploy, publish, or mutation
step. It uses standard GitHub-hosted runner labels. Account quota and billing
must be checked before someone authorizes execution; the workflow does not
configure paid runners or change billing settings.

Official action release references, verified 2026-10-06:

- [checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1),
  pinned to `3d3c42e5aac5ba805825da76410c181273ba90b1`
- [setup-python v7.0.0](https://github.com/actions/setup-python/releases/tag/v7.0.0),
  pinned to `5fda3b95a4ea91299a34e894583c3862153e4b97`
- [upload-artifact v7.0.1](https://github.com/actions/upload-artifact/releases/tag/v7.0.1),
  pinned to `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a`
