#!/usr/bin/env python3
"""Retired entrypoint: local compatibility workloads cannot be promoted again."""


def main() -> int:
    raise RuntimeError("local release compatibility controller is retired; promotion is disabled")


if __name__ == "__main__":
    raise SystemExit(main())
