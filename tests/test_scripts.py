from __future__ import annotations

import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

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
    assert report["command"] is None
    assert report["arguments"][-1] == str(output)
    assert (
        report["source_sha256"]["scripts/benchmark.py"]
        == sha256(Path(benchmark.__file__).read_bytes()).hexdigest()
    )
    assert report["speedup_definition"] == {
        "numerator": "sequential median_seconds for the same profile",
        "denominator": "median_seconds of this configuration",
        "greater_than_one": "faster than sequential",
    }
    assert len(report["measurements"]) == 10
    for measurement in report["measurements"]:
        assert len(measurement["seconds"]) == 2
        assert measurement["median_seconds"] > 0
        assert measurement["speedup"] > 0
        baseline = next(
            item["median_seconds"]
            for item in report["measurements"]
            if item["profile"] == measurement["profile"]
            and item["mode"] == "sequential"
        )
        assert measurement["speedup"] == pytest.approx(
            baseline / measurement["median_seconds"]
        )


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


@pytest.mark.parametrize("output", ["  abc123\n", ""])
def test_command_output(output: str, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, stdout=output))
    monkeypatch.setattr(subprocess, "run", runner)
    assert benchmark.command_output("git", "rev-parse", "HEAD") == output.strip()
    runner.assert_called_once_with(
        ("git", "rev-parse", "HEAD"),
        cwd=benchmark.ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=2,
    )


@pytest.mark.parametrize(
    "error",
    [
        OSError("unavailable"),
        subprocess.CalledProcessError(128, "git"),
        subprocess.TimeoutExpired("git", 2),
        UnicodeError("invalid output"),
    ],
)
def test_command_failure_is_optional(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=error))
    assert benchmark.command_output("git", "rev-parse", "HEAD") is None


@pytest.mark.parametrize("model", ["Apple M4 Max", "", None])
def test_macos_cpu_model(model: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    command = Mock(return_value=model)
    monkeypatch.setattr(benchmark, "command_output", command)
    assert benchmark.cpu_model() == (model or None)
    command.assert_called_once_with("sysctl", "-n", "machdep.cpu.brand_string")


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("processor : 0\nmodel name : \nmodel name : Test CPU\n", "Test CPU"),
        ("Hardware : Test ARM CPU\n", "Test ARM CPU"),
        ("processor : 0\n", None),
    ],
)
def test_linux_cpu_model(
    content: str, expected: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    reader = Mock(return_value=content)
    monkeypatch.setattr(Path, "read_text", reader)
    assert benchmark.cpu_model() == expected
    reader.assert_called_once_with(encoding="utf-8")


@pytest.mark.parametrize("error", [OSError("denied"), UnicodeError("invalid text")])
def test_unreadable_cpu_info(error: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(Path, "read_text", Mock(side_effect=error))
    assert benchmark.cpu_model() is None


def test_unknown_cpu_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Other")
    assert benchmark.cpu_model() is None


@pytest.mark.parametrize("status", ["", " M scripts/benchmark.py", None])
@pytest.mark.parametrize(
    "argv", [None, ["--quick", "--output", "report with spaces.json"]]
)
def test_provenance(
    status: str | None,
    argv: list[str] | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "a" * 40 if status is not None else None
    command = Mock(side_effect=[status, revision])
    monkeypatch.setattr(benchmark, "command_output", command)
    monkeypatch.setattr(benchmark, "cpu_model", lambda: "Test CPU")
    monkeypatch.setattr(benchmark, "ROOT", tmp_path)
    for name in benchmark.SOURCE_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    invocation = ["python", "-m", "scripts.benchmark", "--quick"]
    monkeypatch.setattr(sys, "orig_argv", invocation)
    monkeypatch.setattr(sys, "argv", ["scripts/benchmark.py", "--quick"])
    report = benchmark.provenance(argv)
    assert report["source_revision"] == revision
    assert report["source_dirty"] is (None if status is None else bool(status))
    assert report["source_sha256"] == {
        name: sha256(name.encode()).hexdigest() for name in benchmark.SOURCE_FILES
    }
    assert report["command"] == (invocation if argv is None else None)
    assert report["arguments"] == (["--quick"] if argv is None else argv)
    assert report["working_directory"] == str(Path.cwd())
    assert report["cpu_model"] == "Test CPU"
    assert datetime.fromisoformat(str(report["started_at_utc"])).tzinfo is UTC
