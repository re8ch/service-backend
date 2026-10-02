#!/usr/bin/env python3
"""Encrypt local rclone cloud identities for the cluster cloud-disk workload.

Run after ~/.local/bin/rclone-cloud-refresh. This script never writes plaintext
credentials inside the repository or prints secret values.
"""

import configparser
import io
import json
import os
import secrets
import subprocess
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
DESTINATION = REPO / "clusters/re8ch/secrets/cloud-disk-credentials.sops.yaml"
LOCAL_CONFIG = Path.home() / ".config/rclone/rclone.conf"
LOCAL_INVENTORY = Path.home() / ".config/rclone/buckets.json"
LOCAL_GUI_AUTH = Path.home() / ".config/rclone/cluster-gui-auth.json"
GCS_KEY = Path.home() / ".config/rclone/credentials/gcs-rclone.json"


def gui_auth() -> dict[str, str]:
    if LOCAL_GUI_AUTH.exists():
        return json.loads(LOCAL_GUI_AUTH.read_text())
    credentials = {"user": "cloud-disk", "pass": secrets.token_urlsafe(36)}
    fd = os.open(LOCAL_GUI_AUTH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(credentials, output)
    return credentials


def cluster_config() -> str:
    config = configparser.RawConfigParser()
    config.read(LOCAL_CONFIG)
    inventory = json.loads(LOCAL_INVENTORY.read_text())
    for item in inventory:
        if not config.has_section(item["alias"]):
            raise ValueError(f'Missing alias: {item["alias"]}')

    for section in config.sections():
        if section.startswith("cos_") and config.get(section, "type", fallback="") == "s3":
            profile = next((name for name in ("artc", "default", "suxunji")
                            if section.startswith(f"cos_{name}_")), None)
            if profile is None:
                raise ValueError(f"Unknown COS profile in {section}")
            credentials = json.loads((Path.home() / ".tccli" / f"{profile}.credential").read_text())
            config.set(section, "env_auth", "false")
            config.set(section, "access_key_id", credentials["secretId"])
            config.set(section, "secret_access_key", credentials["secretKey"])
        if section.startswith("oss_") and config.get(section, "type", fallback="") == "s3":
            profile = "qwen-prod" if section.startswith("oss_qwen_prod_") else "default"
            entries = json.loads((Path.home() / ".aliyun/config.json").read_text())["profiles"]
            credentials = next(item for item in entries if item["name"] == profile)
            config.set(section, "env_auth", "false")
            config.set(section, "access_key_id", credentials["access_key_id"])
            config.set(section, "secret_access_key", credentials["access_key_secret"])
    config.set("gcs", "service_account_file", "/etc/rclone/gcs.json")
    if config.has_section("cephfs"):
        raise ValueError("cephfs remote already exists")
    config.add_section("cephfs")
    config.set("cephfs", "type", "alias")
    config.set("cephfs", "remote", "/workspace")
    output = io.StringIO()
    config.write(output)
    return output.getvalue()


def main() -> None:
    auth = gui_auth()
    plaintext = yaml.safe_dump({
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "cloud-disk-credentials", "namespace": "cloud-disk"},
        "type": "Opaque",
        "stringData": {
            "rclone.conf": cluster_config(),
            "gcs.json": GCS_KEY.read_text(),
            "gui-user": auth["user"],
            "gui-pass": auth["pass"],
        },
    }, sort_keys=False)
    result = subprocess.run(
        ["sops", "encrypt", "--filename-override", str(DESTINATION.relative_to(REPO)),
         "--input-type", "yaml", "--output-type", "yaml"],
        cwd=REPO, input=plaintext, capture_output=True, text=True,
    )
    if result.returncode:
        raise SystemExit("SOPS encryption failed: " + result.stderr[-400:])
    encrypted = result.stdout
    if "sops:" not in encrypted or auth["pass"] in encrypted:
        raise SystemExit("Encrypted output failed validation")
    fd = os.open(DESTINATION, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(encrypted)
    print("Encrypted cloud-disk Secret for", len(json.loads(LOCAL_INVENTORY.read_text())), "bucket aliases")


if __name__ == "__main__":
    main()
