#!/usr/bin/env python3
"""Authenticated control plane for the restricted-node-6dc3f140f9 FreeSWITCH voice gateway."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import socket
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")
AGENT_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")
MAX_AGENTS = 4


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class ESLClient:
    def __init__(self, host: str, port: int, password: str):
        self.host, self.port, self.password = host, port, password

    @staticmethod
    def _frame(sock: socket.socket) -> tuple[dict[str, str], bytes]:
        buf = b""
        while b"\n\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise RuntimeError("ESL closed before headers")
            buf += chunk
        raw_headers, body = buf.split(b"\n\n", 1)
        headers = {}
        for line in raw_headers.decode(errors="replace").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        while len(body) < length:
            body += sock.recv(length - len(body))
        return headers, body[:length]

    def command(self, command: str, verb: str = "api") -> str:
        with socket.create_connection((self.host, self.port), timeout=5) as sock:
            headers, _ = self._frame(sock)
            if headers.get("content-type") != "auth/request":
                raise RuntimeError("unexpected ESL greeting")
            sock.sendall(f"auth {self.password}\n\n".encode())
            headers, body = self._frame(sock)
            if b"+OK accepted" not in body and "+OK accepted" not in headers.get("reply-text", ""):
                raise RuntimeError("ESL authentication failed")
            sock.sendall(f"{verb} {command}\n\n".encode())
            _, body = self._frame(sock)
            return body.decode(errors="replace").strip()

    def api(self, command: str) -> str:
        return self.command(command, "api")

    def bgapi(self, command: str) -> str:
        return self.command(command, "bgapi")


@dataclass
class Call:
    call_id: str
    request_id: str
    tenant: str
    destination: str
    line: str
    agents: list[str]
    status: str
    created_at: int
    updated_at: int
    fs_uuid: str = ""
    failure: str = ""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS calls (
          call_id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, tenant TEXT NOT NULL,
          destination TEXT NOT NULL, line TEXT NOT NULL, agents TEXT NOT NULL,
          status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
          fs_uuid TEXT NOT NULL DEFAULT '', failure TEXT NOT NULL DEFAULT '')""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT NOT NULL, type TEXT NOT NULL,
          source TEXT NOT NULL, idempotency_key TEXT UNIQUE NOT NULL,
          created_at INTEGER NOT NULL, payload TEXT NOT NULL)""")
        self.db.commit()
        self.lock = threading.Lock()

    def find_request(self, request_id: str) -> Call | None:
        row = self.db.execute("SELECT * FROM calls WHERE request_id=?", (request_id,)).fetchone()
        return self._call(row) if row else None

    def get(self, call_id: str) -> Call:
        row = self.db.execute("SELECT * FROM calls WHERE call_id=?", (call_id,)).fetchone()
        if not row:
            raise ApiError(404, "call not found")
        return self._call(row)

    @staticmethod
    def _call(row) -> Call:
        return Call(row[0], row[1], row[2], row[3], row[4], json.loads(row[5]), row[6], row[7], row[8], row[9], row[10])

    def create(self, call: Call) -> None:
        with self.lock:
            self.db.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                call.call_id, call.request_id, call.tenant, call.destination, call.line,
                json.dumps(call.agents), call.status, call.created_at, call.updated_at,
                call.fs_uuid, call.failure))
            self.db.commit()

    def update(self, call: Call) -> None:
        with self.lock:
            self.db.execute("UPDATE calls SET agents=?,status=?,updated_at=?,fs_uuid=?,failure=? WHERE call_id=?", (
                json.dumps(call.agents), call.status, call.updated_at, call.fs_uuid, call.failure, call.call_id))
            self.db.commit()

    def event(self, call_id: str, kind: str, source: str, payload: dict, idem: str | None = None) -> None:
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO events(call_id,type,source,idempotency_key,created_at,payload) VALUES(?,?,?,?,?,?)", (
                call_id, kind, source, idem or str(uuid.uuid4()), int(time.time()), json.dumps(payload)))
            self.db.commit()

    def events(self, after: int) -> list[dict]:
        rows = self.db.execute("SELECT seq,call_id,type,source,created_at,payload FROM events WHERE seq>? ORDER BY seq LIMIT 200", (after,)).fetchall()
        return [{"seq": r[0], "call_id": r[1], "type": r[2], "source": r[3], "created_at": r[4], "payload": json.loads(r[5])} for r in rows]


class App:
    def __init__(self):
        self.secret = os.environ["VOICE_API_HMAC_SECRET"].encode()
        self.store = Store(Path(os.getenv("VOICE_DB", "/var/lib/re8ch-voice/voice.db")))
        self.esl = ESLClient("127.0.0.1", 8021, os.environ["FREESWITCH_ESL_PASSWORD"])
        self.allowed_tenants = set(filter(None, os.getenv("VOICE_ALLOWED_TENANTS", "").split(",")))
        self.allowed_lines = set(f"line{i:02d}" for i in range(1, 9))

    def background(self, command: str) -> str:
        return getattr(self.esl, "bgapi", self.esl.api)(command)

    @staticmethod
    def agent_uuid(call_id: str, agent: str) -> str:
        return str(uuid.uuid5(uuid.UUID(call_id), agent))

    def join_agent_media(self, call: Call, agent: str) -> None:
        leg_uuid = self.agent_uuid(call.call_id, agent)
        variables = f"origination_uuid={leg_uuid},voice_call_id={call.call_id},voice_agent_id={agent},absolute_codec_string=PCMA"
        result = self.background(f"originate {{{variables}}}user/{agent}@voice.internal &conference({call.call_id}@voice)")
        if result.startswith("-ERR"):
            raise RuntimeError(result)

    def authenticate(self, headers, body: bytes) -> None:
        timestamp = headers.get("X-Voice-Timestamp", "")
        signature = headers.get("X-Voice-Signature", "")
        if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > 300:
            raise ApiError(401, "expired or missing timestamp")
        expected = hmac.new(self.secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ApiError(401, "invalid signature")

    def create_call(self, data: dict) -> Call:
        required = {"request_id", "destination", "tenant", "expires_at"}
        if not required.issubset(data):
            raise ApiError(400, "missing required field")
        if int(data["expires_at"]) < time.time():
            raise ApiError(403, "authorization expired")
        if not PHONE_RE.fullmatch(str(data["destination"])):
            raise ApiError(400, "invalid destination")
        tenant = str(data["tenant"])
        if self.allowed_tenants and tenant not in self.allowed_tenants:
            raise ApiError(403, "tenant not allowed")
        agents = list(dict.fromkeys(data.get("agents", [])))
        if len(agents) > MAX_AGENTS or any(not AGENT_RE.fullmatch(str(a)) for a in agents):
            raise ApiError(400, "invalid agents or more than four")
        existing = self.store.find_request(str(data["request_id"]))
        if existing:
            return existing
        line = str(data.get("line", "auto"))
        if line != "auto" and line not in self.allowed_lines:
            raise ApiError(400, "invalid line")
        now = int(time.time())
        call = Call(str(uuid.uuid4()), str(data["request_id"]), tenant, str(data["destination"]), line, agents, "originating", now, now)
        self.store.create(call)
        self.store.event(call.call_id, "call.state", "voice-api", {"status": call.status}, f"{call.request_id}:created")
        try:
            dial_line = "line01" if line == "auto" else line
            fs_uuid = str(uuid.uuid4())
            destination = call.destination.lstrip("+")
            variables = f"origination_uuid={fs_uuid},voice_call_id={call.call_id},voice_agents={','.join(agents)},origination_caller_id_name={tenant}"
            result = self.background(f"originate {{{variables}}}sofia/gateway/{dial_line}/{destination} &conference({call.call_id}@voice)")
            if result.startswith("-ERR"):
                raise RuntimeError(result)
            call.fs_uuid, call.status, call.updated_at = fs_uuid, "originating", int(time.time())
            for agent in agents:
                self.join_agent_media(call, agent)
        except Exception as exc:
            call.status, call.failure, call.updated_at = "failed", str(exc)[:500], int(time.time())
        self.store.update(call)
        self.store.event(call.call_id, "call.state", "voice-api", {"status": call.status, "failure": call.failure})
        return call


APP: App


class Handler(BaseHTTPRequestHandler):
    server_version = "re8ch-voice/1.0"

    def log_message(self, fmt, *args):
        print(json.dumps({"level": "info", "message": fmt % args, "client": self.client_address[0]}), flush=True)

    def body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 65536:
            raise ApiError(413, "body too large")
        return self.rfile.read(length)

    def send_json(self, status: int, value) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(raw)

    def route(self):
        parsed = urlparse(self.path)
        if self.command == "GET" and parsed.path == "/healthz":
            APP.esl.api("status")
            return self.send_json(200, {"status": "ok"})
        raw = self.body() if self.command == "POST" else b""
        APP.authenticate(self.headers, raw)
        if self.command == "POST" and parsed.path == "/v1/voice/calls":
            return self.send_json(200, asdict(APP.create_call(json.loads(raw))))
        match = re.fullmatch(r"/v1/voice/calls/([0-9a-f-]+)(?:/(hangup)|/agents/([^/]+)/(join|leave))?", parsed.path)
        if match:
            call = APP.store.get(match.group(1))
            if self.command == "GET" and not match.group(2) and not match.group(3):
                return self.send_json(200, asdict(call))
            if self.command == "POST" and match.group(2) == "hangup":
                if call.fs_uuid: APP.esl.api(f"uuid_kill {call.fs_uuid} NORMAL_CLEARING")
                call.status, call.updated_at = "ended", int(time.time()); APP.store.update(call)
                APP.store.event(call.call_id, "call.state", "voice-api", {"status": "ended"})
                return self.send_json(200, asdict(call))
            if self.command == "POST" and match.group(3):
                agent, action = match.group(3), match.group(4)
                if not AGENT_RE.fullmatch(agent): raise ApiError(400, "invalid agent")
                if action == "join" and agent not in call.agents:
                    if len(call.agents) >= MAX_AGENTS: raise ApiError(409, "agent limit reached")
                    APP.join_agent_media(call, agent)
                    call.agents.append(agent)
                if action == "leave" and agent in call.agents:
                    APP.esl.api(f"uuid_kill {APP.agent_uuid(call.call_id, agent)} NORMAL_CLEARING")
                    call.agents.remove(agent)
                call.updated_at = int(time.time()); APP.store.update(call)
                APP.store.event(call.call_id, f"agent.{action}", "voice-api", {"agent_id": agent})
                return self.send_json(200, asdict(call))
        if self.command == "GET" and parsed.path == "/v1/voice/events":
            after = int(self.headers.get("Last-Event-ID", "0")); events = APP.store.events(after)
            payload = b"".join(f"id: {e['seq']}\nevent: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload); return
        raise ApiError(404, "not found")

    def do_GET(self):
        try: self.route()
        except ApiError as exc: self.send_json(exc.status, {"error": str(exc)})
        except Exception as exc: self.send_json(503, {"error": str(exc)})

    do_POST = do_GET


def main():
    global APP
    APP = App()
    server = ThreadingHTTPServer((os.getenv("VOICE_LISTEN", "127.0.0.1"), int(os.getenv("VOICE_PORT", "18092"))), Handler)
    server.serve_forever()


if __name__ == "__main__": main()
