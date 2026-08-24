#!/usr/bin/env python3
"""Build the immutable CN GeoDB snapshot outside Kubernetes.

Input files are taken from a pinned checkout of MetaCubeX/meta-rules-dat's
``meta`` branch.  Kubernetes jobs never fetch GitHub at runtime.
"""
import argparse, gzip, hashlib, ipaddress, json, pathlib, re

p = argparse.ArgumentParser()
p.add_argument("--source-dir", required=True)
p.add_argument("--source-revision", required=True)
p.add_argument("--output", required=True)
p.add_argument("--canary-categories", default="google,youtube,openai,github,telegram,twitter,netflix")
args = p.parse_args()

source = pathlib.Path(args.source_dir)
site_path = source / "geo/geosite/cn.yaml"
ip_path = source / "geo/geoip/cn.yaml"
DOMAIN = re.compile(r"^(?:\+\.)?([a-z0-9](?:[a-z0-9._-]{0,251}[a-z0-9])?)$", re.I)
COMMON_CC_SLD = {"ac", "co", "com", "edu", "gov", "net", "org"}


def registrable(domain):
    labels = domain.split(".")
    if len(labels) < 2:
        return None
    count = 3 if len(labels[-1]) == 2 and labels[-2] in COMMON_CC_SLD and len(labels) >= 3 else 2
    return ".".join(labels[-count:])


def payload(path):
    values = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line.startswith("- "):
            values.append(line[2:].strip().strip("'\""))
    return values


domains = set()
rejected = 0
for value in payload(site_path):
    match = DOMAIN.fullmatch(value.lower().rstrip("."))
    if match:
        domains.add(match.group(1).lstrip("."))
    else:
        rejected += 1

networks = [ipaddress.ip_network(value) for value in payload(ip_path)]
collapsed_networks = []
for version in (4, 6):
    collapsed_networks.extend(ipaddress.collapse_addresses(
        [value for value in networks if value.version == version]))
collapsed = sorted((str(value) for value in collapsed_networks),
                   key=lambda value: (ipaddress.ip_network(value).version,
                                      int(ipaddress.ip_network(value).network_address),
                                      ipaddress.ip_network(value).prefixlen))

source_hashes = {}
for name, path in (("geosite_cn", site_path), ("geoip_cn", ip_path)):
    source_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()

canary_domains = set()
for category in args.canary_categories.split(","):
    category = category.strip()
    if not category:
        continue
    category_path = source / f"geo/geosite/{category}.yaml"
    source_hashes[f"geosite_{category}"] = hashlib.sha256(category_path.read_bytes()).hexdigest()
    for value in payload(category_path):
        match = DOMAIN.fullmatch(value.lower().rstrip("."))
        if match:
            base = registrable(match.group(1).lstrip("."))
            if base:
                canary_domains.add(base)

data = {
    "schema_version": "re8ch.geodb/v1",
    "version": args.source_revision,
    "source": "MetaCubeX/meta-rules-dat@meta",
    "source_sha256": source_hashes,
    "cn_cidrs": collapsed,
    "cn_domains": sorted(domains),
    "rejected_geosite_entries": rejected,
    "profiles": {"overseas_canary_domains": sorted(canary_domains)},
}
raw = (json.dumps(data, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode()
with gzip.GzipFile(args.output, "wb", mtime=0) as target:
    target.write(raw)
artifact = pathlib.Path(args.output).read_bytes()
print(json.dumps({
    "artifact": args.output,
    "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
    "uncompressed_sha256": hashlib.sha256(raw).hexdigest(),
    "cn_domains": len(domains),
    "cn_cidrs_source": len(networks),
    "cn_cidrs_collapsed": len(collapsed),
    "rejected": rejected,
    "compressed_bytes": len(artifact),
    "overseas_canary_domains": len(canary_domains),
}, sort_keys=True))
