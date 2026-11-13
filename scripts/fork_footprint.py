#!/usr/bin/env python3
"""Count the upstream files this fork changes, and fail when the count grows.

Every upstream file the fork edits is a place a merge from upstream can
conflict. Fork work belongs in files upstream does not have (``editions/``,
fork-only modules); this script keeps the number of edited upstream files from
creeping back up.

    python3 scripts/fork_footprint.py              # compare with the baseline
    python3 scripts/fork_footprint.py --list       # also print the files
    python3 scripts/fork_footprint.py --update     # record a smaller count

The upstream ref defaults to ``upstream/main``; pass ``--ref`` for another. The
baseline is ``scripts/fork-footprint-baseline.txt`` and only goes down.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASELINE = Path(__file__).with_name("fork-footprint-baseline.txt")


def changed_upstream_files(ref: str) -> list[str]:
    """Files that exist at *ref* and differ in the working tree (edited or deleted)."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=MD", ref],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return sorted(line for line in result.stdout.splitlines() if line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", default="upstream/main", help="the upstream ref to compare with")
    parser.add_argument("--list", action="store_true", help="print each changed upstream file")
    parser.add_argument("--update", action="store_true", help="record the count when it is smaller")
    args = parser.parse_args()

    files = changed_upstream_files(args.ref)
    count = len(files)
    if args.list:
        print("\n".join(files))
    baseline = int(BASELINE.read_text(encoding="utf-8").strip()) if BASELINE.exists() else None
    print(f"upstream files changed by the fork: {count} (baseline: {baseline})")

    if baseline is None or (args.update and count < baseline):
        BASELINE.write_text(f"{count}\n", encoding="utf-8")
        print(f"baseline recorded: {count}")
        return 0
    if count > baseline:
        print(
            f"The fork now changes {count - baseline} more upstream file(s) than the baseline. "
            "Move the change into editions/ or a fork-only module, or run with --list to see them.",
            file=sys.stderr,
        )
        return 1
    if count < baseline:
        print("Fewer than the baseline: run with --update to record it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
