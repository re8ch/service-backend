#!/usr/bin/env python3
"""Install an isolated, route-free WSL WireGuard link to RouterOS.

Run as root on the named WSL node. The private key stays in /etc/wireguard
and is embedded only in the root-readable wg-quick config, never in output.
"""

import argparse
import ipaddress
import json
import os
import pathlib
import subprocess


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--action", choices=("stage", "start", "inspect", "stop"), required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("run as root")
    desired = json.loads(pathlib.Path(args.inventory).read_text(encoding="utf-8"))
    links = {link["node"]: link for link in desired["links"]}
    link = links[args.node]
    interface = link["interface"]
    key_file = pathlib.Path("/etc/wireguard") / f"{interface}.key"
    conf = pathlib.Path("/etc/wireguard") / f"{interface}.conf"
    router = ipaddress.ip_interface(link["routerAddress"])
    peer = ipaddress.ip_network(link["peerAddress"]).network_address
    peer_address = f"{peer}/{router.network.prefixlen}"
    router_ip = str(router.ip)
    if args.action in ("stage", "start"):
        if not key_file.is_file():
            raise RuntimeError(f"missing existing private key: {key_file}")
        private = key_file.read_text(encoding="ascii").strip()
        public = subprocess.run(["wg", "pubkey"], input=private + "\n", check=True,
                                capture_output=True, text=True).stdout.strip()
        if public != link["peerPublicKey"]:
            raise RuntimeError("local public key differs from the inventory")
        content = (
            "[Interface]\n"
            f"Address = {peer_address}\n"
            f"PrivateKey = {private}\n"
            f"MTU = {desired['mtu']}\n"
            "Table = off\n\n"
            "[Peer]\n"
            f"PublicKey = {link['routerPublicKey']}\n"
            f"Endpoint = {desired['publicEndpoint']}:{link['listenPort']}\n"
            f"AllowedIPs = {router_ip}/32,192.0.2.65/24\n"
            "PersistentKeepalive = 25\n"
        )
        old_content = content
        middle_content = content.replace(
            "Table = off\n\n",
            "Table = off\n"
            "PostUp = ip route replace 192.0.2.34/28 dev %i\n"
            "PostDown = ip route del 192.0.2.34/28 dev %i || true\n\n",
        ).replace(
            f"AllowedIPs = {router_ip}/32,192.0.2.65/24",
            "AllowedIPs = 192.0.2.34/28,192.0.2.65/24",
        )
        content = middle_content.replace(
            "AllowedIPs = 192.0.2.34/28,192.0.2.65/24",
            "AllowedIPs = 192.0.2.34/28,192.0.2.95/24,192.0.2.65/24",
        )
        if conf.exists() and conf.read_text(encoding="ascii") not in (old_content, middle_content, content):
            raise RuntimeError("refusing to replace a different WireGuard config")
        if not conf.exists() or conf.read_text(encoding="ascii") != content:
            target = conf if not conf.exists() else conf.with_suffix(".conf.new")
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                stream.write(content)
            if target != conf:
                os.replace(target, conf)
        os.chmod(conf, 0o600)
        if args.action == "start":
            run("systemctl", "enable", "--now", f"wg-quick@{interface}.service")
            run("wg", "set", interface, "peer", link["routerPublicKey"],
                "allowed-ips", "192.0.2.34/28,192.0.2.95/24,192.0.2.65/24")
            run("ip", "route", "replace", "192.0.2.34/28", "dev", interface)
    elif args.action == "stop":
        run("systemctl", "disable", "--now", f"wg-quick@{interface}.service")
    if args.action in ("start", "inspect"):
        status = run("systemctl", "is-active", f"wg-quick@{interface}.service")
        handshake = run("wg", "show", interface, "latest-handshakes") if status == "active" else ""
        print(json.dumps({"node": args.node, "interface": interface,
                          "service": status, "latestHandshake": handshake}))
    else:
        print(json.dumps({"node": args.node, "interface": interface, "action": args.action}))


if __name__ == "__main__":
    main()
