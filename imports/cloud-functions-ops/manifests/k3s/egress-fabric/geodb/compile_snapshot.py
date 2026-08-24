#!/usr/bin/env python3
"""Compile normalized CN CIDR/domain inputs into one hashed GeoDB snapshot."""
import argparse, hashlib, ipaddress, json, pathlib, sys

p = argparse.ArgumentParser()
p.add_argument("--cidrs", required=True, help="one CIDR per line")
p.add_argument("--domains", required=True, help="one domain suffix per line")
p.add_argument("--version", required=True)
p.add_argument("--output", required=True)
args = p.parse_args()

def lines(path):
    return [x.strip() for x in pathlib.Path(path).read_text().splitlines()
            if x.strip() and not x.lstrip().startswith("#")]

cidrs = sorted({str(ipaddress.ip_network(x)) for x in lines(args.cidrs)})
domains = sorted({x.lower().lstrip(".").rstrip(".") for x in lines(args.domains)})
data = {"version": args.version, "cn_cidrs": cidrs, "cn_domains": domains}
raw = (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()
pathlib.Path(args.output).write_bytes(raw)
print(json.dumps({"output": args.output, "sha256": hashlib.sha256(raw).hexdigest(),
                  "cidrs": len(cidrs), "domains": len(domains)}))
