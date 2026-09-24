from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_node_dns_rollout_executes_the_mounted_installer() -> None:
    script = (ROOT / "operations/scripts/rollout-node-dns.sh").read_text()
    assert "command: [/bin/sh, /config/install.sh]" in script
    assert "mountPath: /config" in script


def test_node_dns_declares_public_and_split_dns_routes() -> None:
    manifest = (ROOT / "clusters/re8ch/components/node-dns/node-installer.yaml").read_text()
    assert 'resolvectl dns "$public_iface" 1.1.1.1 223.5.5.5' in manifest
    assert 'resolvectl default-route "$public_iface" yes' in manifest
    assert 'resolvectl domain "$iface" ~zt.re8ch.com' in manifest
    assert 'resolvectl default-route "$iface" no' in manifest
