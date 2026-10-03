"""The MUBAO SOCKS transit remains an exact, reversible RouterOS change."""

import importlib.util
from pathlib import Path
import sys


SCRIPT = Path(__file__).parents[1] / "operations/network/scripts/routeros/mubao_global_exit_route.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("mubao_global_exit_route", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Router:
    def __init__(self):
        self.objects = {
            "/interface/wireguard/peers": [{".id": "*A", "interface": "wg-cp-a1",
                                             "comment": module.PEER_COMMENT,
                                             "allowed-address": module.BASE_ALLOWED}],
            "/ip/route": [],
            "/ip/firewall/filter": [{".id": "*8", "comment": "defconf: fasttrack"}],
        }
        self.moves = []

    def rows(self, path):
        return self.objects[path]

    def update(self, path, item_id, body):
        next(item for item in self.objects[path] if item[".id"] == item_id).update(body)

    def create(self, path, body):
        item = {".id": f"*{len(self.objects[path]) + 10}", **body}
        self.objects[path].append(item)
        return item

    def delete(self, path, item_id):
        self.objects[path][:] = [item for item in self.objects[path] if item[".id"] != item_id]

    def move_before(self, path, item_id, destination_id):
        self.moves.append((path, item_id, destination_id))


def test_exact_socks_path_stages_and_rolls_back_idempotently():
    router = Router()
    assert module.reconcile(router, "stage") == {
        "peerAllowsExit": True, "route": True, "forward": True, "return": True,
    }
    assert len(router.moves) == 2
    route = router.rows("/ip/route")[0]
    assert (route["dst-address"], route["gateway"]) == ("10.250.0.220/32", "172.31.96.2")
    rules = router.rows("/ip/firewall/filter")[1:]
    assert {rule["dst-port"] for rule in rules if "dst-port" in rule} == {"1080"}
    assert {rule["src-port"] for rule in rules if "src-port" in rule} == {"1080"}
    module.reconcile(router, "stage")
    assert len(router.rows("/ip/route")) == 1
    assert module.reconcile(router, "rollback") == {
        "peerAllowsExit": False, "route": False, "forward": False, "return": False,
    }
