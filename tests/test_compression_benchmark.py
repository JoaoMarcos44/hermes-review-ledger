"""The offline benchmark reports mechanism evidence, never inference savings."""
import importlib.util
import json
from pathlib import Path
import socket
from unittest.mock import patch

import pytest
from review_ledger.models import LedgerError

PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_compression.py"
SPEC = importlib.util.spec_from_file_location("compression_benchmark", PATH)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def test_zero_and_unavailable_reductions_and_distribution_show_growth():
    assert benchmark.reduction(0, 1) is None
    assert benchmark.reduction(None, 1) is None
    assert benchmark.reduction(1, None) is None
    assert benchmark.reduction(10, 12) < 0
    values = benchmark.distribution([-0.2, 0, .4])
    assert values["median"] == 0
    assert values["samples"] == [-0.2, 0, .4]
    assert values["min"] == -.2 and values["max"] == .4


def test_offline_benchmark_separates_equal_record_projection_and_trajectories():
    with patch.object(socket, "socket", side_effect=AssertionError("Benchmark attempted network")):
        report = benchmark.benchmark(iterations=1, measure_memory=False)
    assert len(report["cases"]) == 8
    assert report["counter"] is None
    assert report["real_inference_savings"] is None
    assert report["provider_total_tokens"] is None
    assert report["summary"]["representation_reduction"]["bytes"]["count"] == 8
    for case in report["cases"]:
        projection = case["representation"]
        assert projection["same_records"] and projection["protected_fields_equal"] and projection["source_unchanged"]
        assert projection["baseline"]["tokens"] is None
        assert projection["compact"]["tokens"] is None
        modes = case["trajectory"]["modes"]
        for mode in modes.values():
            assert mode["protected_fields_recovered"]
            assert mode["all_responses_within_char_cap"]
            assert mode["all_responses_within_byte_cap"]
            assert mode["observed_total"]["chars"] == mode["response_total"]["chars"] + mode["request_total"]["chars"]
            assert mode["observed_total"]["tokens"] is None
            assert mode["responses"] == mode["detail_requests"] + 2
            assert mode["latency_ms"]["cold_first_trajectory"] >= 0
            assert mode["latency_ms"]["warm_repeated_trajectories"]["count"] == 1
            assert mode["peak_tracemalloc_bytes"] is None
    indexed = {case["case"]: case for case in report["cases"]}
    assert indexed["short"]["representation"]["compact_grew"]
    assert indexed["high_repetition"]["representation"]["reduction"]["bytes"] > 0
    assert indexed["extensive_conditions"]["trajectory"]["modes"]["compact"]["detail_requests"] > 0
    unicode = indexed["unicode_escaping"]["representation"]["baseline"]
    assert unicode["bytes"] > unicode["chars"]
    assert "not process RSS" in report["methodology"]["memory"]


def test_explicit_output_only_and_memory_is_separate(tmp_path, capsys):
    output = tmp_path / "requested.json"
    assert benchmark.main(["--case", "short", "--iterations", "1", "--output", str(output)]) == 0
    assert capsys.readouterr().out == ""
    report = json.loads(output.read_text(encoding="utf-8"))
    for mode in report["cases"][0]["trajectory"]["modes"].values():
        assert mode["peak_tracemalloc_bytes"] > 0
    assert benchmark.main(["--case", "no_lessons", "--iterations", "1", "--no-memory"]) == 0
    stdout = capsys.readouterr().out
    assert json.loads(stdout)["cases"][0]["case"] == "no_lessons"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["requested.json"]


@pytest.mark.parametrize("arguments", [{"budget": True}, {"budget": 1999}, {"iterations": 0},
                                      {"iterations": 31}, {"cases": ["private"]}, {"cases": []}])
def test_invalid_benchmark_parameters_do_not_start_work(arguments):
    with pytest.raises((ValueError, LedgerError)):
        benchmark.benchmark(**arguments)
