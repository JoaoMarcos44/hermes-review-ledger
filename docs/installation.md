# Install and update Ledger V1

## Identity and requirements

Ledger V1 uses package, CLI and plugin version **1.0.2**. The earlier 0.x
identifiers were private development builds, not public releases. Historical
reports and stored receipts retain their original versions. SQLite schema **5**,
lineage `adaptive-v15`, protocol **3**, and critic prompt **1** stay independent.
V1 does not imply production certification: the critic remains experimental,
disabled by default and fail-closed in the pinned Hermes adapter.

Use Python 3.12–3.14 for this package. The pinned Hermes integration runtime
requires Python 3.14. Install the package in a dedicated virtual environment;
Hermes must also have the declared runtime dependency `PyYAML>=6.0.2,<7` in its
own runtime if it is a different environment. The installer copies plugin code,
not Python dependencies. It never upgrades Hermes or installs anything at import.

Nothing is published to PyPI or npm. Use an owner-supplied, verified wheel or
an exact reviewed commit from the private repository. Do not install an unrelated
registry package with the same name. There is no npm wrapper, automatic update
service, registry release, or activation during pip installation.

## 1. Install the command

Linux/macOS (choose an available supported interpreter):

```sh
python3.14 -m venv .venv-ledger
.venv-ledger/bin/python -m pip install /absolute/path/hermes_review_ledger-1.0.2-py3-none-any.whl
.venv-ledger/bin/python -m pip check
.venv-ledger/bin/python -m review_ledger --version
```

Windows PowerShell:

```powershell
py -3.14 -m venv .venv-ledger
.\.venv-ledger\Scripts\python.exe -m pip install C:\Downloads\hermes_review_ledger-1.0.2-py3-none-any.whl
.\.venv-ledger\Scripts\python.exe -m pip check
.\.venv-ledger\Scripts\python.exe -m review_ledger --version
```

The final command should print `1.0.2`. Alternatively, with Git and your existing
SSH authentication already configured, use the same environment's Python:

```sh
python -m pip install "git+ssh://git@github.com/JoaoMarcos44/hermes-review-ledger.git@COMMIT_SHA"
```

Replace `COMMIT_SHA` with the reviewed full 40-character commit. An already
configured authenticated HTTPS Git setup is also supported. Never embed tokens
or passwords in URLs, terminal commands, reports, or source. This operation may
fetch build dependencies and PyYAML; it does not choose a Hermes profile.

For offline installation, prepare a wheelhouse on a connected machine for the
target Python/OS/architecture (or use a trusted dependency wheel supplied for it):

```sh
python -m pip download --only-binary=:all: --dest wheelhouse "PyYAML>=6.0.2,<7"
python -m pip install --no-index --find-links /absolute/path/wheelhouse /absolute/path/hermes_review_ledger-1.0.2-py3-none-any.whl
python -m pip check
```

The second command is run with the destination virtual environment's Python.
Do not use `--no-deps` for a complete fresh installation unless the required
runtime dependency is already installed and verified. Package smoke tests use
`--no-deps` deliberately to prove the installer itself works offline; this does
not certify dependency completeness for every runtime feature.

## 2. Install into an existing profile

Stop sessions for the selected Hermes profile. Use its absolute directory,
containing its existing `config.yaml`; spaces and non-ASCII names are supported.
In examples below, `python` means the dedicated virtual environment's Python.

```sh
python -m review_ledger install --profile-dir "/absolute/path/to/profile"
python -m review_ledger status --profile-dir "/absolute/path/to/profile"
```

The equivalent console command is `hermes-review-ledger`. This step installs the
complete bundled native plugin and records hashes of its owned files. It does
not change configuration, credentials, permissions, repository allowlists,
activation, or other profiles. Follow the README's [activation and configuration
steps](../README.md#enable-and-configure-the-selected-profile) explicitly. The
complete operator CLI requires the selected profile's existing `in_process`
isolation mode; do not silently change security policy to make it work.

## 3. Update the package, then the profile code

These are separate operations. Updating pip's package alone does not update a
copy already installed in a Hermes profile. Before either operation:

1. Stop the profile's sessions and retain the old reviewed wheel/source commit.
2. With the currently installed compatible code and the exact intended profile,
   run `hermes review-ledger backup`. Confirm `restore_verified: true` and retain
   its reported backup path. This command uses the SQLite backup API and verifies
   a temporary restored copy. It does not bundle observation filesystem artifacts.
3. Preserve the profile configuration and filesystem artifacts separately. Keep
   the original resolved profile data path: database ownership is path-bound.
   If the operator CLI is unavailable, do not guess a database path or copy an
   active SQLite file; use the supported backup workflow with compatible tooling.

Then use the dedicated virtual environment, a reviewed new wheel, and the same
explicit profile:

```sh
python -m pip install --upgrade /absolute/path/to/reviewed-new-wheel.whl
python -m pip check
python -m review_ledger --version
python -m review_ledger upgrade --profile-dir "/absolute/path/to/profile"
python -m review_ledger status --profile-dir "/absolute/path/to/profile"
```

For an updated private Git commit, use the pinned Git URL above with
`pip install --upgrade` instead. If replacing a private build that also reports
1.0.2, pip may consider it already installed. Prefer a new clean virtual
environment containing the reviewed wheel; otherwise explicitly use pip's
`--force-reinstall` for that exact verified artifact, then run `pip check`.
Never infer source equality from a version string alone.

`upgrade` and `install` have identical safe code-replacement semantics. Neither
fetches packages. Identical content is a no-op; `upgrade` installs when absent.
Existing owned files must match their recorded hashes. User-modified code,
unknown files, manual installations, and malformed ownership metadata are
refused. There is no force-overwrite option. Review and preserve custom changes
before manually moving a manual/modified installation aside.

Repeat the explicit profile operation only for other profiles you intend to
update. Select the same profile in Hermes, run `plugins doctor`, `plugins
validate` and `plugins list` as shown in the README, then restart fresh sessions.
Read the actual results; a local package test is not a real profile validation.

## Recovery, migrations and removal

The installer stages complete code outside plugin discovery and journals the
rename replacement under a per-profile lock. A failed replacement rolls back;
rerunning after an interruption recovers before proceeding. It does not preserve
successful prior code as permanent history. If recovery is blocked, stop Hermes,
fix the reported file-access issue and rerun. Preserve the reported old/new state
until recovery succeeds. Windows file locks can require closing Hermes.
Network filesystems and sudden power-loss durability are not certified.

Code replacement does not open or migrate the database. The first later runtime
access migrates supported schema 1/2 and genuine adaptive schema 3 databases to
schema 5 transactionally. Existing schema 4 receives only the additive frozen-reference migration; existing schema 5 stays 5. The incompatible experimental
critic 0.2.0 schema 3 is refused without mutation; see the [compatibility
guide](v15-pilot.md#release-identities-and-compatibility). No schema downgrade is
provided. An older wheel is not a safe rollback after data migration. Restore a
verified compatible backup only at its original profile data path, preserving
later records separately and keeping matching code. Never relabel schema or
ownership manually.

Disable the plugin in the same profile and stop sessions before removal:

```sh
python -m review_ledger uninstall --profile-dir "/absolute/path/to/profile"
python -m pip uninstall hermes-review-ledger
```

The first command removes intact installer-owned plugin code only. Configuration,
SQLite data, approved lessons, artifacts and backups remain. The second removes
the command/package from its environment, not other profile copies. This guide
never instructs deletion of retained user data.

## Maintainer verification (local only)

```sh
python -m pip install -e '.[test]'
python -m pytest tests/test_installer.py tests/test_installer_packages.py
python -m build --sdist --wheel --no-isolation
```

Tests build an sdist, rebuild a wheel from that sdist, install in a clean
virtual environment with no network, exercise both CLI entry points, validate
payload hashes/resources, and preserve synthetic configuration/data through
install, upgrade and uninstall. Native Hermes gates remain separate. No command
here publishes a package or changes a user's real Hermes installation.

## Payload integrity on install and rebuild

Installation and packaging share one explicit canonical runtime inventory. A
missing required module/resource, an unrecognized runtime source file, or a
linked payload directory is rejected before publishing an installed plugin.
Changing runtime files requires updating that inventory in the same change.

A wheel rebuild uses one validated current-source snapshot for both its normal
Python package and its bundled native installer payload, rather than relying
on source mtimes. The build output must not alias the source package. This
prevents stale modules from surviving same-version or preserved-mtime rebuilds.
