#!/usr/bin/env python3
"""Build RouterOS address-list and split-routing scripts for RE8CH VPN.

The goal is deliberately conservative: external data is compiled into
MikroTik-native address lists, while RouterOS keeps ownership of routing.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SCRIPT_PATH = Path(__file__).resolve()
ROOT = SCRIPT_PATH.parents[2] if len(SCRIPT_PATH.parents) > 2 else Path("/")
DEFAULT_CONFIG_DIR = ROOT / "config" / "mikrotik-routing"
DEFAULT_OUT_DIR = ROOT / "dist" / "mikrotik"
APNIC_URL = "https://ftp.apnic.net/apnic/stats/apnic/delegated-apnic-latest"
MANAGED_LISTS = [
    "re8ch-cn-ip",
    "re8ch-direct-ip",
    "re8ch-proxy-ip",
    "re8ch-apple-direct-ip",
    "re8ch-ai-ip",
    "re8ch-block-ip",
]
DEFAULT_EMPLOYEE_SOURCE = "192.0.2.170/24"
DEFAULT_CANARY_SOURCE = "192.0.2.5/32"
DEFAULT_ROUTING_MARK = "re8ch-malaysia-egress"


@dataclass(frozen=True)
class Entry:
    list_name: str
    network: ipaddress.IPv4Network | ipaddress.IPv6Network
    comment: str
    source: str


def load_text(url: str, cache_path: Path, refresh: bool) -> str:
    if cache_path.exists() and not refresh:
        return cache_path.read_text()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read().decode("utf-8", "replace")
    cache_path.write_text(data)
    return data


def parse_apnic_cn_ipv4(text: str) -> list[Entry]:
    entries: list[Entry] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) < 7:
            continue
        _registry, country, record_type, start, value, _date, status = parts[:7]
        if country != "CN" or record_type != "ipv4":
            continue
        if status not in {"allocated", "assigned"}:
            continue
        count = int(value)
        if count <= 0 or count & (count - 1):
            # Delegated IPv4 records are normally powers of two. If a source
            # ever violates that, avoid silently producing the wrong prefix.
            raise ValueError(f"APNIC IPv4 count is not a power of two: {line}")
        prefix = 32 - int(math.log2(count))
        network = ipaddress.ip_network(f"{start}/{prefix}", strict=False)
        entries.append(
            Entry("re8ch-cn-ip", network, "APNIC CN IPv4", "apnic delegated")
        )
    return entries


def parse_simple_yaml(path: Path) -> dict[str, list[dict[str, str]]]:
    """Parse the tiny YAML subset used by config/mikrotik-routing/*.yml."""
    result: dict[str, list[dict[str, str]]] = {}
    current_list: str | None = None
    current_item: dict[str, str] | None = None
    key_value = re.compile(r"^([A-Za-z0-9_-]+):(?:\s*(.*))?$")
    item_key_value = re.compile(r"^-\s+([A-Za-z0-9_-]+):\s*(.*)$")
    nested_key_value = re.compile(r"^\s+([A-Za-z0-9_-]+):\s*(.*)$")

    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line:
            continue
        top = key_value.match(line)
        if top and not raw.startswith(" "):
            current_list = top.group(1)
            current_item = None
            value = (top.group(2) or "").strip()
            result[current_list] = [] if value in {"", "[]"} else []
            continue
        item = item_key_value.match(line.strip())
        if item:
            if current_list is None:
                raise ValueError(f"Item without list in {path}: {raw}")
            current_item = {item.group(1): item.group(2).strip().strip('"')}
            result[current_list].append(current_item)
            continue
        nested = nested_key_value.match(raw)
        if nested and current_item is not None:
            current_item[nested.group(1)] = nested.group(2).strip().strip('"')
            continue
        raise ValueError(f"Unsupported YAML subset in {path}: {raw}")
    return result


def load_manual_entries(config_dir: Path) -> list[Entry]:
    entries: list[Entry] = []
    for name in ["direct.yml", "proxy.yml", "block.yml"]:
        path = config_dir / name
        if not path.exists():
            continue
        parsed = parse_simple_yaml(path)
        for list_name, items in parsed.items():
            for item in items:
                address = item.get("address")
                if not address:
                    continue
                network = ipaddress.ip_network(address, strict=False)
                comment = item.get("comment", f"manual {list_name}")
                entries.append(Entry(list_name, network, comment, str(path)))
    return entries


def aggregate_entries(entries: Iterable[Entry]) -> list[Entry]:
    grouped: dict[tuple[str, str, int], list[ipaddress._BaseNetwork]] = {}
    comments: dict[tuple[str, str, int], str] = {}
    sources: dict[tuple[str, str, int], str] = {}
    for entry in entries:
        key = (entry.list_name, entry.comment, entry.network.version)
        grouped.setdefault(key, []).append(entry.network)
        comments[key] = entry.comment
        sources[key] = entry.source

    output: list[Entry] = []
    for key, networks in grouped.items():
        list_name, _comment, _version = key
        collapsed = ipaddress.collapse_addresses(networks)
        for network in collapsed:
            output.append(Entry(list_name, network, comments[key], sources[key]))
    return sorted(output, key=lambda e: (e.list_name, e.network.version, int(e.network.network_address), e.network.prefixlen))


def routeros_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_address_lists(entries: list[Entry], managed_lists: list[str]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# RE8CH generated MikroTik address lists\n",
        f"# Generated: {now}\n",
        "# Source of truth: tools/mikrotik_rules/build_split_lists.py\n",
    ]
    for list_name in managed_lists:
        lines.append(f"/ip/firewall/address-list/remove [find list={routeros_quote(list_name)}]\n")
    for entry in entries:
        if entry.list_name not in managed_lists:
            continue
        lines.append(
            "/ip/firewall/address-list/add "
            f"list={routeros_quote(entry.list_name)} "
            f"address={entry.network} "
            f"comment={routeros_quote(entry.comment)}\n"
        )
    return "".join(lines)


def render_split_routing(source_pool: str, routing_mark: str, profile: str) -> str:
    comment_prefix = f"RE8CH VPN {profile}"
    return f"""# RE8CH generated MikroTik {profile} VPN split-routing policy
# Import only after the address lists are present.
/ip/firewall/mangle/remove [find comment~\"^{comment_prefix}\"]
/ip/firewall/mangle/add chain=prerouting action=drop src-address={source_pool} dst-address-list=re8ch-block-ip comment=\"{comment_prefix} block explicit destinations\"
/ip/firewall/mangle/add chain=prerouting action=mark-routing src-address={source_pool} dst-address-list=re8ch-proxy-ip dst-address-type=!local new-routing-mark={routing_mark} passthrough=no comment=\"{comment_prefix} explicit proxy via Malaysia\"
/ip/firewall/mangle/add chain=prerouting action=mark-routing src-address={source_pool} dst-address-list=re8ch-ai-ip dst-address-type=!local new-routing-mark={routing_mark} passthrough=no comment=\"{comment_prefix} AI proxy via Malaysia\"
/ip/firewall/mangle/add chain=prerouting action=accept src-address={source_pool} dst-address-list=re8ch-direct-ip comment=\"{comment_prefix} explicit direct destinations\"
/ip/firewall/mangle/add chain=prerouting action=accept src-address={source_pool} dst-address-list=re8ch-apple-direct-ip comment=\"{comment_prefix} Apple direct destinations\"
/ip/firewall/mangle/add chain=prerouting action=accept src-address={source_pool} dst-address-list=re8ch-cn-ip comment=\"{comment_prefix} CN direct bypass Malaysia\"
/ip/firewall/mangle/add chain=prerouting action=mark-routing src-address={source_pool} dst-address-list=!re8ch-cn-ip dst-address-type=!local new-routing-mark={routing_mark} passthrough=no comment=\"{comment_prefix} non-CN via Malaysia\"
"""


def render_rollback() -> str:
    return """# RE8CH MikroTik airport policy rollback.
# First remove policy-routing rules. Address lists are passive unless referenced.
/ip/firewall/mangle/remove [find comment~\"^RE8CH VPN canary\"]
/ip/firewall/mangle/remove [find comment~\"^RE8CH VPN employee\"]
"""


def render_import_readme(
    canary_source: str,
    employee_source: str,
    routing_mark: str,
    summary: dict[str, object],
) -> str:
    counts = summary.get("counts", {})
    return f"""# RE8CH MikroTik Manual Import Kit

This kit is intentionally manual. Do not enable RouterOS scheduler during the
canary phase.

## Files

- `re8ch-address-lists.rsc`: managed address lists only.
- `re8ch-split-routing-canary.rsc`: policy routing for `{canary_source}` only.
- `re8ch-split-routing-employee.rsc`: full employee pool policy for `{employee_source}`; do not import during first canary.
- `rollback-canary.rsc`: remove RE8CH canary/employee mangle rules.
- `test-checklist.md`: canary validation steps.

## Current Counts

```json
{json.dumps(counts, indent=2)}
```

## Safe Import Order

```routeros
/import file-name=re8ch-address-lists.rsc
/import file-name=re8ch-split-routing-canary.rsc
```

Do not import `re8ch-split-routing-employee.rsc` until canary testing passes.

## Rollback

```routeros
/import file-name=rollback-canary.rsc
```

## Expected Routing

- Canary source: `{canary_source}`
- Employee pool reserved for later: `{employee_source}`
- Overseas routing mark: `{routing_mark}`
"""


def render_test_checklist(canary_source: str, routing_mark: str) -> str:
    return f"""# RE8CH MikroTik Canary Test Checklist

Canary source: `{canary_source}`
Routing mark: `{routing_mark}`

## Before Import

- Confirm Mac mini can reach the Huawei gateway without depending on MikroTik experimental routing.
- Confirm MikroTik WebFig/WinBox/SSH remains reachable from the management path.
- Confirm `re8ch-malaysia-egress` route table and WireGuard egress are already present.

## Import

1. Import `re8ch-address-lists.rsc`.
2. Import `re8ch-split-routing-canary.rsc`.
3. Do not import `re8ch-split-routing-employee.rsc`.

## Validate From Canary VPN Client

- Internal: `192.0.2.131` is reachable.
- Internal: required LAN resources are reachable.
- Proxy test: `1.1.1.1` and `8.8.8.8` use Malaysia egress.
- CN direct test: mainland China sites use local ISP path.
- Overseas test: non-CN sites use Malaysia egress.
- Management: Mac mini to Huawei gateway still works.

## Rollback

If any management path or canary route fails:

```routeros
/import file-name=rollback-canary.rsc
```
"""


def write_manual_kit(
    out_dir: Path,
    summary: dict[str, object],
    canary_source: str,
    employee_source: str,
    routing_mark: str,
) -> None:
    kit_dir = out_dir / "manual-kit"
    kit_dir.mkdir(parents=True, exist_ok=True)
    for filename in [
        "re8ch-address-lists.rsc",
        "re8ch-split-routing-canary.rsc",
        "re8ch-split-routing-employee.rsc",
        "summary.json",
    ]:
        (kit_dir / filename).write_text((out_dir / filename).read_text())
    (kit_dir / "rollback-canary.rsc").write_text(render_rollback())
    (kit_dir / "README.md").write_text(
        render_import_readme(canary_source, employee_source, routing_mark, summary)
    )
    (kit_dir / "test-checklist.md").write_text(
        render_test_checklist(canary_source, routing_mark)
    )


def build_summary(
    entries: list[Entry],
    out_dir: Path,
    refresh: bool,
    canary_source: str,
    employee_source: str,
    routing_mark: str,
) -> dict[str, object]:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.list_name] = counts.get(entry.list_name, 0) + 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "refresh": refresh,
        "managed_lists": MANAGED_LISTS,
        "canary_source": canary_source,
        "employee_source": employee_source,
        "routing_mark": routing_mark,
        "counts": counts,
        "outputs": {
            "address_lists": str(out_dir / "re8ch-address-lists.rsc"),
            "split_routing_canary": str(out_dir / "re8ch-split-routing-canary.rsc"),
            "split_routing_employee": str(out_dir / "re8ch-split-routing-employee.rsc"),
            "manual_kit": str(out_dir / "manual-kit"),
            "summary": str(out_dir / "summary.json"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--refresh", action="store_true", help="refresh remote sources")
    parser.add_argument("--employee-source", default=DEFAULT_EMPLOYEE_SOURCE)
    parser.add_argument("--canary-source", default=DEFAULT_CANARY_SOURCE)
    parser.add_argument("--routing-mark", default=DEFAULT_ROUTING_MARK)
    args = parser.parse_args()

    cache_dir = args.out_dir / "cache"
    args.out_dir.mkdir(parents=True, exist_ok=True)
    legacy_split = args.out_dir / "re8ch-split-routing.rsc"
    if legacy_split.exists():
        legacy_split.unlink()
    apnic = load_text(APNIC_URL, cache_dir / "delegated-apnic-latest", args.refresh)
    entries = parse_apnic_cn_ipv4(apnic)
    entries.extend(load_manual_entries(args.config_dir))
    entries = aggregate_entries(entries)

    (args.out_dir / "re8ch-address-lists.rsc").write_text(
        render_address_lists(entries, MANAGED_LISTS)
    )
    (args.out_dir / "re8ch-split-routing-canary.rsc").write_text(
        render_split_routing(args.canary_source, args.routing_mark, "canary")
    )
    (args.out_dir / "re8ch-split-routing-employee.rsc").write_text(
        render_split_routing(args.employee_source, args.routing_mark, "employee")
    )
    summary = build_summary(
        entries,
        args.out_dir,
        args.refresh,
        args.canary_source,
        args.employee_source,
        args.routing_mark,
    )
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_manual_kit(
        args.out_dir,
        summary,
        args.canary_source,
        args.employee_source,
        args.routing_mark,
    )

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
