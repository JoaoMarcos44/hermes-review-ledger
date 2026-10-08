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
    assert "only when the owned investigation is ready to complete" in normalized
    assert "no extra model call or background worker" in normalized


def test_paused_or_incomplete_run_never_proposes_a_lesson():
    skill = SKILL.read_text(encoding="utf-8")
    skill_section = skill.split("## Automatic conditional lesson creation\n", 1)[1].split("\n## ", 1)[0]
    protocol = (Path(__file__).resolve().parents[1] / "review_ledger" / "resources" / "protocol.md").read_text(encoding="utf-8")
    protocol_text = " ".join(protocol.split()).casefold()

    assert "if the run will be paused or remains incomplete, do not propose a lesson" in skill_section.casefold()
    assert "if missing evidence or budget requires a pause, do not propose a lesson" in protocol_text
