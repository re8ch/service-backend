from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from .model import FactEvent


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS secretary_incident_event (
  seq BIGSERIAL PRIMARY KEY,
  event_id UUID UNIQUE NOT NULL,
  incident_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL,
  received_at TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL,
  authority TEXT NOT NULL,
  failure_domain TEXT NOT NULL,
  measurement_method TEXT NOT NULL,
  actor_kind TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  artifact_ref TEXT NOT NULL,
  idempotency_key TEXT UNIQUE NOT NULL,
  event_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS secretary_incident_event_incident_seq
  ON secretary_incident_event(incident_id, seq);
CREATE TABLE IF NOT EXISTS secretary_outbox (
  seq BIGSERIAL PRIMARY KEY,
  event_id UUID UNIQUE NOT NULL REFERENCES secretary_incident_event(event_id),
  destination TEXT NOT NULL,
  delivered_at TIMESTAMPTZ,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  UNIQUE(event_id, destination)
);
CREATE TABLE IF NOT EXISTS secretary_incident_projection (
  incident_id TEXT PRIMARY KEY,
  severity TEXT NOT NULL,
  state TEXT NOT NULL,
  owner_id TEXT,
  version BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class PostgresLedger:
    def __init__(self, dsn: str):
        import psycopg
        # Reads must not leave an implicit outer transaction open: write methods
        # already establish their own atomic transaction blocks below.
        self.db = psycopg.connect(dsn, autocommit=True)

    def migrate(self) -> None:
        with self.db.transaction(), self.db.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('re8ch-secretary-schema-v1'))")
            cur.execute(SCHEMA_SQL)

    def append(self, event: FactEvent, destinations: Iterable[str] = ("neo4j",)) -> bool:
        with self.db.transaction(), self.db.cursor() as cur:
            cur.execute(
                """INSERT INTO secretary_incident_event
                (event_id,incident_id,event_type,observed_at,received_at,source,authority,
                 failure_domain,measurement_method,actor_kind,actor_id,payload_hash,artifact_ref,
                 idempotency_key,event_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (idempotency_key) DO NOTHING RETURNING event_id""",
                (event.event_id, event.incident_id, event.type, event.observed_at, event.received_at,
                 event.source, event.authority, event.evidence.failure_domain, event.evidence.method,
                 event.actor.kind, event.actor.id, event.payload_hash(), event.evidence.artifact_ref,
                 event.idempotency_key, json.dumps(event.to_dict())),
            )
            inserted = cur.fetchone()
            if not inserted:
                return False
            for destination in destinations:
                cur.execute("INSERT INTO secretary_outbox(event_id,destination) VALUES(%s,%s) ON CONFLICT DO NOTHING", (event.event_id, destination))
            return True

    def events(self, incident_id: str) -> list[FactEvent]:
        with self.db.cursor() as cur:
            cur.execute("SELECT event_json FROM secretary_incident_event WHERE incident_id=%s ORDER BY seq", (incident_id,))
            return [FactEvent.from_dict(row[0]) for row in cur.fetchall()]

    def stream(self, after_seq: int = 0, incident_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        with self.db.cursor() as cur:
            if incident_id:
                cur.execute("SELECT seq,event_json FROM secretary_incident_event WHERE seq>%s AND incident_id=%s ORDER BY seq LIMIT %s", (after_seq, incident_id, limit))
            else:
                cur.execute("SELECT seq,event_json FROM secretary_incident_event WHERE seq>%s ORDER BY seq LIMIT %s", (after_seq, limit))
            return [{"seq": row[0], "event": row[1]} for row in cur.fetchall()]

    def latest(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        with self.db.cursor() as cur:
            cur.execute("SELECT seq,event_json FROM secretary_incident_event ORDER BY seq DESC LIMIT %s", (limit,))
            return [{"seq": row[0], "event": row[1]} for row in cur.fetchall()]

    def unresolved_delivery_attempts(self, older_than_seconds: int, limit: int = 100) -> list[FactEvent]:
        with self.db.cursor() as cur:
            cur.execute(
                """SELECT attempted.event_json
                   FROM secretary_incident_event attempted
                  WHERE attempted.event_type='delivery.attempted'
                    AND attempted.observed_at < now() - (%s * interval '1 second')
                    AND NOT EXISTS (
                      SELECT 1 FROM secretary_incident_event outcome
                       WHERE outcome.incident_id=attempted.incident_id
                         AND outcome.seq > attempted.seq
                         AND outcome.event_type IN (
                           'delivery.provider_accepted','delivery.device_delivered',
                           'delivery.human_seen','delivery.failed','delivery.unknown')
                         AND (
                           outcome.event_json->>'causation_id'=attempted.event_id::text
                           OR outcome.event_json->'data'->>'attempt_event_id'=attempted.event_id::text
                           OR (
                             attempted.event_json->'data'->>'request_key' IS NOT NULL
                             AND outcome.event_json->'data'->>'request_key'=
                                 attempted.event_json->'data'->>'request_key'))
                    )
                  ORDER BY attempted.seq
                  LIMIT %s""",
                (max(1, older_than_seconds), max(1, min(limit, 1000))),
            )
            return [FactEvent.from_dict(row[0]) for row in cur.fetchall()]

    def pending(self, destination: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.cursor() as cur:
            cur.execute("""SELECT o.seq,e.event_json FROM secretary_outbox o
                JOIN secretary_incident_event e USING(event_id)
                WHERE o.destination=%s AND o.delivered_at IS NULL ORDER BY o.seq LIMIT %s""", (destination, limit))
            return [{"seq": row[0], "event": row[1]} for row in cur.fetchall()]

    def mark_delivered(self, seq: int) -> None:
        with self.db.transaction(), self.db.cursor() as cur:
            cur.execute("UPDATE secretary_outbox SET delivered_at=now(),attempts=attempts+1,last_error=NULL WHERE seq=%s", (seq,))

    def mark_failed(self, seq: int, error: str) -> None:
        with self.db.transaction(), self.db.cursor() as cur:
            cur.execute("UPDATE secretary_outbox SET attempts=attempts+1,last_error=%s WHERE seq=%s", (error[:500], seq))
