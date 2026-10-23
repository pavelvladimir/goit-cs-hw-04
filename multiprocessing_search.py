from __future__ import annotations

import math
import multiprocessing
from collections.abc import Iterable, Sequence
from multiprocessing.process import BaseProcess
from queue import Empty
from time import monotonic
from typing import Protocol

from search_common import (
    BatchResult,
    FilePaths,
    SearchResults,
    merge_results,
    prepare_inputs,
    run_batch,
    split_files,
)


class ResultQueue(Protocol):
    def put(self, result: BatchResult) -> None: ...

    def get(self, *, timeout: float) -> BatchResult: ...

    def get_nowait(self) -> BatchResult: ...


def _process_worker(
    files: Sequence[str],
    keywords: Sequence[str],
    case_sensitive: bool,
    results: ResultQueue,
) -> None:
    results.put(run_batch(files, keywords, case_sensitive))


def _collect_results(
    results: ResultQueue, processes: Sequence[BaseProcess], deadline: float
) -> list[BatchResult]:
    batches: list[BatchResult] = []
    while len(batches) < len(processes):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("Перевищено час очікування процесів.")
        try:
            batch = results.get(timeout=min(0.1, remaining))
        except Empty:
            if any(process.exitcode not in (None, 0) for process in processes):
                raise RuntimeError("Дочірній процес аварійно завершився.") from None
            if any(process.exitcode is None for process in processes):
                continue
            try:
                batch = results.get_nowait()
            except Empty:
                raise RuntimeError("Не всі процеси повернули результат.") from None
        if batch.failure is not None:
            raise RuntimeError(f"Помилка виконавця: {batch.failure}")
        batches.append(batch)
    for process in processes:
        process.join(timeout=max(0, deadline - monotonic()))
        if process.is_alive():
            raise TimeoutError("Процес не завершився вчасно.")
        if process.exitcode != 0:
            raise RuntimeError("Дочірній процес аварійно завершився.")
    return batches


def _cleanup(processes: Sequence[BaseProcess]) -> None:
    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        if process.pid is not None:
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join()
        process.close()


def search_multiprocess(
    files: FilePaths,
    keywords: Iterable[str],
    workers: int = 4,
    *,
    case_sensitive: bool = False,
    errors: list[str] | None = None,
    timeout: float = 60.0,
) -> SearchResults:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout має бути додатним скінченним числом.")
    paths, terms = prepare_inputs(files, keywords)
    chunks = split_files(paths, workers)
    if not chunks:
        return {keyword: [] for keyword in terms}
    context = multiprocessing.get_context("spawn")
    deadline = monotonic() + timeout
    try:
        manager = context.Manager()
    except (EOFError, OSError) as error:
        raise RuntimeError("Не вдалося запустити менеджер локальної черги.") from error
    with manager:
        results: ResultQueue = manager.Queue()
        processes: list[BaseProcess] = []
        try:
            for index, chunk in enumerate(chunks):
                if monotonic() >= deadline:
                    raise TimeoutError("Перевищено час запуску процесів.")
                process = context.Process(
                    target=_process_worker,
                    args=(chunk, terms, case_sensitive, results),
                    name=f"keyword-search-{index}",
                )
                processes.append(process)
                process.start()
            batches = _collect_results(results, processes, deadline)
            return merge_results(batches, terms, errors)
        finally:
            _cleanup(processes)


if __name__ == "__main__":
    from main import main

    raise SystemExit(main(default_mode="multiprocessing"))
