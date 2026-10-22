from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import main
from search_common import SearchResults

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("script", "modes"),
    [
        ("main.py", ["threading", "multiprocessing"]),
        ("threading_search.py", ["threading"]),
        ("multiprocessing_search.py", ["multiprocessing"]),
    ],
)
def test_actual_entry_points(script: str, modes: list[str]) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert '"python"' in completed.stdout
    assert "data/01_python.txt" in completed.stdout
    for mode in modes:
        assert f"{mode}: " in completed.stdout


def test_explicit_files_and_partial_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "with space.txt"
    path.write_text("PYTHON", encoding="utf-8")
    code = main.main(
        ["--files", str(path), str(tmp_path / "missing"), "--keywords", "python"]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert str(path) in captured.out
    assert "FileNotFoundError" in captured.err
    assert "збігаються" in captured.out


def test_empty_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main.main(["--directory", str(tmp_path)]) == 0
    assert "Файлів: 0" in capsys.readouterr().out


def test_missing_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main.main(["--directory", str(tmp_path / "missing")]) == 1
    assert "Каталог" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--workers", "0"],
        ["--workers", "abc"],
        ["--timeout", "0"],
        ["--timeout", "nan"],
        ["--timeout", "inf"],
        ["--timeout", "abc"],
        ["--mode", "invalid"],
        ["--files", "a", "--directory", "data"],
    ],
)
def test_invalid_arguments(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main.main(arguments)
    assert error.value.code == 2


def test_empty_keyword(capsys: pytest.CaptureFixture[str]) -> None:
    assert main.main(["--keywords", ""]) == 1
    assert "ключове слово" in capsys.readouterr().err


def test_changed_results_are_detected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def changed(*args: object, **kwargs: object) -> SearchResults:
        return {"python": ["changed"]}

    monkeypatch.setattr(main, "search_multiprocess", changed)
    assert main.main(["--keywords", "python"]) == 1
    assert "відрізняються" in capsys.readouterr().err


def test_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupt(*args: object, **kwargs: object) -> SearchResults:
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "search_threaded", interrupt)
    assert main.main([]) == 130
    assert "перервано" in capsys.readouterr().err


def test_case_sensitive_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "file.txt"
    path.write_text("Python", encoding="utf-8")
    assert (
        main.main(
            [
                "--files",
                str(path),
                "--keywords",
                "python",
                "--case-sensitive",
                "--workers",
                "8",
                "--timeout",
                "5",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert '"python": []' in output
    assert "виконавців: 1" in output
