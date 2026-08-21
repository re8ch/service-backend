import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location("controller", pathlib.Path(__file__).with_name("controller.py"))
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)


class DesiredTest(unittest.TestCase):
    def service(self):
        return {"apiVersion":"devices.re8ch.com/v1alpha1","kind":"DeviceService",
          "metadata":{"name":"gxw","namespace":"edge-devices","uid":"u1"},
          "spec":{"deviceRef":{"name":"gxw"},"connectionName":"lan","protocol":"HTTP","port":80,
          "authentication":{"mode":"Upstream"},
          "exposure":{"mode":"Gateway","hostname":"gxw.example.test","visibility":"Authenticated",
          "gatewayRef":{"name":"gw","namespace":"kube-system","sectionName":"http"}}}}

    def device(self, address="192.0.2.4"):
        return {"spec":{"connections":[{"name":"lan","type":"Network","network":{"addresses":[address]}}]}}

    def test_native_resources(self):
        objects = c.desired_objects(self.service(), self.device())
        self.assertEqual([x["kind"] for x in objects], ["Service", "EndpointSlice", "HTTPRoute"])
        self.assertEqual(objects[1]["endpoints"][0]["addresses"], ["192.0.2.4"])
        self.assertEqual(objects[2]["spec"]["hostnames"], ["gxw.example.test"])

    def test_rejects_documentation_address(self):
        with self.assertRaisesRegex(ValueError, "documentation"):
            c.desired_objects(self.service(), self.device("192.0.2.249"))

    def test_rejects_wrong_connection(self):
        service = self.service(); service["spec"]["connectionName"] = "missing"
        with self.assertRaisesRegex(ValueError, "not found"):
            c.desired_objects(service, self.device())

    def test_rejects_unimplemented_external_auth(self):
        service = self.service(); service["spec"]["authentication"] = {"mode":"ExternalAuth"}
        with self.assertRaisesRegex(ValueError, "upstream authentication"):
            c.desired_objects(service, self.device())


if __name__ == "__main__": unittest.main()
