import importlib.util
import json
from pathlib import Path
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
NAMES = {'source-controller', 'kustomize-controller', 'helm-controller', 'notification-controller',
         'image-reflector-controller', 'image-automation-controller', 'release-compat-controller', 're8ch-release-dispatcher'}

@pytest.mark.parametrize('path', ['clusters/re8ch', 'clusters/re8ch/flux-federation', 'clusters/re8ch/components/release-compat'])
def test_retired_workloads_cannot_return_from_any_render_entrypoint(kustomize_documents, path):
    docs = kustomize_documents(path)
    assert not [d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] in NAMES]
    assert docs  # The retained declarations are not replaced by an empty component.


def test_deletion_plan_is_exact_and_preserves_unrelated_resources():
    plan = json.loads((ROOT / 'operations/releases/flux-retirement-inventory.json').read_text())
    rows = plan['deployments']
    assert len(rows) == 24
    assert len({r['uid'] for r in rows}) == 24
    assert len({(r['namespace'], r['name']) for r in rows}) == 24
    assert sum(r['requiresQuiesce'] for r in rows) == 2
    assert plan['propagationPolicy'] == 'Foreground'
    for r in rows:
        assert r['kind'] == 'Deployment' and r['apiVersion'] == 'apps/v1'
        assert r['name'] in NAMES and r['uid'] and r['resourceVersion']
        assert (ROOT / r['source']).is_file()


def test_retired_promoter_has_no_side_effect_path():
    path = ROOT / 'operations/releases/promote_release_compat.py'
    spec = importlib.util.spec_from_file_location('retired_promoter', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(RuntimeError, match='retired'):
        module.main()
    workflow = yaml.safe_load((ROOT / '.github/workflows/release-compat-controller.yml').read_text())
    assert workflow['jobs']['release']['if'] == '${{ false }}'
