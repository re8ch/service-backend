# Runtime service Catalog MVP

This is the smallest projection of services already running in the RE8CH
cluster. It does not create products, prices, tenants or another runtime
database.

`project.py` reads the existing credential-free Kubernetes snapshot and writes
`inventory/platform/catalog/runtime-services.json`. The generated file is a
stable, Git-reviewable input for Catalog pages and Agents. Kubernetes remains
runtime authority; the projection records only workload identity, readiness
and same-name Service/Ingress discovery.

```console
python operations/catalog-runtime/project.py
python operations/catalog-runtime/project.py --check
```

The initial join is intentionally conservative. A workload is not inferred to
be a commercial product. Product ownership and metering can be added after the
running inventory is visible and reviewed.
