#!/usr/bin/env python3
"""Static fail-closed checks for the portable self-hosted agent runner contract."""

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

    require(controller["flags"]["watchSingleNamespace"] == "re8ch-arc-runners",
            "ARC controller must be namespace bounded")
    require(controller["flags"]["terminatedRunnerPodGracePeriodSeconds"] == 0,
            "completed runner Pods must be removed immediately")
    require(controller["nodeSelector"] == {
                "scheduling.re8ch.com/agent-runtime": "true",
            }, "ARC controller must use the portable self-hosted node pool")
    require(runner["controllerServiceAccount"] == {
                "namespace": "arc-systems",
                "name": "arc-controller-gha-rs-controller",
            }, "runner RBAC must bind the rendered controller identity")

    require(runner["githubConfigUrl"] == "https://github.com/re8ch",
            "runner scale set must remain organization scoped")
    require(runner["githubConfigSecret"] == "arc-re8ch-agent-github-app",
            "runner authentication must use the approved Secret reference")
    require(not isinstance(runner["githubConfigSecret"], dict),
            "inline GitHub credentials are forbidden")
    require(runner["runnerGroup"] == "RE8CH Self-Hosted",
            "runner group must use the portable self-hosted contract")
    require(runner["runnerScaleSetName"] == "re8ch-self-hosted-ephemeral",
            "workflow label is part of the external contract")
    require(runner["minRunners"] == 0 and runner["maxRunners"] <= 16,
            "runner pool must be bounded and have no persistent idle runner")
    require(runner["containerMode"]["type"] == "kubernetes-novolume",
            "jobs must run in isolated Kubernetes job Pods")

    pod = runner["template"]["spec"]
    require(pod["nodeSelector"] == {
                "scheduling.re8ch.com/agent-runtime": "true",
            }, "ephemeral runners must use the portable self-hosted node pool")
    container = pod["containers"][0]
    require(container["name"] == "runner", "ARC customization must target runner")
    require("@sha256:" in container["image"], "runner image must be digest pinned")
    security = container["securityContext"]
    require(security["allowPrivilegeEscalation"] is False,
            "runner privilege escalation must be disabled")
    require(security["capabilities"]["drop"] == ["ALL"],
            "runner Linux capabilities must be dropped")

    rendered_controller = (COMPONENT / "controller-rendered.yaml").read_text()
    rendered_runner = (COMPONENT / "runner-rendered.yaml").read_text()
    policies = [
        document for document in yaml.safe_load_all(
            (COMPONENT / "namespaces-and-policy.yaml").read_text()
        ) if document and document.get("kind") == "NetworkPolicy"
    ]
    https_egress = next(
        (policy for policy in policies
         if policy["metadata"]["name"] == "allow-https-egress"),
        None,
    )
    require(https_egress is not None,
            "runner namespace must explicitly allow HTTPS egress")
    require(https_egress["spec"]["egress"] == [{
                "ports": [{"protocol": "TCP", "port": 443}],
            }], "runner HTTPS egress must be limited to TCP/443")
    require(
        "gha-runner-scale-set-controller@sha256:4b51a20db717b194e2f3fa96cfe2ddcb21602811f79053c75fd1a383517b9f90"
        in rendered_controller,
        "rendered controller image must be digest pinned",
    )
    require("kind: AutoscalingRunnerSet" in rendered_runner,
            "rendered runner scale set must be present")
    require("name: re8ch-self-hosted-ephemeral" in rendered_runner,
            "rendered runner scale-set identity must be stable")

    text = "\n".join(path.read_text() for path in COMPONENT.glob("*.yaml"))
    # Keep the scanner patterns constructed so this guard does not itself look
    # like a credential declaration to repository-wide secret detection.
    forbidden = (
        "github_" + "token:",
        "github_app_" + "private" + "_key:",
        "LITELLM_" + "MASTER_KEY",
    )
    require(not any(value in text for value in forbidden),
            "credential material or master-key references are forbidden")
    print("PASS portable self-hosted ARC contract")


if __name__ == "__main__":
    main()

