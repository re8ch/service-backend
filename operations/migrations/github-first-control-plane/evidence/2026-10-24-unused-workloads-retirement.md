# Observable and ontology microscope retirement evidence

Captured at 2026-10-24 before the reviewed GitOps retirement.

## Observable PostgREST

- Desired source: `clusters/re8ch/components/observable/postgrest-ha.yaml` through the data-storage domain.
- Live runtime: Deployment `observable-postgrest` was `1/2`; its pinned image was `docker.io/postgrest/postgrest:v14.1@sha256:e9490aa503a5fb07d8e8c80da46e5c0c193894e583b59ed9077e74c4101ffae2`.
- Published routes: `db-ro.re8ch.com/observable` and `db-rw.re8ch.com/observable`.
- Retained objects: namespace `observable` and externally managed Secret `observable-postgrest`.
- Delete set: the Deployment, Service, HPA, PDB and two HTTPRoutes owned by this component.

## Ontology microscope

- Desired source: `platform/external-projects/ontology-microscope` through the delivery-products domain.
- Healthy runtime at capture: Deployment `ontology-microscope` was `1/1` at `registry.re8ch.com/re8ch/ontology-microscope@sha256:f806538e9ec90be9cd6813db0e4b99ae5f7dc4427f91345d5afb291c5894eecf`.
- Unavailable runtime at capture: adapter, evaluation worker, Neuronpedia, Neo4j and Neuronpedia PostgreSQL were each `0/1`.
- Published route: `ontology.apps.re8ch.com`.
- Retained PVCs: `data-ontology-microscope-neo4j-0` and `data-ontology-neuronpedia-postgres-0`, both `Bound`, 20Gi, `rbd-r640`.
- Retained objects: namespace, TenantNamespace, PVCs and encrypted/product Secrets. Secret payloads were not read or recorded.
- Delete set: product Deployments, StatefulSets, Services, HTTPRoute, ConfigMaps and CiliumNetworkPolicies.

## Recovery boundary

Recovery requires a reviewed Git change that restores the removed source declarations and reuses the recorded image digests and retained data objects. No namespace, PVC, Secret, database contents or shared Gateway listener is part of this retirement delete set.
