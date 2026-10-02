from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_access_subscription_registers_gotrue_pkce_client():
    app = (ROOT / "platform/apps/access-subscription/app.py").read_text()
    resources = list(yaml.safe_load_all((ROOT / "operations/supabase-identity/oauth-clients-access-subscription.yaml").read_text()))
    registration = resources[0]["data"]["clients.sql"]
    assert 'OAUTH_ISSUER = "https://db-rw.re8ch.com/auth/v1"' in app
    assert 'OAUTH_AUTHORIZE_URL = "https://auth.re8ch.com/auth/v1/oauth/authorize"' in app
    assert 'self.redirect(OAUTH_AUTHORIZE_URL + "?"' in app
    assert '"code_challenge_method": "S256"' in app
    assert "e93247af-5bc0-4f3d-829b-8a70e0a69d31" in registration
    assert "https://sub.access.re8ch.com/oauth/callback" in registration
    assert "'public'" in registration and "'none'" in registration
    assert "- oauth-clients-access-subscription.yaml" in (ROOT / "operations/supabase-identity/kustomization.yaml").read_text()
