#!/usr/bin/env python3
"""Static fail-closed checks for the portable self-hosted agent runner contract."""

from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "clusters/re8ch/components/arc-agent-runtime"
BYOK_SMOKE = ROOT / ".github/workflows/copilot-byok-arc-smoke.yml"


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
    cluster_egress = next(
        (policy for policy in policies
         if policy["metadata"]["name"] == "allow-cluster-egress"),
        None,
    )
    require(cluster_egress is not None,
            "Kubernetes job hooks must reach the cluster service and Pod CIDRs")
    require(cluster_egress["spec"]["egress"] == [{"to": [
                {"ipBlock": {"cidr": "10.42.0.0/16"}},
                {"ipBlock": {"cidr": "10.43.0.0/16"}},
            ]}], "cluster egress must remain bounded to Pod and Service CIDRs")
    cilium_policies = [
        document for document in yaml.safe_load_all(
            (COMPONENT / "namespaces-and-policy.yaml").read_text()
        ) if document and document.get("kind") == "CiliumNetworkPolicy"
    ]
    api_egress = next(
        (policy for policy in cilium_policies
         if policy["metadata"]["name"] == "allow-kube-apiserver-egress"),
        None,
    )
    require(api_egress is not None,
            "Cilium must identify the kube-apiserver across service translation")
    require(api_egress["spec"]["egress"] == [{
                "toEntities": ["kube-apiserver"],
                "toPorts": [{"ports": [
                    {"protocol": "TCP", "port": "443"},
                    {"protocol": "TCP", "port": "6443"},
                ]}],
            }], "API egress must remain entity-bound and port-limited")
    require(
        "gha-runner-scale-set-controller@sha256:4b51a20db717b194e2f3fa96cfe2ddcb21602811f79053c75fd1a383517b9f90"
        in rendered_controller,
        "rendered controller image must be digest pinned",
    )
    require("kind: AutoscalingRunnerSet" in rendered_runner,
            "rendered runner scale set must be present")
    require("name: re8ch-self-hosted-ephemeral" in rendered_runner,
            "rendered runner scale-set identity must be stable")

    byok_smoke = BYOK_SMOKE.read_text()
    require("labels: re8ch-self-hosted-ephemeral" in byok_smoke,
            "BYOK smoke must use the ARC scale-set label")
    require("node:22-bookworm-slim@sha256:" in byok_smoke,
            "BYOK job image must be digest pinned")
    require("SSL_CERT_FILE: /tmp/node-root-certificates.pem" in byok_smoke,
            "Copilot native HTTP must use the job's reviewed CA roots")
    require("@github/copilot@1.0.116" in byok_smoke,
            "Copilot CLI version must be pinned")
    require("COPILOT_OFFLINE: \"true\"" in byok_smoke,
            "seat-independent BYOK jobs must disable GitHub model traffic")
    require("https://litellm.service.re8ch.com/v1" in byok_smoke,
            "BYOK jobs must use the public authenticated LiteLLM endpoint")
    require("secrets.COPILOT_PROVIDER_API_KEY" in byok_smoke,
            "BYOK jobs must receive only the scoped provider key")
    require("--secret-env-vars=COPILOT_PROVIDER_API_KEY" in byok_smoke,
            "Copilot CLI must redact the provider key from output")

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

