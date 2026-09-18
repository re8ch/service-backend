#!/usr/bin/env python3
"""Count Pending Pods that can be affected by control-plane taints."""

import json
import sys


def can_target_control_plane(pod, targets):
    spec = pod.get("spec", {})
    assigned = spec.get("nodeName")
    if assigned:
        return assigned in targets

    hostname = (spec.get("nodeSelector") or {}).get("kubernetes.io/hostname")
    if hostname:
        return hostname in targets

    terms = (
        ((spec.get("affinity") or {}).get("nodeAffinity") or {})
        .get("requiredDuringSchedulingIgnoredDuringExecution", {})
        .get("nodeSelectorTerms", [])
    )
    constrained = False
    eligible = set()
    for term in terms:
        for expression in term.get("matchExpressions", []):
            if (
                expression.get("key") == "kubernetes.io/hostname"
                and expression.get("operator") == "In"
            ):
                constrained = True
                eligible.update(expression.get("values", []))
        for field in term.get("matchFields", []):
            if field.get("key") == "metadata.name" and field.get("operator") == "In":
                constrained = True
                eligible.update(field.get("values", []))
    return bool(targets & eligible) if constrained else True


def main(argv=None):
    targets = set((argv or sys.argv[1:]))
    if not targets:
        raise SystemExit("at least one target node is required")
    pods = json.load(sys.stdin).get("items", [])
    print(sum(1 for pod in pods if can_target_control_plane(pod, targets)))


if __name__ == "__main__":
    main()
