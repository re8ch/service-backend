"""Pure, fail-closed path gate; caller must supply trusted policy/API evidence.

No checkout, network, command execution, or deployment authorization. This is
only one admission predicate, never proof of rendered Kubernetes ownership.
"""
from collections.abc import Mapping


def canonical_path(value):
    return (isinstance(value, str) and bool(value)
            and not any(ord(c) < 32 or ord(c) == 127 for c in value)
            and "\\" not in value
            and all(part not in ("", ".", "..") for part in value.split("/")))


def within(path, prefix):
    return path == prefix or path.startswith(prefix + "/")


def evaluate(policy, *, actor_id, service, files, changed_files):
    """Evaluate a complete immutable-base/head diff supplied by the caller.

    actor_id is a GitHub numeric identity obtained from the API, not PR text.
    Policy must come from an independently pinned trusted revision. Changes to
    that policy require a separate platform route, not a service override.
    Unknown data, partial pagination and renamed source violations deny.
    """
    reasons = set()
    if not isinstance(policy, Mapping) or set(policy) != {"version", "protected", "services"}:
        return {"pathEligible": False, "reasons": ["invalid-policy"], "authorization": False}
    protected, services = policy["protected"], policy["services"]
    valid = (type(policy["version"]) is int and policy["version"] == 1
             and isinstance(protected, list) and bool(protected)
             and all(canonical_path(p) for p in protected) and isinstance(services, dict))
    for rule in services.values() if isinstance(services, dict) else []:
        valid = valid and (isinstance(rule, dict) and set(rule) == {"actorIds", "paths"}
            and isinstance(rule.get("actorIds"), list) and bool(rule["actorIds"])
            and all(type(n) is int and n > 0 for n in rule["actorIds"])
            and isinstance(rule.get("paths"), list) and bool(rule["paths"])
            and all(canonical_path(p) for p in rule["paths"]))
    if not valid:
        return {"pathEligible": False, "reasons": ["invalid-policy"], "authorization": False}
    # Overlapping grants make ownership ambiguous even for an otherwise valid diff.
    grants = [(name, p) for name, rule in services.items() for p in rule["paths"]]
    if any(a != b and (within(p, q) or within(q, p))
           for a, p in grants for b, q in grants):
        reasons.add("ambiguous-ownership")
    rule = services.get(service) if isinstance(service, str) else None
    if rule is None:
        reasons.add("unknown-service")
    elif type(actor_id) is not int or actor_id not in rule["actorIds"]:
        reasons.add("actor-not-authorized")
    if (not isinstance(files, list) or type(changed_files) is not int
            or not 0 < changed_files < 3000 or len(files) != changed_files):
        reasons.add("incomplete-or-empty-diff")
        files = []
    seen = set()
    for entry in files:
        if not isinstance(entry, dict):
            reasons.add("invalid-file-entry")
            continue
        path, status = entry.get("filename"), entry.get("status")
        if not canonical_path(path):
            reasons.add("invalid-path")
            continue
        if path in seen:
            reasons.add("duplicate-path")
        seen.add(path)
        if not isinstance(status, str) or status not in {"added", "modified", "removed", "renamed"}:
            reasons.add("unsupported-file-status")
        paths = [path]
        if status == "renamed":
            old = entry.get("previous_filename")
            if not canonical_path(old):
                reasons.add("missing-or-invalid-rename-source")
            else:
                paths.append(old)
        elif "previous_filename" in entry:
            reasons.add("unexpected-rename-source")
        for changed in paths:
            if any(within(changed, p) for p in protected):
                reasons.add("protected-path")
            if rule is None or not any(within(changed, p) for p in rule["paths"]):
                reasons.add("outside-service-scope")
    return {"pathEligible": not reasons, "reasons": sorted(reasons), "authorization": False}
