import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'operations/network/scripts/la1-public-mesh-persist.py'
SPEC = importlib.util.spec_from_file_location('la1_public_mesh_persist', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_la1_config_admits_api_vip_only_from_r640():
    config = MODULE.desired('la1', '')
    assert 'AllowedIPs = 172.31.92.128/32, 10.250.0.128/32, 10.250.0.1/32' in config
    assert MODULE.desired('la1', config) == config


def test_r640_peer_patch_preserves_existing_peers_and_is_idempotent():
    original = '[Interface]\nAddress = 172.31.92.128/32\n\n[Peer]\nPublicKey = existing\n'
    updated = MODULE.desired('r640', original)
    assert 'PublicKey = existing' in updated
    assert updated.count(MODULE.LA1_KEY) == 1
    assert MODULE.desired('r640', updated) == updated


def test_r640_rejects_partial_existing_peer():
    with pytest.raises(ValueError, match='different fields'):
        MODULE.desired('r640', '[Interface]\n[Peer]\nPublicKey = ' + MODULE.LA1_KEY + '\n')
