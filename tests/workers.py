from __future__ import annotations

import os
import signal
import struct
import time
from collections.abc import Sequence
from multiprocessing.connection import Connection
from pathlib import Path
from unittest.mock import patch

from multiprocessing_search import ResultQueue
from search_common import BatchResult, run_batch


def crash_worker(
    files: Sequence[str], keywords: Sequence[str], case: bool, queue: ResultQueue
) -> None:
    raise SystemExit(7)


def silent_worker(
    files: Sequence[str], keywords: Sequence[str], case: bool, queue: ResultQueue
) -> None:
    return None


def failing_worker(
    files: Sequence[str], keywords: Sequence[str], case: bool, queue: ResultQueue
) -> None:
    queue.put(BatchResult({}, failure="injected failure"))


def slow_worker(
    files: Sequence[str], keywords: Sequence[str], case: bool, queue: ResultQueue
) -> None:
    time.sleep(30)


def slow_exit_worker(
    files: Sequence[str], keywords: Sequence[str], case: bool, queue: ResultQueue
) -> None:
    queue.put(BatchResult({keyword: [] for keyword in keywords}))
    time.sleep(30)


def crash_after_result_worker(
    files: Sequence[str], keywords: Sequence[str], case: bool, queue: ResultQueue
) -> None:
    queue.put(BatchResult({keyword: [] for keyword in keywords}))
    raise SystemExit(7)


def ignore_termination_worker(
    files: Sequence[str], keywords: Sequence[str], case: bool, queue: ResultQueue
) -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    queue.put(BatchResult({keyword: [] for keyword in keywords}))
    time.sleep(30)


def interrupted_transfer_worker(
    files: Sequence[str], keywords: Sequence[str], case: bool, queue: ResultQueue
) -> None:
    original_send = vars(Connection)["_send_bytes"]

    def truncated_send(connection: Connection, payload: bytes) -> None:
        if len(payload) > 16384:
            os.write(connection.fileno(), struct.pack("!i", len(payload)))
            os.write(connection.fileno(), b"incomplete")
            os._exit(7)
        original_send(connection, payload)

    with patch.object(Connection, "_send_bytes", truncated_send):
        queue.put(BatchResult({keyword: ["x" * 32768] for keyword in keywords}))
        time.sleep(30)


def recording_worker(
    files: Sequence[str], keywords: Sequence[str], case: bool, queue: ResultQueue
) -> None:
    for path in files:
        with Path(path).with_suffix(".workers").open("a", encoding="utf-8") as stream:
            stream.write(f"{os.getpid()}\n")
    queue.put(run_batch(files, keywords, case))
