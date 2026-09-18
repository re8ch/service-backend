from __future__ import annotations

import json
import os
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .interaction import InteractionObservation, TopologyDeclaration, supporting_evidence_is_adoptable
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
CREATE TABLE IF NOT EXISTS secretary_interaction_observation (
  seq BIGSERIAL PRIMARY KEY,
  observation_id UUID UNIQUE NOT NULL,
  interaction_id TEXT NOT NULL,
  edge_key TEXT NOT NULL,
  native_type TEXT NOT NULL,
  native_status TEXT NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL,
  received_at TIMESTAMPTZ NOT NULL,
  authority TEXT NOT NULL,
  strength TEXT NOT NULL,
  failure_domain TEXT NOT NULL,
  measurement_method TEXT NOT NULL,
  confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  document_hash TEXT NOT NULL,
  idempotency_key TEXT UNIQUE NOT NULL,
  adopted BOOLEAN NOT NULL DEFAULT FALSE,
  observation_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS secretary_interaction_observation_lookup
  ON secretary_interaction_observation(interaction_id, edge_key, seq);
CREATE TABLE IF NOT EXISTS secretary_interaction_outbox (
  seq BIGSERIAL PRIMARY KEY,
  observation_id UUID UNIQUE NOT NULL REFERENCES secretary_interaction_observation(observation_id),
  destination TEXT NOT NULL,
  delivered_at TIMESTAMPTZ,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  UNIQUE(observation_id, destination)
);
CREATE TABLE IF NOT EXISTS secretary_topology_declaration (
  seq BIGSERIAL PRIMARY KEY,
  declaration_id UUID UNIQUE NOT NULL,
  edge_key TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  declared_at TIMESTAMPTZ NOT NULL,
  enabled BOOLEAN NOT NULL,
  idempotency_key TEXT UNIQUE NOT NULL,
  declaration_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS secretary_topology_declaration_edge
  ON secretary_topology_declaration(edge_key, seq DESC);
"""


class PostgresLedger:
    def __init__(self, dsn: str):
        self.dsn = self._override_host(dsn)

    @staticmethod
    def _override_host(dsn: str) -> str:
        host = os.environ.get("SECRETARY_DATABASE_HOST_OVERRIDE", "").strip()
        if not host or "://" not in dsn:
            return dsn
        parsed = urlsplit(dsn)
        port = int(os.environ.get("SECRETARY_DATABASE_PORT_OVERRIDE", str(parsed.port or 5432)))
        credentials = f"{parsed.netloc.rsplit('@', 1)[0]}@" if "@" in parsed.netloc else ""
        return urlunsplit((parsed.scheme, f"{credentials}{host}:{port}", parsed.path, parsed.query, parsed.fragment))

    def _connect(self):
        import psycopg
        return psycopg.connect(
            self.dsn, autocommit=True, connect_timeout=3,
            options="-c statement_timeout=5000 -c lock_timeout=3000",
        )

    def ping(self) -> None:
        with self._connect() as connection, connection.cursor() as cur:
            cur.execute("SELECT 1")

    def migrate(self) -> None:
        with self._connect() as connection, connection.transaction(), connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('re8ch-secretary-schema-v1'))")
            cur.execute(SCHEMA_SQL)

    def append(self, event: FactEvent, destinations: Iterable[str] = ("neo4j",)) -> bool:
        with self._connect() as connection, connection.transaction(), connection.cursor() as cur:
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
        with self._connect() as connection, connection.cursor() as cur:
            cur.execute("SELECT event_json FROM secretary_incident_event WHERE incident_id=%s ORDER BY seq", (incident_id,))
            return [FactEvent.from_dict(row[0]) for row in cur.fetchall()]

    def stream(self, after_seq: int = 0, incident_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        with self._connect() as connection, connection.cursor() as cur:
            if incident_id:
                cur.execute("SELECT seq,event_json FROM secretary_incident_event WHERE seq>%s AND incident_id=%s ORDER BY seq LIMIT %s", (after_seq, incident_id, limit))
            else:
                cur.execute("SELECT seq,event_json FROM secretary_incident_event WHERE seq>%s ORDER BY seq LIMIT %s", (after_seq, limit))
            return [{"seq": row[0], "event": row[1]} for row in cur.fetchall()]

    def latest(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        with self._connect() as connection, connection.cursor() as cur:
            cur.execute("SELECT seq,event_json FROM secretary_incident_event ORDER BY seq DESC LIMIT %s", (limit,))
            return [{"seq": row[0], "event": row[1]} for row in cur.fetchall()]

    def unresolved_delivery_attempts(self, older_than_seconds: int, limit: int = 100) -> list[FactEvent]:
        with self._connect() as connection, connection.cursor() as cur:
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
        with self._connect() as connection, connection.cursor() as cur:
            cur.execute("""SELECT o.seq,e.event_json FROM secretary_outbox o
                JOIN secretary_incident_event e USING(event_id)
                WHERE o.destination=%s AND o.delivered_at IS NULL ORDER BY o.seq LIMIT %s""", (destination, limit))
            return [{"seq": row[0], "event": row[1]} for row in cur.fetchall()]

    def mark_delivered(self, seq: int) -> None:
        with self._connect() as connection, connection.transaction(), connection.cursor() as cur:
            cur.execute("UPDATE secretary_outbox SET delivered_at=now(),attempts=attempts+1,last_error=NULL WHERE seq=%s", (seq,))

    def mark_failed(self, seq: int, error: str) -> None:
        with self._connect() as connection, connection.transaction(), connection.cursor() as cur:
            cur.execute("UPDATE secretary_outbox SET attempts=attempts+1,last_error=%s WHERE seq=%s", (error[:500], seq))

    def append_observation(self, observation: InteractionObservation, destinations: Iterable[str] = ("neo4j-interaction",)) -> tuple[bool, bool]:
        with self._connect() as connection, connection.transaction(), connection.cursor() as cur:
            authoritative = (
                observation.evidence.strength == "authoritative"
                and observation.authority in {"provider_callback", "runtime_receipt", "authenticated_action"}
            )
            cur.execute(
                """INSERT INTO secretary_interaction_observation
                (observation_id,interaction_id,edge_key,native_type,native_status,observed_at,received_at,
                 authority,strength,failure_domain,measurement_method,confidence,document_hash,
                 idempotency_key,adopted,observation_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (idempotency_key) DO NOTHING RETURNING observation_id""",
                (
                    observation.observation_id, observation.interaction_id, observation.edge_key(),
                    observation.native_type, observation.native_status, observation.observed_at,
                    observation.received_at, observation.authority, observation.evidence.strength,
                    observation.evidence.failure_domain, observation.evidence.method,
                    observation.evidence.confidence, observation.document_hash(), observation.idempotency_key,
                    authoritative, json.dumps(observation.to_dict()),
                ),
            )
            inserted = cur.fetchone()
            if not inserted:
                cur.execute("SELECT adopted FROM secretary_interaction_observation WHERE idempotency_key=%s", (observation.idempotency_key,))
                return False, bool(cur.fetchone()[0])
            for destination in destinations:
                cur.execute(
                    "INSERT INTO secretary_interaction_outbox(observation_id,destination) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                    (observation.observation_id, destination),
                )
            if not authoritative and observation.evidence.strength != "weak" and "log" not in observation.evidence.method.lower():
                cur.execute(
                    """SELECT failure_domain,measurement_method,MAX(confidence)
                         FROM secretary_interaction_observation
                        WHERE interaction_id=%s AND edge_key=%s AND strength <> 'weak'
                          AND lower(measurement_method) NOT LIKE '%%log%%'
                        GROUP BY failure_domain,measurement_method""",
                    (observation.interaction_id, observation.edge_key()),
                )
                independent = [(domain, method, float(confidence)) for domain, method, confidence in cur.fetchall()]
                adopted = supporting_evidence_is_adoptable(independent)
                if adopted:
                    cur.execute(
                        "UPDATE secretary_interaction_observation SET adopted=TRUE WHERE interaction_id=%s AND edge_key=%s",
                        (observation.interaction_id, observation.edge_key()),
                    )
            else:
                adopted = authoritative
            return True, adopted

    def append_declaration(self, declaration: TopologyDeclaration) -> bool:
        with self._connect() as connection, connection.transaction(), connection.cursor() as cur:
            cur.execute(
                """INSERT INTO secretary_topology_declaration
                (declaration_id,edge_key,owner_id,declared_at,enabled,idempotency_key,declaration_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (idempotency_key) DO NOTHING RETURNING declaration_id""",
                (
                    declaration.declaration_id, declaration.edge_key(), declaration.owner_id,
                    declaration.declared_at, declaration.enabled, declaration.idempotency_key,
                    json.dumps(declaration.to_dict()),
                ),
            )
            return cur.fetchone() is not None

    def interaction(self, interaction_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cur:
            cur.execute(
                "SELECT seq,adopted,observation_json FROM secretary_interaction_observation WHERE interaction_id=%s ORDER BY seq",
                (interaction_id,),
            )
            return [{"seq": row[0], "adopted": row[1], "observation": row[2]} for row in cur.fetchall()]

    def topology(self) -> dict[str, list[dict[str, Any]]]:
        with self._connect() as connection, connection.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT ON (edge_key) edge_key,enabled,declaration_json
                     FROM secretary_topology_declaration ORDER BY edge_key,seq DESC"""
            )
            declared = [{"edge_key": row[0], "enabled": row[1], "declaration": row[2]} for row in cur.fetchall()]
            cur.execute(
                """SELECT edge_key,MAX(seq),bool_or(adopted),(array_agg(observation_json ORDER BY seq DESC))[1]
                     FROM secretary_interaction_observation GROUP BY edge_key ORDER BY MAX(seq) DESC"""
            )
            observed = [{"edge_key": row[0], "last_seq": row[1], "adopted": row[2], "observation": row[3]} for row in cur.fetchall()]
            return {"declared": declared, "observed": observed}

    def discrepancies(self) -> list[dict[str, Any]]:
        topology = self.topology()
        declared = {item["edge_key"] for item in topology["declared"] if item["enabled"]}
        observed = {item["edge_key"] for item in topology["observed"] if item["adopted"]}
        return (
            [{"kind": "declared_not_observed", "edge_key": key} for key in sorted(declared - observed)]
            + [{"kind": "observed_not_declared", "edge_key": key} for key in sorted(observed - declared)]
        )

    def pending_interactions(self, destination: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cur:
            cur.execute(
                """SELECT o.seq,e.observation_json FROM secretary_interaction_outbox o
                    JOIN secretary_interaction_observation e USING(observation_id)
                    WHERE o.destination=%s AND o.delivered_at IS NULL ORDER BY o.seq LIMIT %s""",
                (destination, limit),
            )
            return [{"seq": row[0], "observation": row[1]} for row in cur.fetchall()]

    def mark_interaction_delivered(self, seq: int) -> None:
        with self._connect() as connection, connection.transaction(), connection.cursor() as cur:
            cur.execute("UPDATE secretary_interaction_outbox SET delivered_at=now(),attempts=attempts+1,last_error=NULL WHERE seq=%s", (seq,))

    def mark_interaction_failed(self, seq: int, error: str) -> None:
        with self._connect() as connection, connection.transaction(), connection.cursor() as cur:
            cur.execute("UPDATE secretary_interaction_outbox SET attempts=attempts+1,last_error=%s WHERE seq=%s", (error[:500], seq))
