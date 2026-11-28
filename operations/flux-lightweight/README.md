# Retired Flux inventory tooling

This directory is historical migration evidence from the removal of
cluster-resident Flux controllers. Its catalogs and inventory scripts describe
the responsibilities that had to be replaced before retirement. They do not
describe current runtime components or a future executor.

GitHub Actions and the bounded cloud SSA executor are the only platform delivery
writer. Do not use this directory to start a Flux controller, create a new
reconciler identity or infer current live ownership.

The inventory tools may be run offline only to explain a retained declaration or
validate that retirement coverage has not regressed. Their output is evidence,
not desired state. Current operations are documented in
[`operations/releases/DOMAIN-RECONCILIATION.md`](../releases/DOMAIN-RECONCILIATION.md).
