#!/usr/bin/env python3
"""RE8CH VPN identity controller.

The controller is the bridge between Fleet Apple MDM enrollment, Fleet-visible
device status, and MikroTik IKEv2 identity inventory.  It keeps the default
mode conservative: dry-run unless DRY_RUN=false, and no secrets are printed in
state or logs.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import json
import os
import plistlib
import ssl
import sys
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen


APP_NAME = "re8ch-vpn-identity-controller"
CERT_PREFIX = "re8ch-ikev2-client-"
PROFILE_PREFIX = "com.re8ch.fleet.vpn.ikev2."
LABELS = {
    "vpn_active": "RE8CH VPN Active",
    "vpn_pending": "RE8CH VPN Pending",
    "vpn_revoked": "RE8CH VPN Revoked",
    "invite_pending": "RE8CH Invite Pending",
    "review_needed": "RE8CH Review Needed",
}
RE8CH_LABELS = set(LABELS.values())


@dataclass(frozen=True)
class Settings:
    mikrotik_base_url: str
    mikrotik_username: str
    mikrotik_password: str
    fleet_base_url: str | None
    fleet_api_token: str | None
    desired_path: Path
    state_path: Path
    dry_run: bool
    verify_tls: bool
    invite_signing_key: str | None
    public_base_url: str
    fleet_ios_enroll_url_template: str
    status_webclip_url: str


def getenv_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    fleet_base_url = os.getenv("FLEET_BASE_URL", "").rstrip("/") or None
    public_base_url = os.getenv("PUBLIC_BASE_URL", "https://fleet.re8ch.com/re8ch-vpn").rstrip("/")
    enroll_template = os.getenv("FLEET_IOS_ENROLL_URL_TEMPLATE", "")
    if not enroll_template and fleet_base_url:
        enroll_template = f"{fleet_base_url}/api/v1/fleet/enrollment_profiles/ota?{urlencode({'enroll_secret': '{secret}'})}"
    return Settings(
        mikrotik_base_url=os.getenv("MIKROTIK_BASE_URL", "").rstrip("/"),
        mikrotik_username=os.getenv("MIKROTIK_USERNAME", ""),
        mikrotik_password=os.getenv("MIKROTIK_PASSWORD", ""),
        fleet_base_url=fleet_base_url,
        fleet_api_token=os.getenv("FLEET_API_TOKEN") or None,
        desired_path=Path(os.getenv("DESIRED_IDENTITIES_PATH", "/config/identities.json")),
        state_path=Path(os.getenv("STATE_PATH", "/data/state.json")),
        dry_run=getenv_bool("DRY_RUN", True),
        verify_tls=getenv_bool("MIKROTIK_VERIFY_TLS", True),
        invite_signing_key=os.getenv("INVITE_SIGNING_KEY") or None,
        public_base_url=public_base_url,
        fleet_ios_enroll_url_template=enroll_template,
        status_webclip_url=os.getenv("STATUS_WEBCLIP_URL", public_base_url),
    )


def now_ts() -> int:
    return int(time.time())


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text())


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def basic_auth(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def redacted_secret(value: str) -> dict[str, str | int]:
    return {"sha256": sha256_text(value), "length": len(value)}


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: Any = None,
    headers: dict[str, str] | None = None,
    verify_tls: bool = True,
    timeout: int = 20,
) -> Any:
    ctx = None if verify_tls else ssl._create_unverified_context()
    body = None
    req_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode()
        req_headers.setdefault("Content-Type", "application/json")
    req = Request(url, data=body, headers=req_headers, method=method)
    with urlopen(req, timeout=timeout, context=ctx) as resp:
        data = resp.read()
        if not data:
            return None
        content_type = resp.headers.get("content-type", "")
        if "json" not in content_type:
            return {"_raw": data.decode("utf-8", "replace")}
        return json.loads(data)


class RouterOSClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.headers = {
            "Authorization": basic_auth(settings.mikrotik_username, settings.mikrotik_password),
            "Accept": "application/json",
        }

    def get(self, path: str) -> Any:
        if not self.settings.mikrotik_base_url:
            raise RuntimeError("MIKROTIK_BASE_URL is required")
        if not self.settings.mikrotik_username or not self.settings.mikrotik_password:
            raise RuntimeError("MIKROTIK_USERNAME and MIKROTIK_PASSWORD are required")
        return request_json(
            f"{self.settings.mikrotik_base_url}{path}",
            headers=self.headers,
            verify_tls=self.settings.verify_tls,
        )

    def patch(self, path: str, payload: dict[str, Any]) -> Any:
        return request_json(
            f"{self.settings.mikrotik_base_url}{path}",
            method="PATCH",
            payload=payload,
            headers=self.headers,
            verify_tls=self.settings.verify_tls,
        )

    def inventory(self) -> dict[str, Any]:
        certificates = self.get("/rest/certificate")
        identities = self.get("/rest/ip/ipsec/identity")
        return {
            "certificates": certificates if isinstance(certificates, list) else [],
            "ipsec_identities": identities if isinstance(identities, list) else [],
        }

    def disable_re8ch_certificate(self, cert: dict[str, Any]) -> None:
        cert_id = cert.get(".id")
        name = cert.get("name", "")
        if not cert_id or not isinstance(name, str) or not name.startswith(CERT_PREFIX):
            raise RuntimeError(f"refusing to disable non-RE8CH certificate {name!r}")
        self.patch(f"/rest/certificate/{quote(str(cert_id), safe='')}", {"disabled": "yes"})

    def disable_re8ch_identity(self, identity: dict[str, Any]) -> None:
        identity_id = identity.get(".id")
        comment = str(identity.get("comment") or "")
        remote_cert = str(identity.get("remote-certificate") or identity.get("remote-certificate2") or "")
        if not identity_id or not (remote_cert.startswith(CERT_PREFIX) or comment.startswith("RE8CH")):
            raise RuntimeError("refusing to disable non-RE8CH IPsec identity")
        self.patch(f"/rest/ip/ipsec/identity/{quote(str(identity_id), safe='')}", {"disabled": "yes"})


class FleetClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.headers = {"Accept": "application/json"}
        if settings.fleet_api_token:
            self.headers["Authorization"] = f"Bearer {settings.fleet_api_token}"

    def request(self, method: str, path: str, payload: Any = None) -> Any:
        if not self.settings.fleet_base_url or not self.settings.fleet_api_token:
            return None
        try:
            return request_json(
                f"{self.settings.fleet_base_url}{path}",
                method=method,
                payload=payload,
                headers=self.headers,
            )
        except HTTPError as exc:
            if exc.code in {401, 402, 403, 404, 409, 422}:
                return {"_error": exc.code, "_message": exc.read().decode("utf-8", "replace")}
            raise

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, payload: Any = None) -> Any:
        return self.request("POST", path, payload)

    def put(self, path: str, payload: Any = None) -> Any:
        return self.request("PUT", path, payload)

    def patch(self, path: str, payload: Any = None) -> Any:
        return self.request("PATCH", path, payload)

    def delete(self, path: str, payload: Any = None) -> Any:
        return self.request("DELETE", path, payload)

    def inventory(self) -> dict[str, Any]:
        hosts = self.get("/api/latest/fleet/hosts?per_page=500&populate_labels=true&include_device_status=true")
        teams = self.get("/api/latest/fleet/teams")
        labels = self.get("/api/latest/fleet/labels?include_host_counts=false")
        profiles_summary = self.get("/api/latest/fleet/configuration_profiles/summary")
        return {
            "hosts": hosts.get("hosts", []) if isinstance(hosts, dict) else [],
            "teams": teams.get("teams", []) if isinstance(teams, dict) else [],
            "teams_error": teams.get("_error") if isinstance(teams, dict) else None,
            "labels": labels.get("labels", []) if isinstance(labels, dict) else [],
            "profiles_summary": profiles_summary if isinstance(profiles_summary, dict) else {},
        }

    def global_enroll_secrets(self) -> list[str]:
        response = self.get("/api/latest/fleet/spec/enroll_secret")
        if not isinstance(response, dict):
            return []
        entries = response.get("spec", {}).get("secrets") or []
        return [entry["secret"] for entry in entries if isinstance(entry, dict) and entry.get("secret")]

    def set_global_enroll_secrets(self, secrets: list[str]) -> Any:
        payload = {"spec": {"secrets": [{"secret": secret} for secret in sorted(set(secrets))]}}
        return self.post("/api/latest/fleet/spec/enroll_secret", payload)

    def ensure_label(self, name: str, description: str) -> Any:
        labels = self.get("/api/latest/fleet/labels?include_host_counts=false")
        if isinstance(labels, dict):
            for label in labels.get("labels", []):
                if label.get("name") == name:
                    return label
        return self.post("/api/latest/fleet/labels", {"name": name, "description": description, "hosts": []})

    def set_label_hosts(self, name: str, host_ids: list[int]) -> Any:
        labels = self.get("/api/latest/fleet/labels?include_host_counts=false")
        if not isinstance(labels, dict):
            return labels
        label = next((item for item in labels.get("labels", []) if item.get("name") == name), None)
        if not label:
            label = self.ensure_label(name, "RE8CH VPN identity controller managed label")
        label_id = label.get("id")
        if not label_id:
            return {"_error": "missing_label_id", "name": name}
        return self.patch(f"/api/latest/fleet/labels/{label_id}", {"host_ids": sorted(set(host_ids))})

    def update_device_mapping(self, host_id: int, email: str) -> Any:
        return self.put(f"/api/latest/fleet/hosts/{host_id}/device_mapping", {"email": email, "source": "custom"})

    def run_mdm_command(self, host_uuid: str, command_xml: bytes) -> Any:
        command = base64.b64encode(command_xml).decode()
        return self.post("/api/latest/fleet/commands/run", {"command": command, "host_uuids": [host_uuid]})

    def mdm_commands(self, host_id: int) -> list[dict[str, Any]]:
        response = self.get(f"/api/latest/fleet/mdm/commands?host_id={host_id}")
        if not isinstance(response, dict):
            return []
        commands = response.get("commands") or response.get("results") or []
        return commands if isinstance(commands, list) else []


def normalize_serial(value: Any) -> str:
    return str(value or "").strip().upper()


def normalize_user_name(value: str) -> str:
    return "".join(ch for ch in value.lower().strip() if ch.isalnum() or ch in {"-", "_"})


def normalize_desired(raw: Any) -> dict[str, dict[str, Any]]:
    users = raw.get("users", []) if isinstance(raw, dict) else []
    result: dict[str, dict[str, Any]] = {}
    for user in users:
        if not isinstance(user, dict):
            continue
        name = normalize_user_name(str(user.get("name", "")))
        if not name:
            continue
        local_id = str(user.get("local_id") or f"{CERT_PREFIX}{name}")
        serials = [normalize_serial(item) for item in user.get("fleet_host_serials", []) if normalize_serial(item)]
        serial = normalize_serial(user.get("serial"))
        if serial and serial not in serials:
            serials.append(serial)
        result[name] = {
            "name": name,
            "email": str(user.get("email") or "").strip().lower() or None,
            "serial": serial or None,
            "state": user.get("state", "present"),
            "vpn_state": user.get("vpn_state", "present"),
            "invite_expires_at": int(user.get("invite_expires_at") or 0),
            "invite_token": str(user.get("invite_token") or name),
            "local_id": local_id,
            "certificate_name": user.get("certificate_name") or f"{local_id}-v1",
            "fleet_host_serials": serials,
            "vpn_profile_path": user.get("vpn_profile_path"),
            "vpn_profile_b64": user.get("vpn_profile_b64"),
            "vpn_profile_identifier": user.get("vpn_profile_identifier") or f"{PROFILE_PREFIX}{name}.profile",
        }
    return result


def cert_names(inventory: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for cert in inventory.get("certificates", []):
        name = cert.get("name")
        if isinstance(name, str):
            names.add(name)
    return names


def cert_by_name(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(cert.get("name")): cert
        for cert in inventory.get("certificates", [])
        if isinstance(cert, dict) and isinstance(cert.get("name"), str)
    }


def identities_by_remote_cert(inventory: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for identity in inventory.get("ipsec_identities", []):
        remote_cert = identity.get("remote-certificate") or identity.get("remote-certificate2")
        if isinstance(remote_cert, str):
            result.setdefault(remote_cert, []).append(identity)
    return result


def identity_remote_certs(inventory: dict[str, Any]) -> set[str]:
    return set(identities_by_remote_cert(inventory))


def imported_mikrotik_users(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    imported: list[dict[str, Any]] = []
    for cert in inventory.get("certificates", []):
        name = cert.get("name")
        common_name = cert.get("common-name")
        if not isinstance(name, str):
            continue
        if not name.startswith(CERT_PREFIX) and not (
            isinstance(common_name, str) and common_name.startswith(CERT_PREFIX)
        ):
            continue
        local_id = common_name if isinstance(common_name, str) else name.rsplit("-v", 1)[0]
        imported.append(
            {
                "certificate_name": name,
                "local_id": local_id,
                "disabled": cert.get("disabled"),
                "expires_after": cert.get("expires-after"),
                "fingerprint": cert.get("fingerprint"),
            }
        )
    return sorted(imported, key=lambda item: item["certificate_name"])


def host_serial(host: dict[str, Any]) -> str:
    return normalize_serial(host.get("hardware_serial") or host.get("serial_number"))


def host_labels(host: dict[str, Any]) -> set[str]:
    labels = host.get("labels") or []
    return {item.get("name") for item in labels if isinstance(item, dict) and item.get("name")}


def find_user_host(user: dict[str, Any], hosts: list[dict[str, Any]]) -> dict[str, Any] | None:
    serials = {normalize_serial(item) for item in user.get("fleet_host_serials", []) if normalize_serial(item)}
    if user.get("serial"):
        serials.add(normalize_serial(user["serial"]))
    if not serials:
        return None
    return next((host for host in hosts if host_serial(host) in serials), None)


def invite_active(user: dict[str, Any], now: int) -> bool:
    return bool(user.get("email") and user.get("serial") and int(user.get("invite_expires_at") or 0) > now)


def invite_secret(settings: Settings, user: dict[str, Any]) -> str | None:
    if not settings.invite_signing_key:
        return None
    message = "|".join(
        [
            user["name"],
            user.get("email") or "",
            normalize_serial(user.get("serial")),
            str(int(user.get("invite_expires_at") or 0)),
            user.get("invite_token") or user["name"],
        ]
    )
    digest = hmac.new(settings.invite_signing_key.encode(), message.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")[:32]


def enrollment_url(settings: Settings, secret: str) -> str:
    return settings.fleet_ios_enroll_url_template.replace("{secret}", quote(secret))


def invite_url(settings: Settings, user: dict[str, Any]) -> str:
    query = urlencode({"user": user["name"], "token": user.get("invite_token") or user["name"]})
    return f"{settings.public_base_url}/invite?{query}"


def load_profile_bytes(user: dict[str, Any]) -> bytes | None:
    if user.get("vpn_profile_b64"):
        return base64.b64decode(str(user["vpn_profile_b64"]))
    if user.get("vpn_profile_path"):
        return Path(str(user["vpn_profile_path"])).read_bytes()
    return None


def profile_material_available(user: dict[str, Any]) -> bool:
    if user.get("vpn_profile_b64"):
        try:
            base64.b64decode(str(user["vpn_profile_b64"]), validate=True)
            return True
        except Exception:
            return False
    if user.get("vpn_profile_path"):
        return Path(str(user["vpn_profile_path"])).exists()
    return False


def plist_command(request_type: str, payload: dict[str, Any]) -> bytes:
    body = {
        "CommandUUID": f"RE8CH-{request_type}-{uuid.uuid4()}",
        "Command": {"RequestType": request_type, **payload},
    }
    return plistlib.dumps(body, fmt=plistlib.FMT_XML, sort_keys=False)


def install_profile_command(profile: bytes) -> bytes:
    return plist_command("InstallProfile", {"Payload": profile})


def remove_profile_command(identifier: str) -> bytes:
    return plist_command("RemoveProfile", {"Identifier": identifier})


def build_plan(
    settings: Settings,
    desired: dict[str, dict[str, Any]],
    mikrotik: dict[str, Any],
    fleet: dict[str, Any],
    previous_state: dict[str, Any],
) -> list[dict[str, Any]]:
    existing_certs = cert_names(mikrotik)
    existing_identity_certs = identity_remote_certs(mikrotik)
    hosts = fleet.get("hosts", [])
    existing_secret_hashes = set(previous_state.get("managed_enroll_secret_hashes", []))
    fleet_secret_hashes = {sha256_text(secret) for secret in FleetSecretView.from_state(previous_state)}
    if not fleet_secret_hashes:
        fleet_secret_hashes = existing_secret_hashes
    plan: list[dict[str, Any]] = []
    now = now_ts()
    for user in desired.values():
        cert = user["certificate_name"]
        state = user["state"]
        vpn_state = user.get("vpn_state", "present")
        host = find_user_host(user, hosts)
        secret = invite_secret(settings, user)
        if invite_active(user, now) and not host:
            plan.append(
                {
                    "action": "ensure_invite_secret",
                    "user": user["name"],
                    "email": user.get("email"),
                    "serial": user.get("serial"),
                    "invite_url": invite_url(settings, user),
                    "secret": redacted_secret(secret) if secret else None,
                    "blocked": None if secret else "INVITE_SIGNING_KEY is required",
                }
            )
        if state == "present":
            if cert not in existing_certs:
                plan.append({"action": "create_certificate", "user": user["name"], "certificate": cert})
            if cert not in existing_identity_certs:
                plan.append({"action": "create_ipsec_identity", "user": user["name"], "certificate": cert})
            if host:
                plan.append({"action": "bind_fleet_host", "user": user["name"], "host_id": host.get("id"), "serial": host_serial(host)})
                if user.get("email"):
                    plan.append({"action": "set_device_mapping", "user": user["name"], "host_id": host.get("id"), "email": user["email"]})
                if vpn_state == "present":
                    action = "send_vpn_profile" if profile_material_available(user) else "await_vpn_profile_material"
                    plan.append({"action": action, "user": user["name"], "host_id": host.get("id"), "host_uuid": host.get("uuid")})
                elif vpn_state in {"absent", "disabled", "revoked"}:
                    plan.append({"action": "remove_vpn_profile", "user": user["name"], "host_id": host.get("id"), "host_uuid": host.get("uuid")})
        elif state in {"absent", "disabled"}:
            if cert in existing_certs:
                plan.append({"action": "disable_certificate", "user": user["name"], "certificate": cert})
            if cert in existing_identity_certs:
                plan.append({"action": "disable_ipsec_identity", "user": user["name"], "certificate": cert})
            if host:
                plan.append({"action": "remove_vpn_profile", "user": user["name"], "host_id": host.get("id"), "host_uuid": host.get("uuid")})
    if existing_secret_hashes:
        active_hashes = {
            sha256_text(secret)
            for user in desired.values()
            for secret in [invite_secret(settings, user)]
            if secret and invite_active(user, now) and not find_user_host(user, hosts)
        }
        expired = sorted(existing_secret_hashes - active_hashes)
        for secret_hash in expired:
            plan.append({"action": "remove_managed_invite_secret", "secret": {"sha256": secret_hash}})
    return plan


class FleetSecretView:
    @staticmethod
    def from_state(state: dict[str, Any]) -> list[str]:
        # Plain secrets are intentionally not persisted. This method exists for
        # backwards compatibility if an early local state file had them.
        return [item for item in state.get("managed_enroll_secrets", []) if isinstance(item, str)]


def classify_user_label(user: dict[str, Any], host: dict[str, Any] | None, mikrotik: dict[str, Any]) -> str:
    if user.get("state") in {"absent", "disabled"} or user.get("vpn_state") in {"absent", "disabled", "revoked"}:
        return LABELS["vpn_revoked"]
    if not host:
        return LABELS["invite_pending"] if user.get("serial") and user.get("email") else LABELS["review_needed"]
    cert = user.get("certificate_name")
    has_cert = cert in cert_names(mikrotik)
    has_identity = cert in identity_remote_certs(mikrotik)
    if has_cert and has_identity and user.get("vpn_state", "present") == "present":
        return LABELS["vpn_active"]
    return LABELS["vpn_pending"]


def sync_fleet_enroll_secrets(
    settings: Settings,
    fleet_client: FleetClient,
    desired: dict[str, dict[str, Any]],
    hosts: list[dict[str, Any]],
    previous_state: dict[str, Any],
) -> dict[str, Any]:
    now = now_ts()
    current = fleet_client.global_enroll_secrets()
    previous_managed_hashes = set(previous_state.get("managed_enroll_secret_hashes", []))
    active_managed: list[str] = []
    for user in desired.values():
        secret = invite_secret(settings, user)
        if secret and invite_active(user, now) and not find_user_host(user, hosts):
            active_managed.append(secret)
    active_hashes = {sha256_text(secret) for secret in active_managed}
    kept_current = [
        secret
        for secret in current
        if sha256_text(secret) not in previous_managed_hashes or sha256_text(secret) in active_hashes
    ]
    target = sorted(set(kept_current + active_managed))
    changed = set(target) != set(current)
    if changed and not settings.dry_run:
        fleet_client.set_global_enroll_secrets(target)
    return {
        "changed": changed,
        "current_count": len(current),
        "target_count": len(target),
        "managed_enroll_secret_hashes": sorted(active_hashes),
    }


def sync_fleet_visibility(
    settings: Settings,
    fleet_client: FleetClient,
    desired: dict[str, dict[str, Any]],
    mikrotik: dict[str, Any],
    hosts: list[dict[str, Any]],
) -> dict[str, Any]:
    target_hosts: dict[str, list[int]] = {label: [] for label in RE8CH_LABELS}
    mapping_updates: list[dict[str, Any]] = []
    for user in desired.values():
        host = find_user_host(user, hosts)
        label = classify_user_label(user, host, mikrotik)
        if host and isinstance(host.get("id"), int):
            target_hosts[label].append(host["id"])
            if user.get("email"):
                mapping_updates.append({"host_id": host["id"], "email": user["email"], "user": user["name"]})
    results: list[dict[str, Any]] = []
    if not settings.dry_run:
        for label in RE8CH_LABELS:
            fleet_client.ensure_label(label, "Managed by RE8CH VPN identity controller.")
        for label, host_ids in target_hosts.items():
            results.append({"label": label, "result": fleet_client.set_label_hosts(label, host_ids)})
        for update in mapping_updates:
            results.append(
                {
                    "device_mapping": {"host_id": update["host_id"], "email": update["email"]},
                    "result": fleet_client.update_device_mapping(update["host_id"], update["email"]),
                }
            )
    return {
        "target_label_hosts": target_hosts,
        "mapping_updates": mapping_updates,
        "applied": not settings.dry_run,
        "results": results,
    }


def sync_vpn_profiles(
    settings: Settings,
    fleet_client: FleetClient,
    desired: dict[str, dict[str, Any]],
    hosts: list[dict[str, Any]],
    previous_state: dict[str, Any],
) -> dict[str, Any]:
    sent_commands = dict(previous_state.get("sent_vpn_commands", {}))
    actions: list[dict[str, Any]] = []
    for user in desired.values():
        host = find_user_host(user, hosts)
        if not host or not host.get("uuid"):
            continue
        key = f"{user['name']}:{host['uuid']}:{user.get('vpn_state', 'present')}"
        if user.get("vpn_state", "present") in {"absent", "disabled", "revoked"}:
            if sent_commands.get(key):
                continue
            command = remove_profile_command(user["vpn_profile_identifier"])
            action = {"action": "remove_vpn_profile", "user": user["name"], "host_id": host.get("id"), "host_uuid": host["uuid"]}
        else:
            if not profile_material_available(user):
                continue
            profile = load_profile_bytes(user)
            if not profile:
                continue
            profile_hash = sha256_text(base64.b64encode(profile).decode())
            key = f"{user['name']}:{host['uuid']}:install:{profile_hash}"
            if sent_commands.get(key):
                continue
            command = install_profile_command(profile)
            action = {"action": "send_vpn_profile", "user": user["name"], "host_id": host.get("id"), "host_uuid": host["uuid"], "profile_hash": profile_hash}
        if settings.dry_run:
            action["dry_run"] = True
        else:
            result = fleet_client.run_mdm_command(str(host["uuid"]), command)
            action["result"] = result
            if isinstance(result, dict) and result.get("command_uuid"):
                sent_commands[key] = result["command_uuid"]
        actions.append(action)
    return {"actions": actions, "sent_vpn_commands": sent_commands}


def sync_mikrotik_revocations(
    settings: Settings,
    routeros: RouterOSClient,
    desired: dict[str, dict[str, Any]],
    mikrotik: dict[str, Any],
) -> dict[str, Any]:
    certs = cert_by_name(mikrotik)
    identities = identities_by_remote_cert(mikrotik)
    actions: list[dict[str, Any]] = []
    for user in desired.values():
        if user.get("state") not in {"absent", "disabled"} and user.get("vpn_state") not in {"absent", "disabled", "revoked"}:
            continue
        cert_name = user.get("certificate_name")
        cert = certs.get(cert_name)
        if cert:
            action = {"action": "disable_certificate", "certificate": cert_name, "user": user["name"]}
            if not settings.dry_run:
                routeros.disable_re8ch_certificate(cert)
            else:
                action["dry_run"] = True
            actions.append(action)
        for identity in identities.get(cert_name, []):
            action = {"action": "disable_ipsec_identity", "certificate": cert_name, "user": user["name"], "identity": identity.get(".id")}
            if not settings.dry_run:
                routeros.disable_re8ch_identity(identity)
            else:
                action["dry_run"] = True
            actions.append(action)
    return {"actions": actions}


def public_users(desired: dict[str, dict[str, Any]], settings: Settings, hosts: list[dict[str, Any]], mikrotik: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, user in desired.items():
        host = find_user_host(user, hosts)
        secret = invite_secret(settings, user)
        result[name] = {
            "name": name,
            "email": user.get("email"),
            "serial": user.get("serial"),
            "state": user.get("state"),
            "vpn_state": user.get("vpn_state"),
            "local_id": user.get("local_id"),
            "certificate_name": user.get("certificate_name"),
            "invite_expires_at": user.get("invite_expires_at"),
            "invite_url": invite_url(settings, user) if invite_active(user, now_ts()) and not host else None,
            "invite_secret": redacted_secret(secret) if secret else None,
            "fleet_host": {
                "id": host.get("id"),
                "uuid": host.get("uuid"),
                "serial": host_serial(host),
                "platform": host.get("platform"),
                "mdm": host.get("mdm", {}).get("enrollment_status") if isinstance(host.get("mdm"), dict) else None,
            }
            if host
            else None,
            "fleet_label": classify_user_label(user, host, mikrotik),
        }
    return result


def reconcile_once(settings: Settings) -> dict[str, Any]:
    previous_state = read_json(settings.state_path, {})
    desired = normalize_desired(read_json(settings.desired_path, {"users": []}))
    routeros = RouterOSClient(settings)
    mikrotik = routeros.inventory()
    fleet_client = FleetClient(settings)
    fleet = fleet_client.inventory()
    hosts = fleet.get("hosts", [])
    plan = build_plan(settings, desired, mikrotik, fleet, previous_state)
    enroll_sync = sync_fleet_enroll_secrets(settings, fleet_client, desired, hosts, previous_state)
    visibility_sync = sync_fleet_visibility(settings, fleet_client, desired, mikrotik, hosts)
    profile_sync = sync_vpn_profiles(settings, fleet_client, desired, hosts, previous_state)
    mikrotik_sync = sync_mikrotik_revocations(settings, routeros, desired, mikrotik)
    state = {
        "service": APP_NAME,
        "generated_at": now_ts(),
        "mode": "dry-run" if settings.dry_run else "apply",
        "desired_users": public_users(desired, settings, hosts, mikrotik),
        "mikrotik_imported_users": imported_mikrotik_users(mikrotik),
        "mikrotik_counts": {
            "certificates": len(mikrotik.get("certificates", [])),
            "ipsec_identities": len(mikrotik.get("ipsec_identities", [])),
        },
        "fleet_counts": {
            "hosts": len(hosts),
            "teams": len(fleet.get("teams", [])),
            "labels": len(fleet.get("labels", [])),
        },
        "fleet_profiles_summary": fleet.get("profiles_summary", {}),
        "managed_enroll_secret_hashes": enroll_sync["managed_enroll_secret_hashes"],
        "sent_vpn_commands": profile_sync["sent_vpn_commands"],
        "plan": plan,
        "sync": {
            "enroll_secrets": enroll_sync,
            "visibility": visibility_sync,
            "vpn_profiles": profile_sync,
            "mikrotik_revocations": mikrotik_sync,
        },
        "limits": [
            "Fleet Free cannot safely scope per-user Apple configuration profiles through Controls.",
            "Manually enrolled iOS hosts do not provide fleetd/osquery software, policy, disk, or IP telemetry.",
            "Per-user VPN profiles are sent by targeted MDM command only when profile material is supplied.",
        ],
    }
    write_json(settings.state_path, state)
    return state


class HealthHandler(BaseHTTPRequestHandler):
    server_version = APP_NAME

    def do_GET(self) -> None:
        state_path: Path = self.server.state_path  # type: ignore[attr-defined]
        settings: Settings = self.server.settings  # type: ignore[attr-defined]
        if self.path in {"/healthz", "/readyz"}:
            self.respond_json(200, {"ok": True})
            return
        if self.path in {"/", "/state.json"}:
            self.respond_json(200, read_json(state_path, {"service": APP_NAME, "status": "no state yet"}))
            return
        parsed = urlparse(self.path)
        if parsed.path == "/invite" or parsed.path.endswith("/invite"):
            self.respond_invite(settings, read_json(settings.desired_path, {"users": []}), parsed.query)
            return
        self.respond_json(404, {"error": "not found"})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(fmt % args, file=sys.stderr)

    def respond_json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode() + b"\n"
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def respond_html(self, code: int, body: str) -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def respond_invite(self, settings: Settings, raw_desired: Any, query: str) -> None:
        params = parse_qs(query)
        user_name = normalize_user_name(params.get("user", [""])[0])
        token = params.get("token", [""])[0]
        desired = normalize_desired(raw_desired)
        user = desired.get(user_name)
        if not user or token != user.get("invite_token") or not invite_active(user, now_ts()):
            self.respond_html(404, "<h1>RE8CH invite unavailable</h1><p>This invite is invalid or expired.</p>")
            return
        secret = invite_secret(settings, user)
        if not secret:
            self.respond_html(503, "<h1>Invite not ready</h1><p>INVITE_SIGNING_KEY is not configured.</p>")
            return
        link = enrollment_url(settings, secret)
        safe_email = html.escape(user.get("email") or "")
        safe_serial = html.escape(user.get("serial") or "")
        safe_link = html.escape(link, quote=True)
        self.respond_html(
            200,
            f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>RE8CH iOS Enrollment</title></head>
<body>
<h1>RE8CH iOS Enrollment</h1>
<p>Email: <strong>{safe_email}</strong></p>
<p>Expected serial: <strong>{safe_serial}</strong></p>
<p>Open this page on the iPhone whose serial matches the value above, then install the Fleet MDM profile.</p>
<p><a href="{safe_link}">Download Fleet enrollment profile</a></p>
<p>This link is one-time and expires automatically.</p>
</body></html>""",
        )


def serve(settings: Settings, host: str, port: int) -> int:
    server = ThreadingHTTPServer((host, port), HealthHandler)
    server.state_path = settings.state_path  # type: ignore[attr-defined]
    server.settings = settings  # type: ignore[attr-defined]
    print(f"serving {settings.state_path} on {host}:{port}", flush=True)
    server.serve_forever()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["sync", "serve"])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    settings = load_settings()
    try:
        if args.command == "sync":
            state = reconcile_once(settings)
            print(json.dumps(state, indent=2, sort_keys=True))
            return 0
        return serve(settings, args.host, args.port)
    except (HTTPError, URLError, TimeoutError, RuntimeError, ssl.SSLError, OSError) as exc:
        print(f"{APP_NAME}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
