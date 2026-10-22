from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import benchmark
from search_common import SearchResults


def test_benchmark_checks_results_and_records_samples(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    assert (
        benchmark.main(
            [
                "--quick",
                "--repeats",
                "2",
                "--workers",
                "1",
                "2",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["start_method"] == "spawn"
    assert report["repeats"] == 2
    assert len(report["measurements"]) == 10
    for measurement in report["measurements"]:
        assert len(measurement["seconds"]) == 2
        assert measurement["median_seconds"] > 0
        assert measurement["speedup"] > 0


def test_benchmark_rejects_wrong_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def wrong(*args: object, **kwargs: object) -> SearchResults:
        return {}

    monkeypatch.setattr(benchmark, "search_sequential", wrong)
    with pytest.raises(RuntimeError, match="Некоректний"):
        benchmark.measure("sequential", [], {"python": []}, 1)


def test_benchmark_write_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        benchmark.main(
            ["--quick", "--repeats", "1", "--workers", "1", "--output", str(tmp_path)]
        )
        == 1
    )
    assert "Помилка benchmark" in capsys.readouterr().err
