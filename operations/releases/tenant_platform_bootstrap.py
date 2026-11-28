#!/usr/bin/env python3
"""Validate and materialize the one-time tenant-infrastructure bootstrap slice."""
import argparse, json, tarfile
from pathlib import Path
import yaml

NAMESPACE = "tenant-infrastructure"
ALLOWED = {
    ("apps/v1", "DaemonSet", "tenant-runtime-image-cache"),
    ("apps/v1", "Deployment", "admin-api"),
    ("apps/v1", "Deployment", "postgresql-provider"),
    ("apps/v1", "Deployment", "tenant-provider-reconciler"),
    ("cilium.io/v2", "CiliumNetworkPolicy", "postgresql-provider-egress"),
    ("cilium.io/v2", "CiliumNetworkPolicy", "tenant-api-gateway"),
    ("v1", "ConfigMap", "tenant-platform-modules"),
    ("v1", "Service", "admin-api"), ("v1", "Service", "postgresql-provider"),
    ("v1", "Service", "tenant-api"),
    ("v1", "Service", "tenant-control-controller"), ("v1", "Service", "tenant-infrastructure"),
    ("v1", "ServiceAccount", "admin-api"), ("v1", "ServiceAccount", "postgresql-provider"),
    ("v1", "ServiceAccount", "tenant-api"),
    ("v1", "ServiceAccount", "tenant-control-controller"), ("v1", "ServiceAccount", "tenant-provider-reconciler"),
    ("gateway.networking.k8s.io/v1", "HTTPRoute", "tenant-billing-auth-center"),
    ("gateway.networking.k8s.io/v1", "HTTPRoute", "tenant-billing-read"),
    ("gateway.networking.k8s.io/v1", "HTTPRoute", "tenant-credential-redemption"),
    ("gateway.networking.k8s.io/v1", "HTTPRoute", "tenant-native-mcp"),
    ("gateway.networking.k8s.io/v1", "HTTPRoute", "tenant-self-service-capabilities"),
    ("networking.k8s.io/v1", "NetworkPolicy", "admin-api"),
    ("networking.k8s.io/v1", "NetworkPolicy", "postgresql-provider"),
    ("networking.k8s.io/v1", "NetworkPolicy", "tenant-api"),
    ("networking.k8s.io/v1", "NetworkPolicy", "tenant-control-controller"),
    ("policy/v1", "PodDisruptionBudget", "tenant-control-controller"),
    ("tenancy.re8ch.com/v1alpha1", "TenantServiceInstance", "mcp-catalog-runtime"),
}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--bundle",type=Path,required=True); ap.add_argument("--access",type=Path,required=True); ap.add_argument("--role-only",action="store_true"); ap.add_argument("--output",type=Path,required=True)
    a=ap.parse_args()
    with tarfile.open(a.bundle,"r:gz") as tf:
        manifest=json.load(tf.extractfile("manifest.json")); objects=json.load(tf.extractfile("objects.json"))
    if manifest.get("releaseId") != "agent-runtime-domain-tenant-infrastructure": raise SystemExit("wrong releaseId")
    seen=set()
    for obj in objects:
        meta=obj.get("metadata",{}); ident=(obj.get("apiVersion"),obj.get("kind"),meta.get("name"))
        if meta.get("namespace") != NAMESPACE or ident not in ALLOWED: raise SystemExit(f"undeclared bootstrap object: {ident}")
        if obj.get("kind") == "Secret": raise SystemExit("Secret is forbidden")
        seen.add(ident)
    if seen != ALLOWED: raise SystemExit(f"bootstrap inventory mismatch: missing={sorted(ALLOWED-seen)} extra={sorted(seen-ALLOWED)}")
    access=[item for item in yaml.safe_load_all(a.access.read_text()) if item]
    role=next(item for item in access if item.get("kind") == "Role"
              and item.get("metadata",{}).get("namespace") == NAMESPACE
              and item["metadata"].get("name") == "agent-runtime-domain-tenant-infrastructure")
    objects=[role] if a.role_only else objects+[role]
    a.output.write_text(yaml.safe_dump_all(objects,sort_keys=False,explicit_start=True))

if __name__ == "__main__": main()
