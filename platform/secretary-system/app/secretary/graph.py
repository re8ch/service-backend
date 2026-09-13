from __future__ import annotations

from .model import FactEvent


class Neo4jProjector:
    """Idempotent, rebuildable projection. PostgreSQL remains authoritative."""

    def __init__(self, uri: str, user: str, password: str):
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def project(self, event: FactEvent) -> None:
        query = """
        MERGE (i:Incident {id: $incident_id})
        MERGE (e:EvidenceEvent {id: $event_id})
        SET e.type=$type, e.authority=$authority, e.source=$source,
            e.failureDomain=$failure_domain, e.method=$method,
            e.strength=$strength, e.confidence=$confidence,
            e.observedAt=datetime($observed_at), e.artifactRef=$artifact_ref
        MERGE (e)-[:EVIDENCE_FOR]->(i)
        MERGE (a:Actor {kind: $actor_kind, id: $actor_id})
        MERGE (e)-[:OBSERVED_BY]->(a)
        MERGE (f:FailureDomain {id: $failure_domain})
        MERGE (e)-[:ORIGINATED_IN]->(f)
        """
        params = {
            "incident_id": event.incident_id, "event_id": event.event_id, "type": event.type,
            "authority": event.authority, "source": event.source,
            "failure_domain": event.evidence.failure_domain, "method": event.evidence.method,
            "strength": event.evidence.strength, "confidence": event.evidence.confidence,
            "observed_at": event.observed_at, "artifact_ref": event.evidence.artifact_ref,
            "actor_kind": event.actor.kind, "actor_id": event.actor.id,
        }
        with self.driver.session() as session:
            session.run(query, **params).consume()

