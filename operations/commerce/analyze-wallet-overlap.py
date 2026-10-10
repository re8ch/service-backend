#!/usr/bin/env python3
"""Aggregate overlap in frozen Identity and Commerce wallet CSV exports.

The report contains no owner identifiers. Keep source CSVs and output outside
Git because source files contain account identifiers and balances.
"""

import argparse
import csv
import json
from pathlib import Path


def read_wallets(path: Path) -> dict[tuple[str, str], int]:
    wallets: dict[tuple[str, str], int] = {}
    wallet_ids: set[str] = set()
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        expected = {"legacy_wallet_id", "owner_kind", "owner_ref", "source_balance"}
        if set(reader.fieldnames or []) != expected:
            raise ValueError(f"{path}: unexpected wallet CSV columns")
        for line_number, row in enumerate(reader, start=2):
            wallet_id = row["legacy_wallet_id"]
            owner_kind = row["owner_kind"]
            owner_ref = row["owner_ref"]
            if not wallet_id or not owner_ref or owner_kind not in {
                "personal", "organization", "system"
            }:
                raise ValueError(f"{path}:{line_number}: incomplete wallet")
            if wallet_id in wallet_ids:
                raise ValueError(f"{path}:{line_number}: duplicate wallet ID")
            wallet_ids.add(wallet_id)
            key = (owner_kind, owner_ref)
            if key in wallets:
                raise ValueError(f"{path}:{line_number}: duplicate owner")
            try:
                balance = int(row["source_balance"])
            except (TypeError, ValueError) as error:
                raise ValueError(f"{path}:{line_number}: invalid balance") from error
            if str(balance) != row["source_balance"]:
                raise ValueError(f"{path}:{line_number}: noncanonical balance")
            wallets[key] = balance
    return wallets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("identity_csv", type=Path)
    parser.add_argument("commerce_csv", type=Path)
    args = parser.parse_args()
    identity = read_wallets(args.identity_csv)
    commerce = read_wallets(args.commerce_csv)
    identity_owners = {key for key in identity if key[0] != "system"}
    commerce_owners = {key for key in commerce if key[0] != "system"}
    overlap = identity_owners & commerce_owners
    report = {
        "identity_owner_wallets": len(identity_owners),
        "commerce_owner_wallets": len(commerce_owners),
        "overlapping_owners": len(overlap),
        "overlap_equal_balances": sum(identity[key] == commerce[key] for key in overlap),
        "overlap_different_balances": sum(identity[key] != commerce[key] for key in overlap),
        "overlap_identity_balance_sum": sum(identity[key] for key in overlap),
        "overlap_commerce_balance_sum": sum(commerce[key] for key in overlap),
        "identity_only_owners": len(identity_owners - commerce_owners),
        "commerce_only_owners": len(commerce_owners - identity_owners),
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if not overlap else 1


if __name__ == "__main__":
    raise SystemExit(main())
