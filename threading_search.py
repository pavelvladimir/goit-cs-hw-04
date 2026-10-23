from __future__ import annotations

import threading
from collections.abc import Iterable, Sequence

from search_common import (
    BatchResult,
    FilePaths,
    SearchResults,
    merge_results,
    prepare_inputs,
    run_batch,
    split_files,
)


def _thread_worker(
    files: Sequence[str],
    keywords: Sequence[str],
    case_sensitive: bool,
    results: list[BatchResult],
    lock: threading.Lock,
) -> None:
    result = run_batch(files, keywords, case_sensitive)
    with lock:
        results.append(result)


def search_threaded(
    files: FilePaths,
    keywords: Iterable[str],
    workers: int = 4,
    *,
    case_sensitive: bool = False,
    errors: list[str] | None = None,
) -> SearchResults:
    paths, terms = prepare_inputs(files, keywords)
    chunks = split_files(paths, workers)
    results: list[BatchResult] = []
    lock = threading.Lock()
    threads: list[threading.Thread] = []
    try:
        for index, chunk in enumerate(chunks):
            thread = threading.Thread(
                target=_thread_worker,
                args=(chunk, terms, case_sensitive, results, lock),
                name=f"keyword-search-{index}",
            )
            thread.start()
            threads.append(thread)
    finally:
        for thread in threads:
            thread.join()
    if len(results) != len(chunks):
        raise RuntimeError("Не всі потоки повернули результат.")
    return merge_results(results, terms, errors)


if __name__ == "__main__":
    from main import main

    raise SystemExit(main(default_mode="threading"))
