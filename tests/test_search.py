from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from random import Random

import pytest

import search_common
from multiprocessing_search import search_multiprocess
from search_common import (
    BLOCK_SIZE,
    BatchResult,
    SearchResults,
    discover_files,
    find_keywords,
    merge_results,
    prepare_inputs,
    run_batch,
    search_sequential,
    split_files,
)
from threading_search import search_threaded

SEARCHES = [search_sequential, search_threaded, search_multiprocess]
type Search = Callable[..., SearchResults]


@pytest.mark.parametrize("search", SEARCHES)
def test_expected_results_and_unicode(search: Search, tmp_path: Path) -> None:
    first = tmp_path / "перший.txt"
    second = tmp_path / "druhý.txt"
    empty = tmp_path / "empty.txt"
    first.write_text("Python PYTHON\nПАРАЛЕЛЬНІСТЬ Straße", encoding="utf-8")
    second.write_text("multiprocessing\npython", encoding="utf-8")
    empty.touch()
    assert search(
        [second, first, empty, first],
        ["python", "process", "Паралельність", "STRASSE", "absent", "python"],
    ) == {
        "python": sorted([str(first), str(second)]),
        "process": [str(second)],
        "Паралельність": [str(first)],
        "STRASSE": [str(first)],
        "absent": [],
    }


@pytest.mark.parametrize("search", SEARCHES)
def test_case_sensitive(search: Search, tmp_path: Path) -> None:
    path = tmp_path / "case.txt"
    path.write_text("Python Straße", encoding="utf-8")
    assert search([path], ["python", "Python", "STRASSE"], case_sensitive=True) == {
        "python": [],
        "Python": [str(path)],
        "STRASSE": [],
    }


@pytest.mark.parametrize("search", SEARCHES)
def test_empty_input_and_no_matches(search: Search, tmp_path: Path) -> None:
    assert search([], ["python"]) == {"python": []}
    path = tmp_path / "empty.txt"
    path.touch()
    assert search([path], ["python"]) == {"python": []}


@pytest.mark.parametrize("search", SEARCHES)
@pytest.mark.parametrize("keywords", [[], [""], ["  "], ["python", "\n"]])
def test_invalid_keywords(search: Search, keywords: list[str]) -> None:
    with pytest.raises(ValueError, match="ключове слово"):
        search([], keywords)


@pytest.mark.parametrize("search", SEARCHES)
def test_errors_do_not_lose_other_files(search: Search, tmp_path: Path) -> None:
    valid = tmp_path / "valid.txt"
    invalid = tmp_path / "invalid.txt"
    missing = tmp_path / "missing.txt"
    valid.write_text("python", encoding="utf-8")
    invalid.write_bytes(b"python" + b" " * (BLOCK_SIZE * 2) + b"\xff")
    errors: list[str] = []
    assert search([valid, invalid, missing, tmp_path], ["python"], errors=errors) == {
        "python": [str(valid)]
    }
    assert len(errors) == 3
    assert any("UnicodeDecodeError" in error for error in errors)
    assert any("FileNotFoundError" in error for error in errors)


@pytest.mark.parametrize("search", SEARCHES)
def test_block_boundary_and_long_keyword(search: Search, tmp_path: Path) -> None:
    path = tmp_path / "boundary.txt"
    long_keyword = "a" * (BLOCK_SIZE + 13) + "finish"
    path.write_text(
        " " * (BLOCK_SIZE - 2) + "Straße\n" + long_keyword, encoding="utf-8"
    )
    assert search([path], ["STRASSE", "ße\naaa", long_keyword, "z"]) == {
        "STRASSE": [str(path)],
        "ße\naaa": [str(path)],
        long_keyword: [str(path)],
        "z": [],
    }


def test_single_character_and_literal_whitespace(tmp_path: Path) -> None:
    path = tmp_path / "text.txt"
    path.write_bytes(b"X\r\ncat dog\n")
    assert search_sequential([path], ["x", "cat dog", "X\r\n"]) == {
        "x": [str(path)],
        "cat dog": [str(path)],
        "X\r\n": [str(path)],
    }


def test_permission_error_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "denied.txt"
    path.touch()

    def denied(*args: object, **kwargs: object) -> None:
        raise PermissionError("read denied")

    monkeypatch.setattr(Path, "open", denied)
    errors: list[str] = []
    assert search_threaded([path], ["python"], errors=errors) == {"python": []}
    assert "PermissionError" in errors[0]


def test_errors_logged_if_no_collector(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    search_sequential([tmp_path / "missing"], ["python"])
    assert "FileNotFoundError" in caplog.text


def test_split_balanced_and_complete() -> None:
    files = [str(index) for index in range(11)]
    chunks = split_files(files, 4)
    assert list(map(len, chunks)) == [3, 3, 3, 2]
    assert sorted(path for chunk in chunks for path in chunk) == sorted(files)
    assert split_files(["only"], 100) == [["only"]]
    assert split_files([], 100) == []


@pytest.mark.parametrize("workers", [0, -1, True, 1.5])
def test_invalid_worker_count(workers: int) -> None:
    with pytest.raises(ValueError, match="виконавців"):
        split_files([], workers)


def test_iterators_are_consumed_once() -> None:
    assert prepare_inputs(iter(["b", "a", "b"]), iter(["x", "x"])) == (
        ["a", "b"],
        ["x"],
    )


def test_unexpected_worker_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object) -> BatchResult:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(search_common, "search_batch", fail)
    result = run_batch(["file"], ["word"], False)
    with pytest.raises(RuntimeError, match="unexpected"):
        merge_results([result], ["word"], [])


def test_discovery_recursive_and_missing(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    included = nested / "included.txt"
    included.touch()
    (nested / "ignored.md").touch()
    assert discover_files(tmp_path) == [str(included)]
    with pytest.raises(ValueError, match="Каталог"):
        discover_files(tmp_path / "missing")


def test_discovery_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failed_walk(
        self: Path, *, on_error: Callable[[OSError], None]
    ) -> list[tuple[Path, list[str], list[str]]]:
        on_error(PermissionError("denied"))
        return []

    monkeypatch.setattr(Path, "walk", failed_walk)
    with pytest.raises(PermissionError):
        discover_files(tmp_path)


@pytest.mark.parametrize("case_sensitive", [False, True])
@pytest.mark.parametrize("block_size", [1, 2, 7, 64])
def test_chunked_search_matches_whole_text_oracle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_sensitive: bool,
    block_size: int,
) -> None:
    generator = Random(20261022)
    alphabet = "aB іЇßİΣςﬃ\r\n"
    path = tmp_path / "oracle.txt"
    monkeypatch.setattr(search_common, "BLOCK_SIZE", block_size)
    for _ in range(30):
        content = "".join(generator.choices(alphabet, k=200))
        terms = [
            "".join(generator.choices(alphabet, k=generator.randint(1, 15)))
            for _ in range(15)
        ] + [content[3:30], content[62:85], "absent"]
        path.write_bytes(content.encode("utf-8"))
        haystack = content if case_sensitive else content.casefold()
        expected = {
            term
            for term in terms
            if (term if case_sensitive else term.casefold()) in haystack
        }
        assert (
            find_keywords(str(path), terms, case_sensitive=case_sensitive) == expected
        )
