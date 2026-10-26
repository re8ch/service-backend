# Native safe YAML parsing

Measured hotspots use PyYAML's LibYAML-backed `CSafeLoader` explicitly, with
`SafeLoader` as the fallback when the extension is unavailable. Constructors stay
safe; neither `Loader`, `CLoader` nor `UnsafeLoader` is used. There is no global
PyYAML monkeypatch, additional cache, worker fanout or reduction in test scope.
The existing Python emitter is retained so checked-in generated YAML stays stable.
CI verifies that LibYAML is present and records the slowest 20 test phases.

## Local before/after evidence

Base: `dbc9d37f6` (merged #2396). Machine: Macmini, Python 3.13.3, PyYAML 6.0.2
with LibYAML, pytest 8.4.2. Only the selected connectool gitlink was initialized.
Each measurement ran the same 1,240 existing cases: 1,239 passed, one skipped.
These timings exclude the 44 new parser compatibility cases and are not restricted-node-6dc3f140f9 CI
predictions. No parallel tests or reused test results were involved.

| Execution | Pure Python | Native C |
| --- | ---: | ---: |
| Initial before/after | 58.80 s | 31.52 s |
| Repeated warm-input comparison | 56.39 s | 34.19 s |
| Mean | 57.60 s | 32.86 s |

Mean elapsed time falls about 43% (1.75x throughput). Individual suite times
from the first JUnit reports, including their fixture setup:

| Suite | Before | After |
| --- | ---: | ---: |
| Flux inventory | 9.66 s | 1.18 s |
| HelmRelease inventory | 5.20 s | 0.61 s |
| Domain reconcilers | 3.51 s | 0.33 s |
| Tenant infrastructure GitOps | 3.27 s | 1.19 s |
| Flux controller HA | 3.24 s | 1.48 s |
| Main domain SSA | 2.14 s | 0.58 s |

Baseline and native commands used `python -m pytest -q --durations=30
--junitxml=<outside-worktree.xml>`. To reproduce the same original case set after
this PR, add `--ignore=tests/test_native_yaml_loader.py`. The repeated Python
comparison used an external, temporary `sitecustomize.py` that sets
`yaml.CSafeLoader = yaml.SafeLoader`; its directory was added to `PYTHONPATH`
only for that benchmark process and inherited Python children. That override is
not part of the repository or CI. The normal native run has no override.

## Validation and scope

The compatibility cases cover safe-constructor rejection, missing-extension
fallback, scalar types, timestamps, Unicode, binary values, aliases/merge keys,
multi-document streams, malformed input, and identical serialization for
representative checked-in GitOps manifests. Existing inventory/generator checks
continue to verify checked-in desired state. Parser error wording may differ;
invalid documents still fail. The change does not introduce schema validation
or alter ownership, signature, release or runtime health contracts.

## Practices considered

- [PyYAML LibYAML documentation](https://pyyaml.org/wiki/PyYAMLDocumentation):
  use the native parser with explicit safe constructors; test semantic differences.
- [Kubeconform](https://github.com/yannh/kubeconform): native validation,
  bounded worker concurrency and schema caches are useful for large manifest
  sets. Its schema checks do not replace repository-specific policy or all
  server-side admission checks; adding a second validator is outside this slice.
- [Flux Schema](https://fluxcd.io/blog/2026/07/flux-schema-validation/): native
  rendering and validation are available for ordinary Kubernetes/Flux schemas.
  The measured inventory bottleneck does not require replacing our custom rules.
- [pytest-xdist fixtures](https://pytest-xdist.readthedocs.io/en/stable/how-to.html):
  session fixtures execute per worker unless coordinated. Increasing workers can
  multiply inventory scans and compete with other jobs on the same restricted-node-6dc3f140f9 host;
  retain serial execution while measuring the parser change independently.
