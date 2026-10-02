#!/usr/bin/env python3
"""Discover existing COS, OSS, and GCS buckets and update rclone aliases."""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from qcloud_cos import CosConfig, CosS3Client


CONFIG = Path.home() / ".config/rclone/rclone.conf"
INVENTORY = Path.home() / ".config/rclone/buckets.json"
START = "# BEGIN MANAGED BUCKET ALIASES"
END = "# END MANAGED BUCKET ALIASES"
COS_PROFILES = ("artc", "default", "suxunji")


def save_private(path: Path, content: str) -> None:
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(content)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main() -> None:
    buckets = []
    for profile in COS_PROFILES:
        path = Path.home() / ".tccli" / f"{profile}.credential"
        if not path.exists():
            continue
        credentials = json.loads(path.read_text())
        client = CosS3Client(CosConfig(
            Region="ap-guangzhou",
            SecretId=credentials["secretId"],
            SecretKey=credentials["secretKey"],
        ))
        result = client.list_buckets()
        for item in (result.get("Buckets") or {}).get("Bucket") or []:
            name, region = item["Name"], item["Location"]
            buckets.append({
                "provider": "cos", "profile": profile, "bucket": name,
                "region": region,
                "source": f"cos_{profile}_{region.replace('-', '_')}:{name}",
                "alias": f"cos_{name.replace('-', '_')}",
            })

    result = subprocess.run(
        ["aliyun", "ossutil", "ls", "--profile", "qwen-prod"],
        check=True, capture_output=True, text=True,
    )
    for match in re.finditer(r"(oss-[a-z0-9-]+)\s+\S+\s+oss://([^\s]+)", result.stdout):
        region, name = match.group(1)[4:], match.group(2)
        buckets.append({
            "provider": "oss", "profile": "qwen-prod", "bucket": name,
            "region": region,
            "source": f"oss_qwen_prod_{region.replace('-', '_')}:{name}",
            "alias": f"oss_{name.replace('-', '_')}",
        })

    result = subprocess.run(
        ["gcloud", "--configuration=rclone-gcs", "storage", "buckets", "list",
         "--project=operations-498408", "--format=json(name,location)"],
        check=True, capture_output=True, text=True,
    )
    for item in json.loads(result.stdout):
        name = item["name"]
        buckets.append({
            "provider": "gcs", "profile": "rclone-gcs", "bucket": name,
            "region": item.get("location", ""), "source": f"gcs:{name}",
            "alias": f"gcs_{name.replace('-', '_')}",
        })

    names = [item["alias"] for item in buckets]
    if len(names) != len(set(names)):
        raise SystemExit("Alias collision; config unchanged")
    sections = [START]
    for item in sorted(buckets, key=lambda entry: entry["alias"]):
        sections += [f'[{item["alias"]}]', "type = alias", f'remote = {item["source"]}', ""]
    sections.append(END)

    current = CONFIG.read_text()
    current = re.sub(r"\n\[test_gcs_alias\]\ntype = alias\nremote = gcs:operations-498408-jnb-20260930\n", "\n", current)
    if START in current:
        before, rest = current.split(START, 1)
        _, after = rest.split(END, 1)
        current = before.rstrip() + "\n\n" + after.lstrip()
    current = current.rstrip() + "\n\n" + "\n".join(sections) + "\n"
    save_private(CONFIG, current)
    save_private(INVENTORY, json.dumps(buckets, ensure_ascii=False, indent=2) + "\n")
    counts = {provider: sum(item["provider"] == provider for item in buckets)
              for provider in ("cos", "oss", "gcs")}
    print("Refreshed bucket aliases:", counts)


if __name__ == "__main__":
    main()
