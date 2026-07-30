#!/usr/bin/env python3
"""Build a private, per-device RE8CH Windows IKEv2 MSI with WiX v3."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import uuid


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE_DIR = pathlib.Path(__file__).resolve().parent / "templates"
DEFAULT_ROUTES = (
    "192.0.2.18/24",
    "192.0.2.11/24",
    "192.0.2.212/23",
    "192.0.2.20/24",
)
DEVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--profile-mode", choices=("management", "egress"), default="management")
    parser.add_argument("--vpn-name")
    parser.add_argument("--cert-common-name")
    parser.add_argument("--pfx", type=pathlib.Path, required=True)
    parser.add_argument("--pfx-password-file", type=pathlib.Path, required=True)
    parser.add_argument("--ca", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--route", action="append", dest="routes")
    parser.add_argument("--docker", default="/usr/local/bin/docker")
    return parser.parse_args()


def render(template_name: str, replacements: dict[str, str]) -> str:
    text = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace(f"@@{key}@@", value)
    unresolved = sorted(set(re.findall(r"@@[A-Z0-9_]+@@", text)))
    if unresolved:
        raise RuntimeError(f"unresolved template variables in {template_name}: {unresolved}")
    return text


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    if not DEVICE_ID_RE.fullmatch(args.device_id):
        raise SystemExit("--device-id must contain only lowercase letters, digits, and hyphens")
    if not VERSION_RE.fullmatch(args.version):
        raise SystemExit("--version must be a three-part numeric MSI version")

    pfx = args.pfx.resolve()
    ca = args.ca.resolve()
    password_file = args.pfx_password_file.resolve()
    for required in (pfx, ca, password_file):
        if not required.is_file():
            raise SystemExit(f"missing required input: {required}")

    pfx_password = password_file.read_text(encoding="utf-8").strip()
    if not pfx_password:
        raise SystemExit("PKCS#12 password file is empty")

    cert_common_name = args.cert_common_name or f"re8ch-ikev2-client-{args.device_id}-v1"
    vpn_name = args.vpn_name or (
        "RE8CH Egress" if args.profile_mode == "egress" else "RE8CH Management"
    )
    routes = tuple(args.routes or (() if args.profile_mode == "egress" else DEFAULT_ROUTES))
    route_lines = ",\n".join(f'    "{route}"' for route in routes)
    upgrade_code = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"re8ch-vpn-msi:{args.device_id}")).upper()
    component_guid = str(
        uuid.uuid5(uuid.NAMESPACE_DNS, f"re8ch-vpn-msi-component:{args.device_id}")
    ).upper()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_stem = f"RE8CH-VPN-{args.device_id}-{args.version}"
    output_msi = output_dir / f"{output_stem}.msi"
    output_manifest = output_dir / f"{output_stem}.manifest.json"
    output_checksum = output_dir / f"{output_stem}.sha256"

    manifest = {
        "schemaVersion": 1,
        "deviceId": args.device_id,
        "displayName": args.display_name,
        "certificateCommonName": cert_common_name,
        "profileMode": args.profile_mode,
        "vpnName": vpn_name,
        "serverAddress": "vpn.re8ch.com",
        "splitTunneling": args.profile_mode == "management",
        "fleetSafetyWatchdog": args.profile_mode == "egress",
        "automaticConnection": False,
        "routes": routes,
        "version": args.version,
        "upgradeCode": upgrade_code,
        "componentGuid": component_guid,
        "sourcePfxSha256": sha256(pfx),
        "sourceCaSha256": sha256(ca),
    }

    with tempfile.TemporaryDirectory(prefix=f"re8ch-vpn-msi-{args.device_id}-") as temp_name:
        build_dir = pathlib.Path(temp_name)
        payload_dir = build_dir / "payload"
        payload_dir.mkdir()
        shutil.copy2(pfx, payload_dir / "device-identity.p12")
        shutil.copy2(ca, payload_dir / "re8ch-ikev2-ca.crt")

        ps_replacements = {
            "CERT_COMMON_NAME": cert_common_name.replace("'", "''"),
            "DEVICE_DISPLAY_NAME": args.display_name.replace("'", "''"),
            "PFX_PASSWORD": pfx_password.replace('"', '`"').replace("$", "`$"),
            "ROUTES": route_lines,
            "VPN_NAME": vpn_name.replace("'", "''"),
            "SPLIT_TUNNEL_ARGUMENT": (
                "-SplitTunneling `\n        " if args.profile_mode == "management" else ""
            ),
            "WATCHDOG_INSTALL": (
                """
    $WatchdogPath = Join-Path $InstallDirectory "Watchdog.ps1"
    $WatchdogAction = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$WatchdogPath`""
    $WatchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 1)
    $WatchdogPrincipal = New-ScheduledTaskPrincipal `
        -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask `
        -TaskName "RE8CH Egress Safety Watchdog" `
        -Action $WatchdogAction `
        -Trigger $WatchdogTrigger `
        -Principal $WatchdogPrincipal `
        -Force | Out-Null
"""
                if args.profile_mode == "egress"
                else ""
            ),
            "WATCHDOG_REMOVE": (
                """
    Unregister-ScheduledTask `
        -TaskName "RE8CH Egress Safety Watchdog" `
        -Confirm:$false `
        -ErrorAction SilentlyContinue
"""
                if args.profile_mode == "egress"
                else ""
            ),
        }
        (payload_dir / "Install.ps1").write_text(
            render("Install.ps1.in", ps_replacements), encoding="utf-8"
        )
        (payload_dir / "Test.ps1").write_text(
            render(
                "Test.ps1.in",
                {
                    "ROUTES": route_lines,
                    "VPN_NAME": vpn_name.replace("'", "''"),
                    "PROFILE_MODE": args.profile_mode,
                },
            ),
            encoding="utf-8",
        )
        (payload_dir / "Watchdog.ps1").write_text(
            render("Watchdog.ps1.in", {"VPN_NAME": vpn_name.replace("'", "''")}),
            encoding="utf-8",
        )
        (payload_dir / "Uninstall.ps1").write_text(
            render(
                "Uninstall.ps1.in",
                {
                    "CERT_COMMON_NAME": cert_common_name.replace("'", "''"),
                    "VPN_NAME": vpn_name.replace("'", "''"),
                    "WATCHDOG_REMOVE": ps_replacements["WATCHDOG_REMOVE"],
                },
            ),
            encoding="utf-8",
        )
        (payload_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (build_dir / "Product.wxs").write_text(
            render(
                "Product.wxs.in",
                {
                    "DEVICE_DISPLAY_NAME_XML": html.escape(args.display_name, quote=True),
                    "DEVICE_ID": args.device_id,
                    "COMPONENT_GUID": component_guid,
                    "UPGRADE_CODE": upgrade_code,
                    "VERSION": args.version,
                },
            ),
            encoding="utf-8",
        )

        docker = pathlib.Path(args.docker)
        if not docker.is_file():
            raise SystemExit(f"Docker executable not found: {docker}")

        container_script = (
            "set -e; "
            "wine /home/wine/wix/candle.exe -nologo -out Z:\\\\work\\\\Product.wixobj Z:\\\\work\\\\Product.wxs; "
            "wine /home/wine/wix/light.exe -nologo -sval "
            "-out Z:\\\\work\\\\output.msi Z:\\\\work\\\\Product.wixobj"
        )
        subprocess.run(
            [
                str(docker),
                "run",
                "--rm",
                "--platform",
                "linux/amd64",
                "-v",
                f"{build_dir}:/work",
                "fleetdm/wix:latest",
                "bash",
                "-lc",
                container_script,
            ],
            check=True,
        )
        shutil.copy2(build_dir / "output.msi", output_msi)
        output_msi.chmod(0o600)

    msi_hash = sha256(output_msi)
    manifest["msiSha256"] = msi_hash
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    output_checksum.write_text(f"{msi_hash}  {output_msi.name}\n", encoding="utf-8")
    output_manifest.chmod(0o600)
    output_checksum.chmod(0o600)

    print(output_msi)
    print(output_manifest)
    print(output_checksum)


if __name__ == "__main__":
    main()
