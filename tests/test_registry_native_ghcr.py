import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest
import yaml


BASE = Path(__file__).resolve().parents[1] / "clusters/re8ch/components/registry-authority"
SPEC = importlib.util.spec_from_file_location("ghcr_native", BASE / "ghcr-native.py")
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)

REGISTRY = '''mirrors:
  docker.io:
    endpoint: ["https://registry.re8ch.com"]
  ghcr.io:
    endpoint: ["https://registry.re8ch.com"]
    rewrite: {"^(.*)$": "proxy-ghcr/$1"}
  quay.io:
    endpoint: ["https://registry.re8ch.com"]
configs:
  ghcr.io:
    auth: {auth: "opaque-ghcr-fixture-not-a-token"}
  registry.re8ch.com:
    auth: {password: REDACTED}
'''
HOSTS = '''server = "https://ghcr.io/v2"
capabilities = ["pull", "resolve", "push"]
ca = "/private/existing-ca.pem"
[header]
  X-Existing = ["opaque-ghcr-header-fixture"]
[host."https://registry.re8ch.com/v2"]
  capabilities = ["pull", "resolve"]
[host."https://registry.re8ch.com/v2".rewrite]
  "^(.*)$" = "proxy-ghcr/$1"
[host."https://registry.re8ch.com/v2".header]
  Authorization = ["opaque-harbor-header-fixture"]
'''


def sandbox(tmp_path, registry=REGISTRY, hosts=HOSTS):
    config = tmp_path / native.CONFIG.lstrip("/")
    config.parent.mkdir(parents=True)
    config.write_text('version = 3\n[plugins."io.containerd.cri.v1.images".registry]\n'
                      + 'config_path = "' + native.HOSTS_DIR + '"\n')
    durable = tmp_path / native.REGISTRIES.lstrip("/")
    durable.parent.mkdir(parents=True)
    durable.write_text(registry)
    effective = tmp_path / native.HOSTS_DIR.lstrip("/") / "ghcr.io/hosts.toml"
    effective.parent.mkdir(parents=True)
    effective.write_text(hosts)
    return durable, effective


@pytest.mark.parametrize("key", ["ghcr.io", "'ghcr.io'", '"ghcr.io"'])
def test_removes_only_ghcr_mirror_without_serializing_auth(key):
    raw = REGISTRY.replace("  ghcr.io:\n", "  " + key + ":\n", 1)
    result = native.without_ghcr_mirror(raw)
    expected = raw[:raw.index("  " + key + ":\n")] + raw[raw.index("  quay.io:\n"):]
    assert result == expected
    assert result[result.index("configs:\n"):] == REGISTRY[REGISTRY.index("configs:\n"):]
    assert native.without_ghcr_mirror(result) == result


def test_only_mirror_becomes_empty_map_and_configs_remain_opaque():
    raw = 'mirrors:\n  ghcr.io: {endpoint: ["https://registry.re8ch.com"]}\nconfigs:\n  untouched: true\n'
    assert native.without_ghcr_mirror(raw) == "mirrors: {}\nconfigs:\n  untouched: true\n"


def test_native_hosts_never_moves_harbor_auth_to_canonical():
    result = native.canonical_hosts(HOSTS)
    assert result == HOSTS[:HOSTS.index('[host."https://registry.re8ch.com/v2"]')]
    assert "opaque-ghcr-header-fixture" in result
    assert "opaque-harbor-header-fixture" not in result
    assert "proxy-ghcr" not in result
    assert native.canonical_hosts(result) == result
    assert "host" not in tomllib.loads(result)
    assert tomllib.loads(result)["server"] == "https://ghcr.io/v2"


@pytest.mark.parametrize("hosts", [
    HOSTS.replace("registry.re8ch.com", "unreviewed.example"),
    HOSTS.replace('server = "https://ghcr.io/v2"', 'server = "https://other.example"'),
    'server = "https://ghcr.io"\n[host."https://ghcr.io".rewrite]\n".*"="changed"\n',
])
def test_unknown_authority_fails_before_either_file_changes(tmp_path, hosts):
    durable, effective = sandbox(tmp_path, hosts=hosts)
    before = durable.read_bytes(), effective.read_bytes()
    with pytest.raises(ValueError):
        native.activate(tmp_path, b"")
    assert (durable.read_bytes(), effective.read_bytes()) == before
    assert not list(tmp_path.rglob("*.pre-re8ch-ghcr-native"))


@pytest.mark.parametrize("raw", [
    'mirrors: {ghcr.io: {endpoint: ["https://registry.re8ch.com"]}}\nconfigs: {}\n',
    'mirrors:\n    ghcr.io: {}\nconfigs: {}\n',
    '"mirrors":\n  ghcr.io: {}\nconfigs: {}\n',
    'mirrors:\n  ghcr.io: &shared {}\n  docker.io: *shared\nconfigs: {}\n',
])
def test_unknown_yaml_layout_is_not_silently_treated_as_no_mirror(raw):
    with pytest.raises(ValueError):
        native.without_ghcr_mirror(raw)


def test_check_stale_guard_atomic_private_backups_and_idempotence(tmp_path):
    durable, effective = sandbox(tmp_path)
    before = durable.read_bytes(), effective.read_bytes()
    checked = native.activate(tmp_path, b"", check=True)
    assert checked["durableChanged"] and checked["effectiveChanged"]
    assert (durable.read_bytes(), effective.read_bytes()) == before
    assert not list(tmp_path.rglob("*.pre-re8ch-ghcr-native"))
    with pytest.raises(ValueError):
        native.activate(tmp_path, b"", expected="stale")
    native.activate(tmp_path, b"", expected=checked["fingerprint"])
    assert durable.read_text() == native.without_ghcr_mirror(REGISTRY)
    assert effective.read_text() == native.canonical_hosts(HOSTS)
    for path, previous in zip((durable, effective), before):
        backup = path.with_name(path.name + ".pre-re8ch-ghcr-native")
        assert backup.read_bytes() == previous
        assert backup.stat().st_mode & 0o777 == 0o600
        assert path.stat().st_mode & 0o777 == 0o600
    mtimes = durable.stat().st_mtime_ns, effective.stat().st_mtime_ns
    again = native.activate(tmp_path, b"")
    assert not again["durableChanged"] and not again["effectiveChanged"]
    assert (durable.stat().st_mtime_ns, effective.stat().st_mtime_ns) == mtimes


def test_wrong_active_hosts_dir_or_symlink_leaves_state_untouched(tmp_path):
    durable, effective = sandbox(tmp_path)
    config = tmp_path / native.CONFIG.lstrip("/")
    config.write_text('config_path="/unreviewed/path"\n')
    with pytest.raises(ValueError):
        native.activate(tmp_path, b"")
    assert durable.read_text() == REGISTRY and effective.read_text() == HOSTS
    config.write_text('config_path="' + native.HOSTS_DIR + '"\n')
    durable.rename(durable.with_suffix(".real"))
    durable.symlink_to(durable.with_suffix(".real"))
    with pytest.raises(ValueError):
        native.activate(tmp_path, b"")


def test_node_bound_check_cannot_be_used_on_another_node(tmp_path, monkeypatch):
    durable, effective = sandbox(tmp_path)
    monkeypatch.setenv("NODE_NAME", "r640")
    checked = native.activate(tmp_path, b"", check=True)
    monkeypatch.setenv("NODE_NAME", "another-node")
    with pytest.raises(ValueError):
        native.activate(tmp_path, b"", expected=checked["fingerprint"])
    assert durable.read_text() == REGISTRY and effective.read_text() == HOSTS


def test_cli_never_prints_auth_and_configmap_projection_is_exact(tmp_path):
    sandbox(tmp_path)
    result = subprocess.run([sys.executable, str(BASE / "ghcr-native.py"), "--root", str(tmp_path), "--check"],
                            input=b"", capture_output=True, check=True)
    assert "opaque-" not in (result.stdout + result.stderr).decode()
    assert json.loads(result.stdout)["restarted"] is False
    objects = list(yaml.safe_load_all((BASE / "node-installer.yaml").read_text()))
    config = next(o for o in objects if o["kind"] == "ConfigMap")
    assert config["data"]["ghcr-native.py"] == (BASE / "ghcr-native.py").read_text()
    assert "ghcr.io" not in yaml.safe_load(config["data"]["registries-prefix.yaml"])["mirrors"]
    script = config["data"]["install.sh"]
    assert "systemctl" not in script and "hosts_work" not in script and "proxy_dropin" not in script
    daemon = next(o for o in objects if o["kind"] == "DaemonSet")
    assert next(e["value"] for e in daemon["spec"]["template"]["spec"]["containers"][0]["env"]
                if e["name"] == "RESTART_K3S") == "false"
