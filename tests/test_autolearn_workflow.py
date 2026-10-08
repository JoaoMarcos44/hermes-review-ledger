from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "skills" / "review-ledger" / "SKILL.md"


def test_completed_investigation_proactively_proposes_only_supported_lessons():
    text = SKILL.read_text(encoding="utf-8")
    section = text.split("## Automatic conditional lesson creation\n", 1)[1].split("\n## ", 1)[0]

    assert "Do not wait for the user to ask" in section
    assert "before `ledger_run(action=\"complete\")`" in section
    assert '`ledger_lesson(action="propose")`' in section
    assert "exact source observation IDs" in section
    assert "complete behavioral test report" in section
    assert "not a background worker" in section
    assert "If no lesson meets the criteria, make no lesson call" in section


def test_host_neutral_protocol_requires_proactive_conditional_lesson_creation():
    protocol_path = Path(__file__).resolve().parents[1] / "review_ledger" / "resources" / "protocol.md"
    protocol = protocol_path.read_text(encoding="utf-8")
    normalized = " ".join(protocol.split()).casefold()
    assert protocol.startswith("# Review Ledger procedure 3\n")
    assert "proactively assess the current run for a reusable conditional lesson" in normalized
    assert "before completing the owned run" in normalized
    assert "no extra model call or background worker" in normalized
