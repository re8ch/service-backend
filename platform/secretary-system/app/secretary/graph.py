from __future__ import annotations

from .interaction import InteractionObservation
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

    def project_interaction(self, observation: InteractionObservation) -> None:
        query = """
        MERGE (i:Interaction {id: $interaction_id})
        MERGE (e:Observation {id: $observation_id})
        SET e.nativeType=$native_type, e.nativeStatus=$native_status,
            e.authority=$authority, e.strength=$strength, e.confidence=$confidence,
            e.failureDomain=$failure_domain, e.method=$method,
            e.observedAt=datetime($observed_at), e.artifactRef=$artifact_ref,
            e.payloadHash=$payload_hash, e.schemaRef=$schema_ref
        MERGE (e)-[:EVIDENCE_FOR]->(i)
        MERGE (s:Endpoint {kind: $source_kind, id: $source_id})
        MERGE (t:Endpoint {kind: $target_kind, id: $target_id})
        MERGE (r:Runtime {kind: $runtime_kind, id: $runtime_id})
        MERGE (e)-[:OBSERVED_IN]->(r)
        MERGE (e)-[:OBSERVED_SOURCE]->(s)
        MERGE (e)-[:OBSERVED_TARGET]->(t)
        MERGE (s)-[:OBSERVED_ROUTE {relation: $relation}]->(t)
        """
        params = {
            "interaction_id": observation.interaction_id, "observation_id": observation.observation_id,
            "native_type": observation.native_type, "native_status": observation.native_status,
            "authority": observation.authority, "strength": observation.evidence.strength,
            "confidence": observation.evidence.confidence,
            "failure_domain": observation.evidence.failure_domain, "method": observation.evidence.method,
            "observed_at": observation.observed_at, "artifact_ref": observation.evidence.artifact_ref,
            "payload_hash": observation.evidence.payload_hash, "schema_ref": observation.evidence.schema_ref,
            "source_kind": observation.source.kind, "source_id": observation.source.id,
            "target_kind": observation.target.kind, "target_id": observation.target.id,
            "runtime_kind": observation.runtime.kind, "runtime_id": observation.runtime.id,
            "relation": observation.relation,
        }
        with self.driver.session() as session:
            session.run(query, **params).consume()
