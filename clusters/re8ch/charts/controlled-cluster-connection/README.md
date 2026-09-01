# Controlled Cluster Connection

This owner-installed Helm application grants explicit OIDC identities access to
one independent Kubernetes cluster. It does not create credentials, tunnels,
service discovery or data-plane federation.

## Install

The cluster owner reviews its values file and installs the release locally:

```sh
helm upgrade --install controlled-cluster-connection . \
  --namespace cluster-access \
  --create-namespace \
  -f examples/artc-values.yaml
```

Use `examples/qwen-values.yaml` for qwen. In production, keep the reviewed
values in the owner's own repository; the examples here describe the current
cross-organisation agreement but are not reconciled into either external
cluster by RE8CH Flux.

## Verify

Verify both aliases for each human until the target API server's username claim
has been proven stable:

```sh
kubectl auth can-i '*' '*' --as='oidc:owner@example.com'
kubectl get configmap -n cluster-access controlled-cluster-connection-contract
```

Then perform a fresh OIDC login through the consuming Headlamp instance.

## Revoke

The owner can remove one `accessGrant` and upgrade the release, or revoke the
whole relationship independently:

```sh
helm uninstall controlled-cluster-connection --namespace cluster-access
```

Verify denial before removing the consumer-side discovery entry.
