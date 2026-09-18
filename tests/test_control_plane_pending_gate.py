import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "control_plane_pending_gate",
    ROOT / "operations/scripts/control-plane-pending-gate.py",
)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
TARGETS = {"a1-wsl-zt", "b1-wsl-zt", "r640"}


def pod(**spec):
    return {"spec": spec}


def test_unconstrained_or_control_plane_bound_pods_are_in_scope():
    assert gate.can_target_control_plane(pod(), TARGETS)
    assert gate.can_target_control_plane(pod(nodeName="r640"), TARGETS)
    assert gate.can_target_control_plane(
        pod(nodeSelector={"kubernetes.io/hostname": "a1-wsl-zt"}), TARGETS
    )


def test_pods_pinned_to_other_nodes_do_not_block_the_taint_gate():
    assert not gate.can_target_control_plane(pod(nodeName="zan"), TARGETS)
    assert not gate.can_target_control_plane(
        pod(nodeSelector={"kubernetes.io/hostname": "amdwsl-zt"}), TARGETS
    )
    assert not gate.can_target_control_plane(
        pod(
            affinity={
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [
                            {
                                "matchFields": [
                                    {
                                        "key": "metadata.name",
                                        "operator": "In",
                                        "values": ["muaya-lightweight"],
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        ),
        TARGETS,
    )
