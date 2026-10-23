from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
import threading
from collections import Counter
from collections.abc import Callable
from multiprocessing.context import SpawnContext, SpawnProcess
from pathlib import Path
from queue import Empty
from time import monotonic
from unittest.mock import Mock

import pytest

import multiprocessing_search
import search_common
import threading_search
from multiprocessing_search import search_multiprocess
from search_common import BatchResult, SearchResults
from tests.workers import (
    crash_after_result_worker,
    crash_worker,
    failing_worker,
    ignore_termination_worker,
    recording_worker,
    silent_worker,
    slow_exit_worker,
    slow_worker,
)
from threading_search import search_threaded


def test_threads_really_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    barrier = threading.Barrier(2, timeout=5)
    identities: set[int] = set()
    lock = threading.Lock()

    def find(*args: object, **kwargs: object) -> set[str]:
        with lock:
            identities.add(threading.get_ident())
        barrier.wait()
        return {"word"}

    monkeypatch.setattr(search_common, "find_keywords", find)
    assert search_threaded(["a", "b"], ["word"], 2) == {"word": ["a", "b"]}
    assert len(identities) == 2


def test_thread_start_failure_joins_started_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = threading.Thread.start
    calls = 0

    def start(thread: threading.Thread) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("cannot start thread")
        original(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    with pytest.raises(RuntimeError, match="cannot start"):
        search_threaded(["a", "b"], ["word"], 2)
    assert not any(
        thread.name.startswith("keyword-search-") for thread in threading.enumerate()
    )


def test_missing_thread_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(threading_search, "_thread_worker", lambda *args: None)
    with pytest.raises(RuntimeError, match="Не всі потоки"):
        search_threaded(["a"], ["word"])


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="Timeout"):
        search_multiprocess([], ["word"], timeout=timeout)


@pytest.mark.parametrize(
    ("worker", "message", "exception"),
    [
        (crash_worker, "аварійно", RuntimeError),
        (silent_worker, "Не всі процеси", RuntimeError),
        (failing_worker, "injected failure", RuntimeError),
        (slow_worker, "очікування", TimeoutError),
        (slow_exit_worker, "не завершився", TimeoutError),
        (crash_after_result_worker, "аварійно", RuntimeError),
    ],
)
def test_process_failures_clean_up(
    worker: Callable[..., None],
    message: str,
    exception: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = {process.pid for process in multiprocessing.active_children()}
    monkeypatch.setattr(multiprocessing_search, "_process_worker", worker)
    with pytest.raises(exception, match=message):
        search_multiprocess(["file"], ["word"], timeout=2)
    assert {process.pid for process in multiprocessing.active_children()} == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal handling")
def test_unresponsive_process_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        multiprocessing_search, "_process_worker", ignore_termination_worker
    )
    with pytest.raises(TimeoutError, match="не завершився"):
        search_multiprocess(["file"], ["word"], timeout=2)
    assert not multiprocessing.active_children()


def test_process_start_failure_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    original = SpawnProcess.start
    calls = 0

    def start(process: SpawnProcess) -> None:
        nonlocal calls
        if process.name.startswith("keyword-search-"):
            calls += 1
            if calls == 2:
                raise OSError("cannot start process")
        original(process)

    monkeypatch.setattr(SpawnProcess, "start", start)
    with pytest.raises(OSError, match="cannot start"):
        search_multiprocess(["a", "b"], ["word"], 2)
    assert not multiprocessing.active_children()


def test_large_queue_payload_does_not_deadlock(tmp_path: Path) -> None:
    expected: SearchResults = {"python": [], "thread": [], "process": []}
    for index in range(350):
        path = tmp_path / (f"{index:04d}-" + "x" * 180 + ".txt")
        path.write_text("python thread process", encoding="utf-8")
        for paths in expected.values():
            paths.append(str(path))
    assert search_multiprocess(expected["python"], expected, 2, timeout=15) == expected


@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO and file permissions")
def test_special_files_and_real_permission_errors(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe.txt"
    denied = tmp_path / "denied.txt"
    os.mkfifo(fifo)
    denied.write_text("python", encoding="utf-8")
    denied.chmod(0)
    try:
        errors: list[str] = []
        result = search_multiprocess(
            [fifo, denied], ["python"], errors=errors, timeout=5
        )
        assert any(str(fifo) in error for error in errors)
        if os.geteuid() != 0:
            assert len(errors) == 2
            assert result == {"python": []}
    finally:
        denied.chmod(0o600)


def test_result_arriving_between_empty_poll_and_exit_check() -> None:
    result = BatchResult({"word": ["file"]})
    queue = Mock()
    queue.get.side_effect = Empty
    queue.get_nowait.return_value = result
    process = Mock(exitcode=0)
    process.is_alive.return_value = False
    assert multiprocessing_search._collect_results(
        queue, [process], monotonic() + 5
    ) == [result]


@pytest.mark.skipif(os.name != "posix", reason="POSIX truncated IPC frame injection")
def test_crash_during_queue_transfer_is_bounded() -> None:
    probe = """
import multiprocessing_search
from tests.workers import interrupted_transfer_worker

multiprocessing_search._process_worker = interrupted_transfer_worker
try:
    multiprocessing_search.search_multiprocess(["file"], ["word"], timeout=2)
except RuntimeError as error:
    assert "аварійно" in str(error), str(error)
else:
    raise AssertionError("Expected a worker crash")
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_files_are_distributed_across_real_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = [tmp_path / f"input-{index}.txt" for index in range(7)]
    for path in files:
        path.write_text("python", encoding="utf-8")
    monkeypatch.setattr(multiprocessing_search, "_process_worker", recording_worker)
    assert search_multiprocess(files, ["python"], 4) == {
        "python": [str(path) for path in files]
    }
    owners = []
    for path in files:
        entries = path.with_suffix(".workers").read_text(encoding="utf-8").splitlines()
        assert len(entries) == 1
        owners.append(int(entries[0]))
    assert os.getpid() not in owners
    assert sorted(Counter(owners).values()) == [1, 2, 2, 2]


def test_no_more_workers_start_after_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([0.0, 0.0, 2.0])
    original = SpawnProcess.start
    started: list[str] = []

    def start(process: SpawnProcess) -> None:
        if process.name.startswith("keyword-search-"):
            started.append(process.name)
        original(process)

    monkeypatch.setattr(multiprocessing_search, "monotonic", lambda: next(clock))
    monkeypatch.setattr(multiprocessing_search, "_process_worker", slow_worker)
    monkeypatch.setattr(SpawnProcess, "start", start)
    with pytest.raises(TimeoutError, match="запуску"):
        search_multiprocess(["a", "b"], ["word"], 2, timeout=1)
    assert len(started) == 1
    assert not multiprocessing.active_children()


def test_exception_after_process_start_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = SpawnProcess.start

    def start(process: SpawnProcess) -> None:
        original(process)
        if process.name.startswith("keyword-search-"):
            raise OSError("interrupted after start")

    monkeypatch.setattr(multiprocessing_search, "_process_worker", slow_worker)
    monkeypatch.setattr(SpawnProcess, "start", start)
    with pytest.raises(OSError, match="interrupted after start"):
        search_multiprocess(["file"], ["word"])
    assert not multiprocessing.active_children()


def test_manager_start_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object) -> None:
        raise EOFError

    monkeypatch.setattr(SpawnContext, "Manager", fail)
    with pytest.raises(RuntimeError, match="менеджер"):
        search_multiprocess(["file"], ["word"])
