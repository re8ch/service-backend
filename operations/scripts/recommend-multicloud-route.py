#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = ROOT / "operations" / "config" / "multicloud-routing-policy.json"
DEFAULT_STATE = ROOT / ".multicloud-functions" / "routes.json"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def country_group(policy, country):
    country = country.upper()
    for group, countries in policy.get("countryGroups", {}).items():
        if country in countries:
            return group
    return "default"


def healthy_targets(state):
    return {
        f"{target['providerName']}:{target['region']}"
        for target in state.get("targets", [])
        if target.get("healthy") and target.get("enabled")
    }


def recommend(policy, state, tenant, country):
    tenant_config = policy.get("tenants", {}).get(tenant)
    if not tenant_config or not tenant_config.get("enabled"):
        raise ValueError(f"tenant is not enabled: {tenant}")
    group = country_group(policy, country)
    healthy = healthy_targets(state)
    https_ready = set(policy.get("httpsReadyProviders", []))
    candidates = policy["recommendations"].get(group, policy["recommendations"]["default"])
    selected = [
        candidate
        for candidate in candidates
        if candidate in healthy and candidate.split(":", 1)[0] in https_ready
    ]
    if not selected:
        raise ValueError(f"no healthy HTTPS route for tenant={tenant} country={country}")
    domains = []
    for candidate in selected:
        provider, region = candidate.split(":", 1)
        domains.append(
            {
                "provider": provider,
                "region": region,
                "domain": f"{provider}-{region}.{tenant}.{policy['domainSuffix']}",
            }
        )
    return {"tenant": tenant, "country": country.upper(), "group": group, "routes": domains}


def main():
    parser = argparse.ArgumentParser(description="Recommend healthy RE8CH regional function domains.")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--country", required=True, help="ISO 3166-1 alpha-2 country code")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    args = parser.parse_args()
    try:
        output = recommend(load(args.policy), load(args.state), args.tenant, args.country)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
