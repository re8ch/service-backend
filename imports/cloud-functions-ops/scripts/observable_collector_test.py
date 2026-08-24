#!/usr/bin/env python3
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import observable_collector as collector


class ObservableCollectorTest(unittest.TestCase):
    def test_parse_aliases(self):
        self.assertEqual(collector.parse_aliases("qwen=qianwen, ledger=ledger"), {"qwen": "qianwen", "ledger": "ledger"})

    def test_parse_catalog_aliases(self):
        text = """
tenants:
  - id: qwen
    observability:
      tenantId: qianwen
      namespace: qwen
  - id: ledger
    observability:
      tenantId: ledger
      namespace: ledger
"""
        self.assertEqual(collector.parse_catalog_aliases(text), {"qwen": "qianwen", "ledger": "ledger"})

    def test_tenant_resolver_uses_namespace_label_and_alias(self):
        resolver = collector.TenantResolver({"qwen": "qianwen"})
        resolver.namespace_labels = {
            "qwen": {"saas.re8ch.com/tenant": "qwen"},
            "ledger": {"saas.re8ch.com/tenant": "ledger"},
        }
        self.assertEqual(resolver.tenant_for_namespace("qwen"), "qianwen")
        self.assertEqual(resolver.tenant_for_namespace("ledger"), "ledger")

    def test_tenant_resolver_unlabeled_namespace_falls_back_to_namespace(self):
        resolver = collector.TenantResolver({})
        resolver.namespace_labels = {"customer-a": {}}
        self.assertEqual(resolver.tenant_for_namespace("customer-a"), "customer-a")
        self.assertEqual(resolver.tenant_for_namespace(""), "platform")

    def test_observable_event_defaults(self):
        event = collector.observable_event(
            tenant_id="",
            namespace="",
            source="k3s-event",
            severity="",
            reason="BackOff",
            object_ref="Pod/qwen/api",
            message="container backoff",
        )
        self.assertEqual(event["tenant_id"], "platform")
        self.assertEqual(event["severity"], "info")
        self.assertEqual(event["status"], "active")
        self.assertTrue(event["fingerprint"])

    def test_pod_event_for_restarts(self):
        resolver = collector.TenantResolver({"qwen": "qianwen"})
        resolver.namespace_labels = {"qwen": {"saas.re8ch.com/tenant": "qwen"}}
        pod = {
            "metadata": {"namespace": "qwen", "name": "api"},
            "status": {
                "phase": "Running",
                "containerStatuses": [{"name": "api", "ready": True, "restartCount": 5}],
            },
        }
        original = collector.kube_request
        try:
            collector.kube_request = lambda path: {"items": [pod]}
            events = collector.pod_events(resolver)
        finally:
            collector.kube_request = original
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["tenant_id"], "qianwen")
        self.assertEqual(events[0]["reason"], "pod_restarts")


if __name__ == "__main__":
    unittest.main()
