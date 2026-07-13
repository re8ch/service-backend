#!/usr/bin/env python3
"""Replace ZeroTier One's compiled default planet with a supplied planet file."""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
from textwrap import wrap


DEFAULT_WORLD_RE = re.compile(
    r"#define ZT_DEFAULT_WORLD_LENGTH\s+\d+\n"
    r"static const unsigned char ZT_DEFAULT_WORLD\[ZT_DEFAULT_WORLD_LENGTH\]\s*=\s*\{.*?\};",
    re.DOTALL,
)


def git_output(source_dir: pathlib.Path, args: list[str]) -> str:
    return subprocess.check_output(
        ["git", "-C", str(source_dir), *args],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def validate_git_ref(source_dir: pathlib.Path, expected_ref: str | None, expected_commit: str | None) -> None:
    if expected_ref:
        ref = git_output(source_dir, ["describe", "--tags", "--exact-match"])
        if ref != expected_ref:
            raise SystemExit(f"expected git ref {expected_ref!r}, got {ref!r}")

    if expected_commit:
        commit = git_output(source_dir, ["rev-parse", "HEAD"])
        if commit != expected_commit:
            raise SystemExit(f"expected git commit {expected_commit!r}, got {commit!r}")


def c_array(data: bytes) -> str:
    hex_bytes = [f"0x{b:02x}" for b in data]
    lines = wrap(", ".join(hex_bytes), 115)
    body = "\n\t".join(line for line in lines if line)
    return (
        f"#define ZT_DEFAULT_WORLD_LENGTH {len(data)}\n"
        "static const unsigned char ZT_DEFAULT_WORLD[ZT_DEFAULT_WORLD_LENGTH] = {\n"
        f"\t{body}\n"
        "};"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=pathlib.Path)
    parser.add_argument("planet_file", type=pathlib.Path)
    parser.add_argument("--expected-ref")
    parser.add_argument("--expected-commit")
    args = parser.parse_args()

    source_dir = args.source_dir.resolve()
    planet_file = args.planet_file.resolve()
    topology = source_dir / "node" / "Topology.cpp"

    if not topology.exists():
        raise SystemExit(f"missing {topology}")
    if not planet_file.exists():
        raise SystemExit(f"missing {planet_file}")

    validate_git_ref(source_dir, args.expected_ref, args.expected_commit)

    planet = planet_file.read_bytes()
    if not planet:
        raise SystemExit("planet file is empty")

    before = topology.read_text()
    after, count = DEFAULT_WORLD_RE.subn(c_array(planet), before, count=1)
    if count != 1:
        raise SystemExit("failed to locate ZT_DEFAULT_WORLD block in node/Topology.cpp")

    topology.write_text(after)
    print(f"injected {len(planet)} planet bytes into {topology}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
