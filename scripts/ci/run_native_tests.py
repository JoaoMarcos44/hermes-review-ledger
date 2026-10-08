"""Run the full suite on the current OS, retaining evidence and rejecting skips.

This is a native runner, not a platform simulation. It imports no Hermes modules
itself; the integration tests load the exact public source in temporary profiles.
Only explicit runtime metadata, package versions, and synthetic test output are
recorded. Environment variables, credentials, profiles, and Git config are not.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
HERMES_REVISION = "0dbaf33f67acf1f6d8e8e6c6efe8042ef8db98c4"
REQUIRED_INTEGRATION_TESTS = {
    "test_native_external_reference_is_context_not_evidence",
    "test_native_external_reference_revoke_and_cutoff",
    "test_actual_plugin_doctor",
    "test_real_discovery_and_skill_serving",
    "test_real_registry_rejects_absent_trusted_session",
    "test_real_cli_command_attachment",
    "test_real_host_profile_state_property_is_dynamic",
    "test_real_model_dispatch_binds_session_and_blocks_model_owner_fields",
    "test_real_profiles_keep_same_repository_separate",
    "test_real_cli_transfer_invalidates_previous_owner_generation",
    "test_real_cli_approval_and_revocation_are_separate_from_model_tools",
    "test_real_tools_discover_historical_runs_without_cached_ids",
    "test_real_tool_unicode_budget_reference_and_complete_detail",
    "test_real_scoped_github_credentials_and_unscoped_refusal",
    "test_real_v15_context_registry_usage_and_resume",
    "test_real_critic_disabled_preserves_v15_runtime",
    "test_real_critic_unverifiable_preserves_v15_runtime",
    "test_native_compression_is_disabled_by_default_and_legacy_unchanged",
    "test_native_final_string_enforces_operator_and_model_limits",
    "test_native_model_cannot_change_operator_controls",
    "test_native_resume_and_independent_detail_preserve_context",
    "test_native_invalidated_observation_never_returns_as_projected_detail",
}


def git_revision(path: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        text=True, encoding="utf-8", stderr=subprocess.STDOUT, timeout=30,
    ).strip()


def runtime_identity() -> dict:
    return {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "sys_platform": sys.platform,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_build": platform.python_build(),
        "sqlite_version": sqlite3.sqlite_version,
        "hermes_expected_revision": HERMES_REVISION,
        "packages": sorted(
            f"{distribution.metadata['Name']}=={distribution.version}"
            for distribution in distributions()
        ),
        "status": "not_run",
        "counts": None,
    }


def summarize_junit(path: Path) -> tuple[dict, list[str]]:
    cases = list(ET.parse(path).getroot().iter("testcase"))
    counts = {"total": len(cases), "passed": 0, "failures": 0, "errors": 0,
              "skipped": 0, "core": 0, "hermes_integration": 0}
    actual_integration = set()
    for case in cases:
        integration = case.get("classname", "").split(".")[-1] in {"test_hermes_integration", "test_compression_native", "test_reference_native"}
        counts["hermes_integration" if integration else "core"] += 1
        if integration:
            actual_integration.add(case.get("name"))
        if case.find("error") is not None:
            counts["errors"] += 1
        elif case.find("failure") is not None:
            counts["failures"] += 1
        elif case.find("skipped") is not None:
            counts["skipped"] += 1
        else:
            counts["passed"] += 1
    problems = []
    if not counts["core"]:
        problems.append("No core tests ran")
    missing = REQUIRED_INTEGRATION_TESTS - actual_integration
    if missing:
        problems.append("Missing real Hermes tests: " + ", ".join(sorted(missing)))
    if counts["hermes_integration"] != len(REQUIRED_INTEGRATION_TESTS):
        problems.append("Real Hermes test count differs from the required contract cases")
    for outcome in ("failures", "errors", "skipped"):
        if counts[outcome]:
            problems.append(f"JUnit contains {counts[outcome]} {outcome}")
    return counts, problems


def save_report(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-source", type=Path, required=True)
    parser.add_argument("--expected-platform", choices=("Linux", "Darwin", "Windows"))
    parser.add_argument("--reports-dir", type=Path, default=ROOT / "ci-artifacts")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    source = args.hermes_source.resolve()
    reports = args.reports_dir.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    report_path = reports / "runtime.json"
    report = runtime_identity()
    try:
        report["ledger_revision"] = git_revision(ROOT)
    except (OSError, subprocess.SubprocessError):
        # The same runner can validate a local source tree before its first commit.
        report["ledger_revision"] = None
    save_report(report_path, report)
    try:
        if sys.version_info[:2] != (3, 14):
            raise RuntimeError("Pinned Hermes integration requires actual Python 3.14")
        if args.expected_platform and platform.system() != args.expected_platform:
            raise RuntimeError(f"Expected native {args.expected_platform}; observed {platform.system()}")
        if not (source / "hermes_cli" / "plugins.py").is_file():
            raise RuntimeError(f"Missing real Hermes source at {source}")
        report["hermes_actual_revision"] = git_revision(source)
        if report["hermes_actual_revision"] != HERMES_REVISION:
            raise RuntimeError("Hermes source revision differs from the pinned public contract")
        changed = subprocess.check_output(
            ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"],
            text=True, encoding="utf-8", timeout=30,
        )
        if changed.strip():
            raise RuntimeError("Pinned Hermes source has modified tracked files")
        report["source_verified"] = True
        if args.preflight_only:
            print(f"Verified {platform.system()}, Python {platform.python_version()}, Hermes {HERMES_REVISION}")
            save_report(report_path, report)
            return 0

        junit = reports / "junit.xml"
        # A previous report must not turn a failed invocation into apparent evidence.
        junit.unlink(missing_ok=True)
        command = [sys.executable, "-X", "utf8", "-m", "pytest", "-q", "tests",
                   f"--junitxml={junit}"]
        env = os.environ.copy()
        env["HERMES_SOURCE_DIR"] = str(source)
        env["HERMES_INTEGRATION_REQUIRED"] = "1"
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        # Keep developer/runner options from filtering away cases or changing paths.
        env.pop("PYTEST_ADDOPTS", None)
        env.pop("PYTEST_PLUGINS", None)
        with (reports / "pytest.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=ROOT, env=env, text=True,
                                    encoding="utf-8", stdout=log, stderr=subprocess.STDOUT,
                                    timeout=1200, check=False)
        print((reports / "pytest.log").read_text(encoding="utf-8"))
        report["pytest_exit_code"] = result.returncode
        report["counts"], problems = summarize_junit(junit)
        if result.returncode:
            problems.append(f"pytest exited with {result.returncode}")
        report["problems"] = problems
        report["status"] = "failed" if problems else "passed"
    except (OSError, RuntimeError, subprocess.SubprocessError, ET.ParseError) as exc:
        report["status"] = "failed"
        report["problems"] = [str(exc)]
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    save_report(report_path, report)
    print(json.dumps({key: report.get(key) for key in ("status", "counts", "problems")}, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
