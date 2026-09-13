from __future__ import annotations

import json
import hashlib
import hmac
import os
import smtplib
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from .model import ChannelCapabilities


CHANNEL_CAPABILITIES = {
    "imessage": ChannelCapabilities(True, True, True, True, True, True, True, "macmini"),
    "phone": ChannelCapabilities(True, True, False, False, True, True, True, "r640-voice"),
    "human_delegate": ChannelCapabilities(True, False, False, False, True, True, True, "human-delegate"),
    "discord": ChannelCapabilities(True, True, True, False, True, True, True, "discord"),
    "wechat": ChannelCapabilities(True, True, False, False, True, True, True, "wechat-business"),
    "email": ChannelCapabilities(True, True, False, False, True, True, True, "stalwart"),
}


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None, timeout: int = 10) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


@dataclass
class BlueBubblesAdapter:
    base_url: str
    password: str

    def send(self, chat_guid: str, text: str) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/api/v1/message/text?{urllib.parse.urlencode({'password': self.password})}"
        result = post_json(url, {"chatGuid": chat_guid, "message": text, "method": "apple-script"})
        if int(result.get("status", 500)) >= 300:
            raise RuntimeError(f"BlueBubbles rejected message: {result.get('message', 'unknown error')}")
        return result


@dataclass
class VoiceGatewayAdapter:
    base_url: str
    secret: bytes

    def call(self, request_id: str, destination: str, tenant: str, expires_at: int, agents: list[str] | None = None,
             incident_id: str = "", recipient_id: str = "", destination_verified: bool = False) -> dict[str, Any]:
        import hashlib, hmac, time
        payload = {"request_id": request_id, "destination": destination, "tenant": tenant, "expires_at": expires_at, "agents": agents or [],
                   "incident_id": incident_id, "recipient_id": recipient_id, "destination_verified": destination_verified}
        raw = json.dumps(payload, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(self.secret, timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/v1/voice/calls", data=raw, method="POST",
            headers={"Content-Type": "application/json", "X-Voice-Timestamp": timestamp, "X-Voice-Signature": signature},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())


@dataclass
class EmailAdapter:
    host: str
    port: int
    username: str
    password: str
    sender: str
    starttls: bool = True
    tls_fingerprint_sha256: str = ""

    def send(self, *, recipient: str, subject: str, text: str, message_id: str) -> dict[str, Any]:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = recipient
        message["Subject"] = subject
        message["Message-ID"] = message_id
        message["Auto-Submitted"] = "auto-generated"
        message.set_content(text)
        with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
            smtp.ehlo()
            if self.starttls:
                context = ssl.create_default_context()
                if self.tls_fingerprint_sha256:
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                smtp.starttls(context=context)
                if self.tls_fingerprint_sha256:
                    certificate = smtp.sock.getpeercert(binary_form=True)
                    actual = hashlib.sha256(certificate).hexdigest()
                    if not hmac.compare_digest(actual, self.tls_fingerprint_sha256.lower()):
                        raise ssl.SSLError("SMTP TLS certificate fingerprint mismatch")
                smtp.ehlo()
            if self.username:
                smtp.login(self.username, self.password)
            refused = smtp.send_message(message)
        if refused:
            raise RuntimeError("SMTP refused one or more recipients")
        return {"provider": "stalwart", "message_id": message_id, "accepted": True}
