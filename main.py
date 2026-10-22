from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter

from multiprocessing_search import search_multiprocess
from search_common import SearchResults, discover_files
from threading_search import search_threaded


def positive_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Очікується додатне ціле число.") from error
    if number < 1:
        raise argparse.ArgumentTypeError("Очікується додатне ціле число.")
    return number


def positive_seconds(value: str) -> float:
    try:
        number = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Очікується додатне число секунд.") from error
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Очікується додатне скінченне число секунд.")
    return number


def build_parser(default_mode: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Паралельний пошук у UTF-8 файлах.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--directory", type=Path, help="Рекурсивно шукати у *.txt.")
    source.add_argument("--files", nargs="+", type=Path, help="Явний список файлів.")
    parser.add_argument(
        "--keywords", nargs="+", default=["python", "thread", "process"]
    )
    parser.add_argument("--workers", type=positive_integer, default=4)
    parser.add_argument(
        "--mode", choices=["threading", "multiprocessing", "both"], default=default_mode
    )
    parser.add_argument("--case-sensitive", action="store_true")
    parser.add_argument("--timeout", type=positive_seconds, default=60.0)
    return parser


def main(argv: Sequence[str] | None = None, *, default_mode: str = "both") -> int:
    parser = build_parser(default_mode)
    arguments = parser.parse_args(argv)
    try:
        files = (
            [str(path) for path in arguments.files]
            if arguments.files is not None
            else discover_files(arguments.directory or Path("data"))
        )
        modes = (
            ["threading", "multiprocessing"]
            if arguments.mode == "both"
            else [arguments.mode]
        )
        print(
            f"Файлів: {len(set(files))}; "
            f"виконавців: {min(arguments.workers, len(set(files)))}"
        )
        outcomes: list[tuple[SearchResults, list[str]]] = []
        has_errors = False
        for mode in modes:
            errors: list[str] = []
            started = perf_counter()
            if mode == "threading":
                matches = search_threaded(
                    files,
                    arguments.keywords,
                    arguments.workers,
                    case_sensitive=arguments.case_sensitive,
                    errors=errors,
                )
            else:
                matches = search_multiprocess(
                    files,
                    arguments.keywords,
                    arguments.workers,
                    case_sensitive=arguments.case_sensitive,
                    errors=errors,
                    timeout=arguments.timeout,
                )
            elapsed = perf_counter() - started
            print(f"\n{mode}: {elapsed:.6f} с")
            print(json.dumps(matches, ensure_ascii=False, indent=2))
            for error in errors:
                print(f"[{mode}] {error}", file=sys.stderr)
            has_errors |= bool(errors)
            outcomes.append((matches, errors))
        if len(outcomes) == 2:
            if outcomes[0] != outcomes[1]:
                print(
                    "Результати відрізняються: перевірте зміни файлів.", file=sys.stderr
                )
                return 1
            print("\nРезультати обох версій збігаються.")
        return int(has_errors)
    except (OSError, EOFError, UnicodeError, ValueError, RuntimeError) as error:
        print(f"Помилка: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nПошук перервано.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
