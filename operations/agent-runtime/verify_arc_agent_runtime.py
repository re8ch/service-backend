#!/usr/bin/env python3
"""Static fail-closed checks for the ephemeral R640 agent runner contract."""

from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "clusters/re8ch/components/arc-agent-runtime"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def load(name: str):
    return yaml.safe_load((COMPONENT / name).read_text())


def main() -> None:
    controller = load("controller-values.yaml")
    runner = load("runner-values.yaml")

    require(controller["flags"]["watchSingleNamespace"] == "arc-runners",
            "ARC controller must be namespace bounded")
    require(controller["flags"]["terminatedRunnerPodGracePeriodSeconds"] == 0,
            "completed runner Pods must be removed immediately")
    require(controller["nodeSelector"]["kubernetes.io/hostname"] == "r640",
            "ARC controller must remain on R640")

    require(runner["githubConfigUrl"] == "https://github.com/re8ch",
            "runner scale set must remain organization scoped")
    require(runner["githubConfigSecret"] == "arc-re8ch-agent-github-app",
            "runner authentication must use the approved Secret reference")
    require(not isinstance(runner["githubConfigSecret"], dict),
            "inline GitHub credentials are forbidden")
    require(runner["runnerScaleSetName"] == "r640-agent-ephemeral",
            "workflow label is part of the external contract")
    require(runner["minRunners"] == 0 and runner["maxRunners"] <= 16,
            "runner pool must be bounded and have no persistent idle runner")
    require(runner["containerMode"]["type"] == "kubernetes-novolume",
            "jobs must run in isolated Kubernetes job Pods")

    pod = runner["template"]["spec"]
    require(pod["nodeSelector"]["kubernetes.io/hostname"] == "r640",
            "ephemeral runners must remain on R640")
    container = pod["containers"][0]
    require(container["name"] == "runner", "ARC customization must target runner")
    require("@sha256:" in container["image"], "runner image must be digest pinned")
    security = container["securityContext"]
    require(security["allowPrivilegeEscalation"] is False,
            "runner privilege escalation must be disabled")
    require(security["capabilities"]["drop"] == ["ALL"],
            "runner Linux capabilities must be dropped")

    text = "\n".join(path.read_text() for path in COMPONENT.glob("*.yaml"))
    forbidden = ("github_token:", "github_app_private_key:", "LITELLM_MASTER_KEY")
    require(not any(value in text for value in forbidden),
            "credential material or master-key references are forbidden")
    print("PASS ephemeral R640 ARC contract")


if __name__ == "__main__":
    main()

