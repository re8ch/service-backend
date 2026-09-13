import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("voice_gateway", Path(__file__).with_name("voice_gateway.py"))
vg = importlib.util.module_from_spec(SPEC); sys.modules[SPEC.name] = vg; SPEC.loader.exec_module(vg)


class FakeESL:
    def api(self, command): return "+OK fake-uuid"


class VoiceGatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ.update(VOICE_API_HMAC_SECRET="x" * 32, FREESWITCH_ESL_PASSWORD="y" * 32, VOICE_DB=self.tmp.name + "/voice.db", VOICE_ALLOWED_TENANTS="tenant-a")
        self.app = vg.App(); self.app.esl = FakeESL()

    def tearDown(self): self.tmp.cleanup()

    def payload(self, **overrides):
        data = {"request_id": "r1", "destination": "13800138000", "tenant": "tenant-a", "expires_at": int(time.time()) + 60, "agents": ["a1", "a2"]}
        data.update(overrides); return data

    def test_idempotent_create(self):
        first = self.app.create_call(self.payload()); second = self.app.create_call(self.payload())
        self.assertEqual(first.call_id, second.call_id)

    def test_rejects_fifth_agent(self):
        with self.assertRaises(vg.ApiError): self.app.create_call(self.payload(agents=["a", "b", "c", "d", "e"]))

    def test_rejects_tenant(self):
        with self.assertRaises(vg.ApiError): self.app.create_call(self.payload(tenant="other"))

    def test_secretary_context_is_bound_to_call(self):
        call = self.app.create_call(self.payload(incident_id="inc_test", recipient_id="human-1", destination_verified=True))
        self.assertEqual(self.app.store.context(call.call_id), {
            "incident_id": "inc_test", "recipient_id": "human-1", "destination_verified": True,
        })


if __name__ == "__main__": unittest.main()
