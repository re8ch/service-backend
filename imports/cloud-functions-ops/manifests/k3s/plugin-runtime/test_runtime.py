import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parent


def load_runtime(definitions=None):
    tmp = Path(tempfile.mkdtemp())
    for name, value in (definitions or {}).items():
        (tmp / f"{name}.json").write_text(json.dumps(value))
    os.environ["PLUGIN_DEFINITIONS_DIR"] = str(tmp)
    spec = importlib.util.spec_from_file_location("plugin_runtime_test", ROOT / "runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plugin(name="demo", source="kubernetes"):
    return {
        "apiVersion": "plugins.re8ch.com/v1alpha1", "kind": "PluginDefinition",
        "metadata": {"name": name},
        "spec": {
            "source": {"type": source}, "identityRef": "plugin-demo",
            "discovery": {},
            "policy": {
                "allowedOperations": ["get", "list", "patch"],
                "deniedFields": ["secret", "password", "token", "privateKey", "clientSecret", "apiKey"],
                "resourceScopes": [{"group": "apps", "version": "v1", "resource": "deployments",
                                    "namespaced": True, "namespaces": ["safe"], "verbs": ["get", "list", "patch"]}],
            },
            "publication": {"enabled": True, "toolPrefix": name},
        },
    }


class RuntimeTests(unittest.TestCase):
    def test_kubernetes_tools_are_stable_and_scoped(self):
        m = load_runtime({"demo": plugin()})
        names = set(m.STORE.tools)
        self.assertEqual(names, {"demo.deployments.get", "demo.deployments.list", "demo.deployments.patch"})
        schema = m.STORE.tools["demo.deployments.patch"]["inputSchema"]
        self.assertEqual(schema["properties"]["namespace"]["enum"], ["safe"])
        self.assertIn("object", schema["required"])

    def test_secret_fields_are_rejected_recursively(self):
        m = load_runtime({"demo": plugin()})
        self.assertTrue(m.contains_denied({"spec": {"clientSecret": "x"}}, {"clientsecret"}))
        self.assertFalse(m.contains_denied({"spec": {"replicas": 2}}, {"clientsecret"}))

    def test_arbitrary_shell_is_rejected(self):
        value = plugin(source="cli")
        value["spec"]["discovery"] = {"commands": [{"name": "bad", "argv": ["sh", "-c", "id"]}]}
        value["spec"]["policy"]["allowedOperations"] = ["bad"]
        with self.assertRaises(Exception):
            load_runtime({"demo": value})

    def test_unavailable_shape_is_stable(self):
        m = load_runtime({"demo": plugin()})
        value = m.unavailable("demo", "health_check_failed")
        self.assertEqual(value["code"], "PLUGIN_UNAVAILABLE")
        self.assertTrue(value["retryable"])

    def test_disable_keeps_tool_in_catalog(self):
        m = load_runtime({"demo": plugin()})
        before = set(m.STORE.tools)
        m.STORE.set_enabled("demo", False)
        self.assertEqual(before, set(m.STORE.tools))
        result = m.EXECUTOR.execute("demo.deployments.list", {"namespace": "safe"})
        self.assertEqual(result["reason"], "disabled")

    def test_invalid_generation_does_not_replace_current(self):
        m = load_runtime({"demo": plugin()})
        generation = m.STORE.generation
        (m.STORE.root / "bad.json").write_text("not json")
        with self.assertRaises(json.JSONDecodeError):
            m.STORE.sync()
        self.assertEqual(m.STORE.generation, generation)
        self.assertIn("demo.deployments.list", m.STORE.tools)


if __name__ == "__main__":
    unittest.main()
