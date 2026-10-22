from __future__ import annotations

import logging
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

type SearchResults = dict[str, list[str]]
type FilePaths = Iterable[str | Path]

BLOCK_SIZE = 64 * 1024


@dataclass
class BatchResult:
    matches: SearchResults
    errors: list[str] = field(default_factory=list)
    failure: str | None = None


def prepare_inputs(
    files: FilePaths, keywords: Iterable[str]
) -> tuple[list[str], list[str]]:
    terms = list(dict.fromkeys(keywords))
    if not terms or any(not term.strip() for term in terms):
        raise ValueError("Потрібне хоча б одне непорожнє ключове слово.")
    paths = sorted({str(Path(path)) for path in files})
    return paths, terms


def split_files(files: Sequence[str], workers: int) -> list[list[str]]:
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("Кількість виконавців має бути додатним цілим числом.")
    count = min(workers, len(files))
    return [list(files[index::count]) for index in range(count)]


def find_keywords(
    path: str, keywords: Sequence[str], *, case_sensitive: bool = False
) -> set[str]:
    if not stat.S_ISREG(Path(path).stat().st_mode):
        raise OSError(f"Не є звичайним файлом: {path}")
    needles = {
        keyword: keyword if case_sensitive else keyword.casefold()
        for keyword in keywords
    }
    overlap = max(map(len, needles.values())) - 1
    found: set[str] = set()
    tail = ""
    with Path(path).open(encoding="utf-8", newline="") as stream:
        while block := stream.read(BLOCK_SIZE):
            text = tail + (block if case_sensitive else block.casefold())
            found.update(
                keyword
                for keyword, needle in needles.items()
                if keyword not in found and needle in text
            )
            tail = text[-overlap:] if overlap else ""
    return found


def search_batch(
    files: Sequence[str], keywords: Sequence[str], case_sensitive: bool
) -> BatchResult:
    result = BatchResult({keyword: [] for keyword in keywords})
    for path in files:
        try:
            found = find_keywords(path, keywords, case_sensitive=case_sensitive)
        except (OSError, UnicodeError) as error:
            result.errors.append(f"{path}: {type(error).__name__}: {error}")
            continue
        for keyword in found:
            result.matches[keyword].append(path)
    return result


def run_batch(
    files: Sequence[str], keywords: Sequence[str], case_sensitive: bool
) -> BatchResult:
    try:
        return search_batch(files, keywords, case_sensitive)
    except Exception as error:
        return BatchResult({}, failure=f"{type(error).__name__}: {error}")


def merge_results(
    batches: Iterable[BatchResult], keywords: Sequence[str], errors: list[str] | None
) -> SearchResults:
    combined: dict[str, set[str]] = {keyword: set() for keyword in keywords}
    problems: list[str] = []
    for batch in batches:
        if batch.failure is not None:
            raise RuntimeError(f"Помилка виконавця: {batch.failure}")
        for keyword in keywords:
            combined[keyword].update(batch.matches[keyword])
        problems.extend(batch.errors)
    if errors is None:
        for problem in sorted(problems):
            logging.getLogger(__name__).warning("%s", problem)
    else:
        errors.extend(sorted(problems))
    return {keyword: sorted(paths) for keyword, paths in combined.items()}


def search_sequential(
    files: FilePaths,
    keywords: Iterable[str],
    *,
    case_sensitive: bool = False,
    errors: list[str] | None = None,
) -> SearchResults:
    paths, terms = prepare_inputs(files, keywords)
    return merge_results([search_batch(paths, terms, case_sensitive)], terms, errors)


def discover_files(directory: Path) -> list[str]:
    if not directory.is_dir():
        raise ValueError(f"Каталог не існує або недоступний: {directory}")

    def raise_walk_error(error: OSError) -> None:
        raise error

    files: list[str] = []
    for root, _, names in directory.walk(on_error=raise_walk_error):
        files.extend(str(root / name) for name in names if name.endswith(".txt"))
    return sorted(files)
