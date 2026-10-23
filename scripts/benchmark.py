from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import TypedDict

from main import positive_integer
from multiprocessing_search import search_multiprocess
from search_common import SearchResults, search_sequential
from threading_search import search_threaded

KEYWORDS = ["python", "thread", "process", "паралельність", "absent-keyword"]
ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    "main.py",
    "search_common.py",
    "threading_search.py",
    "multiprocessing_search.py",
    "scripts/__init__.py",
    "scripts/benchmark.py",
)


class Measurement(TypedDict):
    profile: str
    files: int
    bytes: int
    mode: str
    workers: int
    seconds: list[float]
    median_seconds: float
    speedup: float


def command_output(*command: str) -> str | None:
    try:
        return subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        ).stdout.strip()
    except OSError, subprocess.SubprocessError, UnicodeError:
        return None


def cpu_model() -> str | None:
    system = platform.system()
    if system == "Darwin":
        return command_output("sysctl", "-n", "machdep.cpu.brand_string") or None
    if system == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                name, _, value = line.partition(":")
                if name.strip() in {"model name", "Hardware"} and value.strip():
                    return value.strip()
        except OSError, UnicodeError:
            return None
    return None


def provenance(argv: list[str] | None) -> dict[str, object]:
    status = command_output("git", "status", "--porcelain", "--untracked-files=normal")
    return {
        "started_at_utc": datetime.now(UTC).isoformat(),
        "source_revision": command_output("git", "rev-parse", "HEAD"),
        "source_dirty": None if status is None else bool(status),
        "source_sha256": {
            name: sha256((ROOT / name).read_bytes()).hexdigest()
            for name in SOURCE_FILES
        },
        "command": list(sys.orig_argv) if argv is None else None,
        "arguments": list(sys.argv[1:] if argv is None else argv),
        "working_directory": str(Path.cwd()),
        "cpu_model": cpu_model(),
    }


def generate_dataset(
    directory: Path, file_count: int, size_kib: int
) -> tuple[list[str], SearchResults]:
    directory.mkdir(parents=True)
    expected: SearchResults = {keyword: [] for keyword in KEYWORDS}
    files: list[str] = []
    filler = b"Independent files contain text for a reproducible search experiment.\n"
    for index in range(file_count):
        path = str(directory / f"document-{index:04d}.txt")
        selected = KEYWORDS[: index % 4 + 1]
        ending = ("\n" + " ".join(selected).upper() + "\n").encode("utf-8")
        padding = size_kib * 1024 - len(ending)
        content = (filler * (padding // len(filler) + 1))[:padding] + ending
        Path(path).write_bytes(content)
        files.append(path)
        for keyword in selected:
            expected[keyword].append(path)
    return files, expected


def measure(
    mode: str, files: list[str], expected: SearchResults, workers: int
) -> float:
    errors: list[str] = []
    started = perf_counter()
    if mode == "sequential":
        actual = search_sequential(files, KEYWORDS, errors=errors)
    elif mode == "threading":
        actual = search_threaded(files, KEYWORDS, workers, errors=errors)
    else:
        actual = search_multiprocess(files, KEYWORDS, workers, errors=errors)
    elapsed = perf_counter() - started
    if actual != expected or errors:
        raise RuntimeError(f"Некоректний результат benchmark: {mode}: {errors}")
    return elapsed


def benchmark_profile(
    name: str,
    directory: Path,
    file_count: int,
    size_kib: int,
    workers: list[int],
    repeats: int,
) -> list[Measurement]:
    files, expected = generate_dataset(directory, file_count, size_kib)
    jobs = [("sequential", 1)] + [
        (mode, count)
        for mode in ("threading", "multiprocessing")
        for count in dict.fromkeys(workers)
    ]
    samples: dict[tuple[str, int], list[float]] = {job: [] for job in jobs}
    for mode, count in jobs:
        measure(mode, files, expected, count)
    for repeat in range(repeats):
        offset = repeat % len(jobs)
        for mode, count in jobs[offset:] + jobs[:offset]:
            samples[mode, count].append(measure(mode, files, expected, count))
    baseline = statistics.median(samples["sequential", 1])
    total_bytes = sum(Path(path).stat().st_size for path in files)
    results: list[Measurement] = []
    for (mode, count), seconds in samples.items():
        median = statistics.median(seconds)
        results.append(
            {
                "profile": name,
                "files": file_count,
                "bytes": total_bytes,
                "mode": mode,
                "workers": min(count, file_count),
                "seconds": seconds,
                "median_seconds": median,
                "speedup": baseline / median,
            }
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Порівняти пошук на однакових файлах.")
    parser.add_argument("--repeats", type=positive_integer, default=5)
    parser.add_argument(
        "--workers", type=positive_integer, nargs="+", default=[1, 2, 4]
    )
    parser.add_argument(
        "--quick", action="store_true", help="Малий набір для smoke test."
    )
    parser.add_argument("--output", type=Path, default=Path("artifacts/benchmark.json"))
    arguments = parser.parse_args(argv)
    profiles = (
        [("many-small", 8, 1), ("few-large", 2, 8)]
        if arguments.quick
        else [("many-small", 128, 16), ("few-large", 8, 8192)]
    )
    try:
        metadata = provenance(argv)
        measurements: list[Measurement] = []
        with tempfile.TemporaryDirectory(prefix="goit-hw04-") as temporary:
            for name, file_count, size_kib in profiles:
                measurements.extend(
                    benchmark_profile(
                        name,
                        Path(temporary) / name,
                        file_count,
                        size_kib,
                        arguments.workers,
                        arguments.repeats,
                    )
                )
        report = {
            **metadata,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "gil_enabled": sys._is_gil_enabled(),
            "start_method": "spawn",
            "result_transport": "multiprocessing.Manager().Queue",
            "repeats": arguments.repeats,
            "warmups_per_configuration": 1,
            "speedup_definition": {
                "numerator": "sequential median_seconds for the same profile",
                "denominator": "median_seconds of this configuration",
                "greater_than_one": "faster than sequential",
            },
            "measurements": measurements,
        }
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for measurement in measurements:
            print(
                f"{measurement['profile']:12} {measurement['mode']:15} "
                f"workers={measurement['workers']} "
                f"median={measurement['median_seconds']:.6f}s "
                f"speedup={measurement['speedup']:.2f}x"
            )
        print(f"Звіт: {arguments.output}")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Помилка benchmark: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
