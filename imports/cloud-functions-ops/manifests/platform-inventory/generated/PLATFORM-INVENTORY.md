# RE8CH Live Platform Inventory

Generated; do not edit.

## Nodes

| Node | Ready | Internal IP | External IP | OS |
|---|---:|---|---|---|
| restricted-node-1eb0570f74 | True | 192.0.2.18 |  | Ubuntu 24.04 LTS |
| amdwsl-zt | True | fc64:f13e:b4c:5bfb:7a0f::1 |  | Ubuntu 24.04.1 LTS |
| restricted-node-296f36f2fb | True | 192.0.2.208 |  | Ubuntu 24.04 LTS |
| domain-ex | True | 192.0.2.172 | 192.0.2.72 | Ubuntu 22.04 LTS |
| muaya-ecs | True | 192.0.2.203 | 192.0.2.210 | Ubuntu 26.04 LTS |
| muaya-lightweight | True | 192.0.2.141 | 192.0.2.110 | Ubuntu 24.04.2 LTS |
| overseas-edge-50 | True | 192.0.2.122 | 192.0.2.3 | Ubuntu 24.04.3 LTS |
| overseas-kl | True | 192.0.2.50 | 192.0.2.27 | Ubuntu 24.04 LTS |
| overseas-la | Unknown | 192.0.2.55 | 192.0.2.91 | Ubuntu 26.04 LTS |
| qwen-1 | True | 192.0.2.115 |  | Ubuntu 24.04.4 LTS |
| qwen-2 | True | 192.0.2.91 |  | Ubuntu 24.04.4 LTS |
| qwen-3 | True | 192.0.2.33 |  | Ubuntu 24.04.4 LTS |
| restricted-node-6dc3f140f9 | True | 192.0.2.183 | 192.0.2.79 | Ubuntu 26.04 LTS |
| suxunji | True | 192.0.2.143 | 192.0.2.231 | Ubuntu 22.04 LTS |
| vm-20-12-ubuntu | True | 192.0.2.67 | 192.0.2.99 | Ubuntu 22.04 LTS |
| zan | True | 192.0.2.79 | 192.0.2.67 | Ubuntu 22.04 LTS |

## Long-running services

| Namespace | Kind | Name | State / exposure |
|---|---|---|---|
| agent-collab | Deployment | teable | 1/1 ready |
| agent-collab | Deployment | teable-edge-relay | 1/1 ready |
| agent-collab | Deployment | teable-redis | 1/1 ready |
| agent-collab | Deployment | wechat-content-orchestrator | 1/1 ready |
| agent-collab | Ingress | teable | teable.re8ch.com |
| agent-collab | Service | teable | NodePort ports=3000 |
| agent-collab | Service | teable-edge-relay | NodePort ports=3000 |
| agent-collab | Service | teable-redis | ClusterIP ports=6379 |
| agent-collab | Service | wechat-content-orchestrator | ClusterIP ports=8080 |
| ai-gateway | Deployment | re8ch-ai-gateway | 0/0 ready |
| ai-gateway | Service | re8ch-ai-gateway | ClusterIP ports=4000 |
| ai-operations | Deployment | agent-orchestra-collab | 1/1 ready |
| ai-operations | Deployment | browser-runtime-pool-poc | 0/0 ready |
| ai-operations | Deployment | cloud-platform-tool | 2/2 ready |
| ai-operations | Deployment | cluster-infra-tool | 2/2 ready |
| ai-operations | Deployment | oauth2-proxy-steel | 1/1 ready |
| ai-operations | Deployment | re8ch-cloud-platform | 2/2 ready |
| ai-operations | Deployment | steel-browser | 1/1 ready |
| ai-operations | Deployment | steel-browser-gateway | 1/1 ready |
| ai-operations | Deployment | steel-browser-stealth-canary | 1/1 ready |
| ai-operations | Deployment | steel-browser-ui | 1/1 ready |
| ai-operations | Ingress | steel-browser | browser.re8ch.com |
| ai-operations | Service | agent-orchestra-collab | ClusterIP ports=80 |
| ai-operations | Service | browser-runtime-pool-poc | ClusterIP ports=3000 |
| ai-operations | Service | cloud-platform-tool | ClusterIP ports=80 |
| ai-operations | Service | cluster-infra-tool | ClusterIP ports=80 |
| ai-operations | Service | oauth2-proxy-steel | ClusterIP ports=4180 |
| ai-operations | Service | re8ch-cloud-platform | ClusterIP ports=19443 |
| ai-operations | Service | steel-browser | ClusterIP ports=3000,9223 |
| ai-operations | Service | steel-browser-gateway | ClusterIP ports=8080 |
| ai-operations | Service | steel-browser-stealth-canary | ClusterIP ports=3000,9223 |
| ai-operations | Service | steel-browser-ui | ClusterIP ports=80 |
| anycam | Deployment | nuclio-anycam-model-gateway | 1/1 ready |
| anycam | Service | nuclio-anycam-model-gateway | ClusterIP ports=8080 |
| anysite | Deployment | anysite-postgrest | 1/1 ready |
| anysite | Deployment | scale-api | 2/2 ready |
| anysite | Deployment | scale-worker | 1/1 ready |
| anysite | Ingress | scale-api | api-guangzhou24.re8ch.com, api-shanghai217.re8ch.com, api.re8ch.com |
| anysite | Service | anysite-postgrest | NodePort ports=3000 |
| anysite | Service | scale-api | ClusterIP ports=80 |
| auth | Deployment | authentik-redis | 0/0 ready |
| auth | Deployment | authentik-server | 0/0 ready |
| auth | Deployment | authentik-worker | 0/0 ready |
| auth | Deployment | nuclio-re8ch-auth-gateway | 0/0 ready |
| auth | Deployment | re8ch-auth-gateway | 1/1 ready |
| auth | Ingress | authentik-server | auth.re8ch.com |
| auth | Ingress | re8ch-auth-gateway | api-guangzhou24.re8ch.com, api-shanghai217.re8ch.com, api-shanghai77.re8ch.com, api.re8ch.com |
| auth | Service | authentik-postgres-primary | ClusterIP ports=5432 |
| auth | Service | authentik-redis | ClusterIP ports=6379 |
| auth | Service | authentik-server | ClusterIP ports=80,443 |
| auth | Service | nuclio-re8ch-auth-gateway | ClusterIP ports=8080,8090 |
| auth | Service | re8ch-auth-gateway | ClusterIP ports=8080 |
| cert-manager | Deployment | cert-manager | 1/1 ready |
| cert-manager | Deployment | cert-manager-cainjector | 1/1 ready |
| cert-manager | Deployment | cert-manager-webhook | 1/1 ready |
| cert-manager | Service | cert-manager | ClusterIP ports=9402 |
| cert-manager | Service | cert-manager-cainjector | ClusterIP ports=9402 |
| cert-manager | Service | cert-manager-webhook | ClusterIP ports=443,9402 |
| codex-app-server | Deployment | codex-app-server | 1/1 ready |
| codex-app-server | Service | codex-app-server | ClusterIP ports=4500 |
| codex-platform | Deployment | codex-app-server-console | 1/1 ready |
| codex-platform | Deployment | codex-session-controller | 1/1 ready |
| codex-platform | Deployment | codex-session-web | 1/1 ready |
| codex-platform | Ingress | codex-app-server-console | api-shanghai217.re8ch.com |
| codex-platform | Ingress | codex-session-web | codex.re8ch.com |
| codex-platform | Ingress | codex-session-web-shanghai217 | api-shanghai217.re8ch.com |
| codex-platform | Service | codex-app-server-console | ClusterIP ports=8080 |
| codex-platform | Service | codex-app-server-console-internal | NodePort ports=8080 |
| codex-platform | Service | codex-session-web | ClusterIP ports=8080 |
| comfy-agent | Deployment | comfyui-agent-r640 | 0/1 ready |
| comfy-agent | Deployment | comfyui-amd | 1/1 ready |
| comfy-agent | Service | comfyui-agent | ClusterIP ports=8080 |
| comfy-agent | Service | comfyui-amd | ClusterIP ports=8188 |
| compocv | Deployment | compocv | 0/0 ready |
| compocv | Deployment | compocv-api-relay | 1/1 ready |
| compocv | Deployment | nuclio-compocv-api | 1/1 ready |
| compocv | Ingress | compocv | api-guangzhou24.re8ch.com, api-shanghai217.re8ch.com, api-shanghai77.re8ch.com, api.re8ch.com |
| compocv | Service | compocv | ClusterIP ports=80 |
| compocv | Service | compocv-api-r640-nodeport | NodePort ports=8080 |
| compocv | Service | compocv-api-relay | ClusterIP ports=18080 |
| compocv | Service | nuclio-compocv-api | ClusterIP ports=8080 |
| cosyvoice | Deployment | cosyvoice | 1/1 ready |
| cosyvoice | Service | cosyvoice | ClusterIP ports=50000 |
| cube-trial | DaemonSet | cube-trial-node | 1/1 ready |
| cube-trial | DaemonSet | cube-trial-node-bootstrap | 1/1 ready |
| cube-trial | DaemonSet | cube-trial-node-installer | 1/1 ready |
| cube-trial | Deployment | cube-trial-api | 1/1 ready |
| cube-trial | Deployment | cube-trial-cubemastercli | 1/1 ready |
| cube-trial | Deployment | cube-trial-lifecycle-manager | 1/1 ready |
| cube-trial | Deployment | cube-trial-master | 1/1 ready |
| cube-trial | Deployment | cube-trial-ops | 1/1 ready |
| cube-trial | Deployment | cube-trial-proxy | 1/1 ready |
| cube-trial | Deployment | cube-trial-webui | 1/1 ready |
| cube-trial | Deployment | oauth2-proxy-cube | 1/1 ready |
| cube-trial | Ingress | cube-dns-authority | cube.re8ch.com |
| cube-trial | Service | cube-trial-api | ClusterIP ports=3000 |
| cube-trial | Service | cube-trial-lifecycle-manager | ClusterIP ports=8083 |
| cube-trial | Service | cube-trial-master | ClusterIP ports=8089 |
| cube-trial | Service | cube-trial-mysql | ClusterIP ports=3306 |
| cube-trial | Service | cube-trial-ops | ClusterIP ports=3010 |
| cube-trial | Service | cube-trial-proxy | ClusterIP ports=80,443,8082 |
| cube-trial | Service | cube-trial-redis | ClusterIP ports=6379 |
| cube-trial | Service | cube-trial-webui | ClusterIP ports=12088 |
| cube-trial | Service | oauth2-proxy-cube | ClusterIP ports=4180 |
| cube-trial | StatefulSet | cube-trial-mysql | 1/1 ready |
| cube-trial | StatefulSet | cube-trial-redis | 1/1 ready |
| dataspace | Deployment | dataspace-gateway | 1/1 ready |
| dataspace | Ingress | storage-collaborator | storage.re8ch.com |
| dataspace | Service | dataspace-gateway | ClusterIP ports=8080 |
| default | Deployment | mobile-egress-mobile-egress-control | 0/0 ready |
| default | Service | kubernetes | ClusterIP ports=443 |
| default | Service | mobile-egress-mobile-egress-control | ClusterIP ports=8080 |
| dex | Deployment | dex | 2/2 ready |
| dex | Ingress | dex-dns-authority | dex.re8ch.com |
| dex | Service | dex | NodePort ports=80 |
| edge-devices | DaemonSet | r640-usb-dra | 1/1 ready |
| edge-devices | Deployment | device-fabric-controller | 1/1 ready |
| edge-devices | Service | grandstream-gui-canary | ClusterIP ports=80 |
| edge-devices | Service | r640-voice-control-canary | ClusterIP ports=18092 |
| egress-fabric | DaemonSet | mihomos-cn-exit | 7/7 ready |
| egress-fabric | DaemonSet | mihomos-global-exit | 2/2 ready |
| egress-fabric | DaemonSet | mihomos-mobile-publication | 2/2 ready |
| egress-fabric | DaemonSet | mihomos-node-cn | 13/13 ready |
| egress-fabric | DaemonSet | mihomos-node-global | 2/3 ready |
| egress-fabric | DaemonSet | mihomos-status-agent | 15/16 ready |
| egress-fabric | Deployment | mihomo-egress-kl | 0/0 ready |
| egress-fabric | Deployment | mihomo-egress-la | 0/0 ready |
| egress-fabric | Deployment | mobile-egress-mobile-egress-control | 1/1 ready |
| egress-fabric | Deployment | network-decomplexer-policy-api | 0/1 ready |
| egress-fabric | Deployment | path-controller | 1/1 ready |
| egress-fabric | Ingress | network-decomplexer-policy-api-dns | agent.re8ch.com |
| egress-fabric | Service | mihomos-cn-exit | ClusterIP ports=1080 |
| egress-fabric | Service | mihomos-global-exit | ClusterIP ports=1080 |
| egress-fabric | Service | mobile-egress-mobile-egress-control | ClusterIP ports=8080 |
| egress-fabric | Service | network-decomplexer-policy-api | ClusterIP ports=80 |
| egress-fabric | Service | path-controller | ClusterIP ports=8080 |
| fleet | Deployment | fleet | 1/1 ready |
| fleet | Deployment | fleet-egress-proxy | 1/1 ready |
| fleet | Deployment | fleet-redis | 1/1 ready |
| fleet | Ingress | fleet | fleet.re8ch.com |
| fleet | Service | fleet | ClusterIP ports=8080 |
| fleet | Service | fleet-egress-proxy | ClusterIP ports=3128 |
| fleet | Service | fleet-mysql | ClusterIP ports=3306 |
| fleet | Service | fleet-redis | ClusterIP ports=6379 |
| fleet | StatefulSet | fleet-mysql | 1/1 ready |
| flux-system | Deployment | helm-controller | 1/1 ready |
| flux-system | Deployment | image-automation-controller | 1/1 ready |
| flux-system | Deployment | image-reflector-controller | 1/1 ready |
| flux-system | Deployment | kustomize-controller | 1/1 ready |
| flux-system | Deployment | notification-controller | 1/1 ready |
| flux-system | Deployment | source-controller | 1/1 ready |
| flux-system | Service | notification-controller | ClusterIP ports=80 |
| flux-system | Service | source-controller | ClusterIP ports=80 |
| flux-system | Service | webhook-receiver | ClusterIP ports=80 |
| gitlab-agent | Deployment | gitlab-agent | 1/1 ready |
| harbor | Deployment | harbor-core | 1/1 ready |
| harbor | Deployment | harbor-jobservice | 1/1 ready |
| harbor | Deployment | harbor-log | 1/1 ready |
| harbor | Deployment | harbor-portal | 1/1 ready |
| harbor | Deployment | harbor-postgresql | 1/1 ready |
| harbor | Deployment | harbor-proxy | 1/1 ready |
| harbor | Deployment | harbor-redis | 1/1 ready |
| harbor | Deployment | harbor-registry | 1/1 ready |
| harbor | Deployment | harbor-registryctl | 1/1 ready |
| harbor | Deployment | trivy-adapter | 1/1 ready |
| harbor | Service | core | ClusterIP ports=8080 |
| harbor | Service | harbor-registry | ClusterIP ports=80,443 |
| harbor | Service | jobservice | ClusterIP ports=8080 |
| harbor | Service | log | ClusterIP ports=10514 |
| harbor | Service | portal | ClusterIP ports=8080 |
| harbor | Service | postgresql | ClusterIP ports=5432 |
| harbor | Service | re8ch-cilium-gateway-vip | ClusterIP ports=443 |
| harbor | Service | redis | ClusterIP ports=6379 |
| harbor | Service | registry | ClusterIP ports=5000 |
| harbor | Service | registryctl | ClusterIP ports=8080 |
| harbor | Service | trivy-adapter | ClusterIP ports=8080 |
| headlamp | Deployment | headlamp | 1/1 ready |
| headlamp | Ingress | headlamp-dns-authority | headlamp.re8ch.com |
| headlamp | Ingress | headlamp-gateway-http | headlamp.re8ch.com |
| headlamp | Ingress | headlamp-gateway-https | headlamp.re8ch.com |
| headlamp | Service | headlamp | LoadBalancer ports=80 |
| headlamp | Service | headlamp-gateway-origin | ClusterIP ports=80 |
| image | Deployment | nuclio-registry-image-control-api | 0/0 ready |
| image | Deployment | nuclio-registry-image-metering-worker | 0/0 ready |
| image | Ingress | registry-image-control-api | api-guangzhou24.re8ch.com, api-shanghai217.re8ch.com, api-shanghai77.re8ch.com, api.re8ch.com, image.re8ch.com |
| image | Service | nuclio-registry-image-control-api | ClusterIP ports=8080,8090 |
| image | Service | nuclio-registry-image-metering-worker | ClusterIP ports=8080,8090 |
| kagent | Deployment | kagent-controller | 1/1 ready |
| kagent | Deployment | kagent-kmcp-controller-manager | 1/1 ready |
| kagent | Deployment | kagent-postgresql | 1/1 ready |
| kagent | Deployment | kagent-tools | 1/1 ready |
| kagent | Deployment | kagent-ui | 1/1 ready |
| kagent | Deployment | my-first-k8s-agent | 1/1 ready |
| kagent | Deployment | readonly-k8s-diagnostic-agent | 1/1 ready |
| kagent | Deployment | wechat-content-operations-agent | 1/1 ready |
| kagent | Ingress | kagent-ui-shanghai217 | api-shanghai217.re8ch.com |
| kagent | Ingress | kagent-ui-static-shanghai217 | api-shanghai217.re8ch.com |
| kagent | Service | kagent-controller | ClusterIP ports=8083 |
| kagent | Service | kagent-kmcp-controller-manager-metrics-service | ClusterIP ports=8443 |
| kagent | Service | kagent-postgresql | ClusterIP ports=5432 |
| kagent | Service | kagent-tools | ClusterIP ports=8084 |
| kagent | Service | kagent-tools-metrics | ClusterIP ports=8085 |
| kagent | Service | kagent-ui | NodePort ports=8080 |
| kagent | Service | my-first-k8s-agent | ClusterIP ports=8080 |
| kagent | Service | readonly-k8s-diagnostic-agent | ClusterIP ports=8080 |
| kagent | Service | wechat-content-operations-agent | ClusterIP ports=8080 |
| kube-system | DaemonSet | cilium | 15/16 ready |
| kube-system | DaemonSet | podcidr-fabric-reconciler | 15/16 ready |
| kube-system | DaemonSet | re8ch-advanced-fabric-agent | 15/16 ready |
| kube-system | DaemonSet | re8ch-node-external-ip-agent | 12/13 ready |
| kube-system | DaemonSet | regional-origin-dnat | 3/3 ready |
| kube-system | DaemonSet | traefik | 1/1 ready |
| kube-system | DaemonSet | traefik-public-edge | 1/1 ready |
| kube-system | DaemonSet | traefik-r640 | 1/1 ready |
| kube-system | Deployment | cilium-operator | 3/3 ready |
| kube-system | Deployment | coredns | 2/2 ready |
| kube-system | Deployment | coredns-r640 | 0/0 ready |
| kube-system | Deployment | hubble-relay | 1/1 ready |
| kube-system | Deployment | hubble-ui | 1/1 ready |
| kube-system | Deployment | kube-state-metrics | 1/1 ready |
| kube-system | Deployment | local-path-provisioner | 1/1 ready |
| kube-system | Deployment | metrics-server | 0/0 ready |
| kube-system | Deployment | metrics-server-ha | 2/2 ready |
| kube-system | Deployment | re8ch-advanced-fabric-controller | 1/1 ready |
| kube-system | Deployment | routeros-node-controller | 1/1 ready |
| kube-system | Service | cilium-gateway-re8ch-gateway-canary | LoadBalancer ports=80,443 |
| kube-system | Service | cilium-gateway-re8ch-region-guangzhou | LoadBalancer ports=80,443 |
| kube-system | Service | cilium-gateway-re8ch-region-hangzhou-nuclio | LoadBalancer ports=80,443 |
| kube-system | Service | cilium-gateway-re8ch-region-loudi | LoadBalancer ports=80,443 |
| kube-system | Service | cilium-gateway-re8ch-region-shanghai | LoadBalancer ports=80,443 |
| kube-system | Service | cilium-gateway-re8ch-region-shanghai-nuclio | LoadBalancer ports=80,443 |
| kube-system | Service | hubble-peer | ClusterIP ports=443 |
| kube-system | Service | hubble-relay | ClusterIP ports=80 |
| kube-system | Service | hubble-ui | ClusterIP ports=80 |
| kube-system | Service | kube-dns | ClusterIP ports=53,9153 |
| kube-system | Service | kube-dns-r640 | ClusterIP ports=53,9153 |
| kube-system | Service | kube-state-metrics | ClusterIP ports=8080,8081 |
| kube-system | Service | metrics-server | ClusterIP ports=443 |
| kube-system | Service | re8ch-cilium-lb-canary | LoadBalancer ports=4240 |
| kube-system | Service | traefik | ClusterIP ports=80,443 |
| ledger | Deployment | nuclio-ledger-api-gateway | 1/1 ready |
| ledger | Deployment | nuclio-ledger-api-gateway-shanghai217 | 0/0 ready |
| ledger | Deployment | nuclio-ledger-api-gateway-shanghai77 | 0/0 ready |
| ledger | Deployment | nuclio-ledger-gnucash-report-runner | 1/1 ready |
| ledger | Deployment | nuclio-ledger-gnucash-report-runner-shanghai217 | 0/0 ready |
| ledger | Deployment | nuclio-ledger-gnucash-report-runner-shanghai77 | 0/0 ready |
| ledger | Deployment | nuclio-ledger-wechat-auth | 1/1 ready |
| ledger | Deployment | nuclio-ledger-wechat-auth-shanghai217 | 0/0 ready |
| ledger | Deployment | nuclio-ledger-wechat-auth-shanghai77 | 0/0 ready |
| ledger | Service | ledger-api-gateway-local | ClusterIP ports=8080 |
| ledger | Service | ledger-gnucash-report-runner-local | ClusterIP ports=8080 |
| ledger | Service | ledger-wechat-auth-local | ClusterIP ports=8080 |
| ledger | Service | nuclio-ledger-api-gateway | NodePort ports=8080,8090 |
| ledger | Service | nuclio-ledger-api-gateway-shanghai217 | NodePort ports=8080,8090 |
| ledger | Service | nuclio-ledger-api-gateway-shanghai77 | NodePort ports=8080,8090 |
| ledger | Service | nuclio-ledger-gnucash-report-runner | NodePort ports=8080,8090 |
| ledger | Service | nuclio-ledger-gnucash-report-runner-shanghai217 | NodePort ports=8080,8090 |
| ledger | Service | nuclio-ledger-gnucash-report-runner-shanghai77 | NodePort ports=8080,8090 |
| ledger | Service | nuclio-ledger-wechat-auth | NodePort ports=8080,8090 |
| ledger | Service | nuclio-ledger-wechat-auth-shanghai217 | NodePort ports=8080,8090 |
| ledger | Service | nuclio-ledger-wechat-auth-shanghai77 | NodePort ports=8080,8090 |
| llm-gateway | Deployment | litellm | 1/1 ready |
| llm-gateway | Deployment | litellm-postgresql | 1/1 ready |
| llm-gateway | Ingress | litellm-admin | litellm.re8ch.com |
| llm-gateway | Ingress | litellm-dns-authority | litellm.re8ch.com |
| llm-gateway | Service | litellm | NodePort ports=4000 |
| llm-gateway | Service | litellm-postgresql | ClusterIP ports=5432 |
| mcp-registry | Deployment | mcp-registry | 0/1 ready |
| mcp-registry | Deployment | mcp-registry-postgresql | 1/1 ready |
| mcp-registry | Service | mcp-registry | ClusterIP ports=8080 |
| mcp-registry | Service | postgresql | ClusterIP ports=5432 |
| mitos-trial | DaemonSet | mitos-forkd | 0/0 ready |
| mitos-trial | DaemonSet | mitos-kernel-stage | 0/0 ready |
| mitos-trial | DaemonSet | mitos-kvm-device-plugin | 0/0 ready |
| mitos-trial | Deployment | mitos-controller | 0/0 ready |
| neo4j | Ingress | neo4j-browser | neo4j.re8ch.com |
| neo4j | Service | ledger-casegraph | ClusterIP ports=7474,7687 |
| neo4j | Service | ledger-economicsgraph | ClusterIP ports=7474,7687 |
| neo4j | Service | neo4j | NodePort ports=7474,7687 |
| neo4j | StatefulSet | ledger-casegraph | 1/1 ready |
| neo4j | StatefulSet | ledger-economicsgraph | 1/1 ready |
| neo4j | StatefulSet | neo4j | 1/1 ready |
| netbird-access | Deployment | netbird-dashboard | 0/0 ready |
| netbird-access | Deployment | netbird-egress-probe | 0/0 ready |
| netbird-access | Deployment | netbird-relay-gz-1 | 0/0 ready |
| netbird-access | Deployment | netbird-relay-hz-1 | 0/0 ready |
| netbird-access | Deployment | netbird-router-cn | 0/0 ready |
| netbird-access | Deployment | netbird-router-la | 0/0 ready |
| netbird-access | Deployment | netbird-server | 0/0 ready |
| netbird-access | Ingress | netbird-access-dns | access.re8ch.com |
| netbird-access | Ingress | netbird-relay-gz-1-dns | relay-gz-1.re8ch.com |
| netbird-access | Ingress | netbird-relay-hz-1-dns | relay-hz-1.re8ch.com |
| netbird-access | Service | netbird-dashboard | ClusterIP ports=80 |
| netbird-access | Service | netbird-relay-gz-1-dns | ClusterIP ports=3478,31080,31081 |
| netbird-access | Service | netbird-relay-hz-1-dns | ClusterIP ports=3478,31080,31081 |
| netbird-access | Service | netbird-routing-peer-metrics | ClusterIP ports=9100 |
| netbird-access | Service | netbird-server | ClusterIP ports=80,9000,9090 |
| netbird-access | Service | netbird-stun | NodePort ports=3478 |
| nuclio | DaemonSet | ledger-accounting-agent-edge-proxy | 1/1 ready |
| nuclio | Deployment | ledger-accounting-agent | 1/1 ready |
| nuclio | Deployment | ledger-accounting-agent-reverse-tunnel | 1/1 ready |
| nuclio | Deployment | ledger-decision-analysis | 1/1 ready |
| nuclio | Deployment | ledger-decisions-static | 2/2 ready |
| nuclio | Deployment | nuclio-anycam-model-gateway | 0/0 ready |
| nuclio | Deployment | nuclio-bench-hello | 0/0 ready |
| nuclio | Deployment | nuclio-bench-pg-read | 0/0 ready |
| nuclio | Deployment | nuclio-bench-pg-write | 0/0 ready |
| nuclio | Deployment | nuclio-controller | 1/1 ready |
| nuclio | Deployment | nuclio-dashboard | 0/1 ready |
| nuclio | Ingress | ledger-accounting-agent | api-guangzhou24.re8ch.com, api-shanghai217.re8ch.com, api-shanghai77.re8ch.com, api.re8ch.com, neo4j.re8ch.com |
| nuclio | Ingress | ledger-decision-analysis | decision.re8ch.com |
| nuclio | Ingress | ledger-decision-analysis-api | api-guangzhou24.re8ch.com, api-shanghai217.re8ch.com, api-shanghai77.re8ch.com, api.re8ch.com |
| nuclio | Ingress | ledger-decision-analysis-r640 | api-guangzhou24.re8ch.com, api-shanghai217.re8ch.com, api-shanghai77.re8ch.com, api.re8ch.com, neo4j.re8ch.com |
| nuclio | Ingress | ledger-decisions-static | ledger-decisions.re8ch.com |
| nuclio | Ingress | ledger-decisions-static-http | ledger-decisions.re8ch.com |
| nuclio | Ingress | nuclio-agw-bench-hello-gw | bench.nuclio.internal |
| nuclio | Ingress | nuclio-agw-bench-pg-read-gw | bench.nuclio.internal |
| nuclio | Ingress | nuclio-agw-bench-pg-write-gw | bench.nuclio.internal |
| nuclio | Service | ledger-accounting-agent | ClusterIP ports=80 |
| nuclio | Service | ledger-accounting-agent-edge | ClusterIP ports=80 |
| nuclio | Service | ledger-decision-analysis | ClusterIP ports=80 |
| nuclio | Service | ledger-decision-analysis-edge | ClusterIP ports=80 |
| nuclio | Service | ledger-decisions-static | ClusterIP ports=80 |
| nuclio | Service | nuclio-anycam-model-gateway | NodePort ports=8080 |
| nuclio | Service | nuclio-bench-hello | ClusterIP ports=8080 |
| nuclio | Service | nuclio-bench-pg-read | ClusterIP ports=8080 |
| nuclio | Service | nuclio-bench-pg-write | ClusterIP ports=8080 |
| nuclio | Service | nuclio-dashboard | NodePort ports=8070 |
| observability-ai | Deployment | robusta-forwarder | 1/1 ready |
| observability-ai | Deployment | robusta-runner | 1/1 ready |
| observability-ai | Deployment | trial-gateway-collector | 1/1 ready |
| observability-ai | Service | robusta-forwarder | ClusterIP ports=80 |
| observability-ai | Service | robusta-runner | ClusterIP ports=80 |
| observability-ai | Service | trial-gateway-collector | ClusterIP ports=4317,4318 |
| observability-ai | Service | trial-gateway-collector-headless | ClusterIP ports=4317,4318 |
| observability-ai | Service | trial-gateway-collector-monitoring | ClusterIP ports=8888 |
| observable | Deployment | observable-postgrest | 2/2 ready |
| observable | Service | observable-postgrest | ClusterIP ports=3000 |
| observable | Service | pigsty-postgrest-role-gateway | ClusterIP ports=3080 |
| opencost | Deployment | opencost | 2/2 ready |
| opencost | Service | opencost | ClusterIP ports=8081,9003,9090 |
| opensandbox-system | Deployment | opensandbox-controller-manager | 1/1 ready |
| opensandbox-system | Deployment | opensandbox-server | 1/1 ready |
| opensandbox-system | Deployment | teable-infra-compat | 1/1 ready |
| opensandbox-system | Deployment | teable-infra-git | 1/1 ready |
| opensandbox-system | Deployment | teable-infra-minio | 1/1 ready |
| opensandbox-system | Ingress | opensandbox-server | opensandbox.re8ch.com |
| opensandbox-system | Service | opensandbox-server | NodePort ports=80 |
| opensandbox-system | Service | teable-infra | ClusterIP ports=8080 |
| opensandbox-system | Service | teable-infra-git | ClusterIP ports=8080 |
| opensandbox-system | Service | teable-infra-minio | ClusterIP ports=9000 |
| opentelemetry-operator | Deployment | otel-operator-opentelemetry-operator | 0/0 ready |
| opentelemetry-operator | Service | otel-operator-opentelemetry-operator | ClusterIP ports=8443 |
| opentelemetry-operator | Service | otel-operator-opentelemetry-operator-webhook | ClusterIP ports=443 |
| ops | Deployment | cloudbeaver | 1/1 ready |
| ops | Deployment | mikrotik-rule-publisher | 0/0 ready |
| ops | Deployment | ops-mcp-server | 0/1 ready |
| ops | Deployment | vpn-identity-controller | 0/0 ready |
| ops | Ingress | cloudbeaver-dns-authority | dbgui.re8ch.com |
| ops | Service | cloudbeaver | ClusterIP ports=8978 |
| ops | Service | mikrotik-rule-publisher | NodePort ports=8080 |
| ops | Service | ops-mcp-server | ClusterIP ports=8088 |
| ops | Service | vpn-identity-controller | ClusterIP ports=8080 |
| pigsty-external | Service | pg-meta-primary | ClusterIP ports=5432,6432 |
| pigsty-external | Service | pg-meta-replicas | ClusterIP ports=5432,6432 |
| pigsty-external | Service | pg-re8ch-primary | ClusterIP ports=5432,6432 |
| pigsty-external | Service | pg-re8ch-replicas | ClusterIP ports=5432,6432 |
| pigsty-external | Service | pgread-gz | ClusterIP ports=3000,3080,5432,6432 |
| pigsty-external | Service | pgread-sh | ClusterIP ports=3000,3080,5432,6432 |
| pigsty-external | Service | pgwrite | ClusterIP ports=3000,3080,5432,6432 |
| qianwen-ops | DaemonSet | alloy-nuclio-logs | 1/1 ready |
| qianwen-ops | DaemonSet | panel-harbor-node-vmagent | 1/1 ready |
| qianwen-ops | DaemonSet | panel-node-exporter | 9/9 ready |
| qianwen-ops | DaemonSet | panel-qwen-functions-vmagent | 2/2 ready |
| qianwen-ops | DaemonSet | panel-vlagent | 2/2 ready |
| qianwen-ops | Deployment | aliyun-esa-api-origin-controller | 0/0 ready |
| qianwen-ops | Deployment | external-dns-cloudflare | 1/1 ready |
| qianwen-ops | Deployment | hyperglass | 0/2 ready |
| qianwen-ops | Deployment | hyperglass-redis | 1/1 ready |
| qianwen-ops | Deployment | loki-lite | 0/1 ready |
| qianwen-ops | Deployment | oauth2-proxy-hubble | 2/2 ready |
| qianwen-ops | Deployment | oauth2-proxy-hyperglass | 2/2 ready |
| qianwen-ops | Deployment | oauth2-proxy-opencost | 2/2 ready |
| qianwen-ops | Deployment | panel-blackbox-exporter | 1/1 ready |
| qianwen-ops | Deployment | panel-grafana | 1/1 ready |
| qianwen-ops | Deployment | panel-grafana-renderer | 1/1 ready |
| qianwen-ops | Deployment | panel-pigsty-vmalert | 1/1 ready |
| qianwen-ops | Deployment | panel-re8ch-vmalert | 1/1 ready |
| qianwen-ops | Deployment | panel-tenant-logs-proxy | 1/1 ready |
| qianwen-ops | Deployment | panel-tenant-metrics-proxy | 1/1 ready |
| qianwen-ops | Deployment | panel-victoria-logs | 1/1 ready |
| qianwen-ops | Deployment | panel-victoria-metrics | 1/1 ready |
| qianwen-ops | Deployment | re8ch-routing-advisor | 1/1 ready |
| qianwen-ops | Ingress | grafana-dns-authority | grafana.re8ch.com |
| qianwen-ops | Ingress | panel-grafana | grafana.re8ch.com |
| qianwen-ops | Ingress | panel-grafana-gateway-http | grafana.re8ch.com |
| qianwen-ops | Ingress | static-cilium-edge-services-dns | registry.re8ch.com |
| qianwen-ops | Ingress | static-hangzhou-services-dns | auth.re8ch.com |
| qianwen-ops | Ingress | static-icp-services-dns | feishu-agent.re8ch.com |
| qianwen-ops | Ingress | static-nuclio-shanghai-dns | nuc-1.re8ch.com |
| qianwen-ops | Ingress | static-panel-services-dns | portainer.re8ch.com |
| qianwen-ops | Ingress | static-pg1-identity-dns | pg-1.re8ch.com |
| qianwen-ops | Ingress | static-pg2-services-dns | pg-2.re8ch.com |
| qianwen-ops | Ingress | static-pg3-identity-dns | pg-3.re8ch.com |
| qianwen-ops | Ingress | static-r640-services-dns | files.re8ch.com |
| qianwen-ops | Service | external-dns-placeholder | ClusterIP ports=80 |
| qianwen-ops | Service | hyperglass | ClusterIP ports=8001 |
| qianwen-ops | Service | hyperglass-redis | ClusterIP ports=6379 |
| qianwen-ops | Service | loki-lite | NodePort ports=3100 |
| qianwen-ops | Service | oauth2-proxy-hubble | NodePort ports=4180 |
| qianwen-ops | Service | oauth2-proxy-hyperglass | NodePort ports=4180 |
| qianwen-ops | Service | oauth2-proxy-opencost | NodePort ports=4180 |
| qianwen-ops | Service | panel-blackbox-exporter | ClusterIP ports=9115 |
| qianwen-ops | Service | panel-grafana | LoadBalancer ports=3000 |
| qianwen-ops | Service | panel-grafana-gateway-origin | ClusterIP ports=80 |
| qianwen-ops | Service | panel-grafana-renderer | ClusterIP ports=8081 |
| qianwen-ops | Service | panel-grafana-vip-origin | ClusterIP ports=3000 |
| qianwen-ops | Service | panel-node-exporter | ClusterIP ports=9100 |
| qianwen-ops | Service | panel-pigsty-vmalert | ClusterIP ports=8880 |
| qianwen-ops | Service | panel-re8ch-vmalert | ClusterIP ports=8880 |
| qianwen-ops | Service | panel-tenant-logs-proxy | ClusterIP ports=39092 |
| qianwen-ops | Service | panel-tenant-metrics-proxy | ClusterIP ports=39091 |
| qianwen-ops | Service | panel-victoria-logs | ClusterIP ports=9428 |
| qianwen-ops | Service | panel-victoria-metrics | NodePort ports=8428 |
| qianwen-ops | Service | panel-vlagent | ClusterIP ports=9429 |
| qianwen-ops | Service | pigsty-icp-infra-exporters | ClusterIP ports=9059,9115 |
| qianwen-ops | Service | pigsty-pg-meta-exporters | ClusterIP ports=8008,9101,9630,9631,9854 |
| qianwen-ops | Service | re8ch-routing-advisor | ClusterIP ports=9790 |
| qwen | Deployment | aliyun-esa-api-origin-controller | 1/1 ready |
| qwen | Deployment | nuclio-qianwen-agent-api | 1/1 ready |
| qwen | Deployment | nuclio-qianwen-mcp-gateway | 1/1 ready |
| qwen | Deployment | nuclio-qianwen-prod-analytics | 1/1 ready |
| qwen | Deployment | nuclio-qianwen-prod-api-core | 1/1 ready |
| qwen | Deployment | nuclio-qianwen-prod-assets-api | 1/1 ready |
| qwen | Deployment | nuclio-qianwen-prod-feishu-agent | 0/0 ready |
| qwen | Deployment | nuclio-qianwen-prod-image-api | 1/1 ready |
| qwen | Deployment | nuclio-qianwen-prod-image-worker | 1/1 ready |
| qwen | Deployment | nuclio-qianwen-prod-integrations-api | 1/1 ready |
| qwen | Deployment | nuclio-qianwen-prod-oss-sts | 1/1 ready |
| qwen | Deployment | nuclio-qianwen-prod-reverse-image-prompt | 0/0 ready |
| qwen | Deployment | nuclio-qianwen-prod-script-api | 1/1 ready |
| qwen | Deployment | nuclio-qianwen-prod-storyboard-api | 0/0 ready |
| qwen | Deployment | nuclio-qianwen-prod-tasks | 1/1 ready |
| qwen | Deployment | nuclio-qianwen-prod-text-api | 0/0 ready |
| qwen | Deployment | nuclio-qianwen-prod-video-api | 1/1 ready |
| qwen | Deployment | nuclio-qianwen-prod-video-meta | 0/0 ready |
| qwen | Deployment | nuclio-qianwen-prod-workflow-api | 1/1 ready |
| qwen | Deployment | qianwen-region-metadata | 1/1 ready |
| qwen | Deployment | qwen-edge-redirects | 1/1 ready |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-admin | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-analytics-events | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-assets | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-billing | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-client-events | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-default-api-core | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-feishu-callback | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-feishu-callback-code | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-get-captcha | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-homepage | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-login-phone | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-oss-sts | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-other-auth | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-playground-image | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-playground-text | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-playground-video | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-public-assets | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-register-phone | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-reverse-image-prompt | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-script | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-send-phone-code | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-storyboard | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-tasks | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-tasks-list | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-video | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-video-meta | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-wechat-login | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-wechat-mini | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-wechat-register | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-live-native-wechat-web | api.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-native-analytics-client-events | api-native.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-native-api-core-login | api-native.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-native-feishu-callback | api-native.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-native-feishu-callback-code | api-native.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-native-homepage | api-native.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-native-oss-sts | api-native.jz.ynzs.com |
| qwen | Ingress | nuclio-agw-qianwen-prod-native-tasks-list | api-native.jz.ynzs.com |
| qwen | Ingress | qianwen-prod-api-re8ch-alias-hosts | api.re8ch.com |
| qwen | Ingress | qianwen-prod-feishu-agent | api-shanghai217.re8ch.com, api.re8ch.com, feishu-agent.re8ch.com |
| qwen | Ingress | qianwen-prod-native-alias-hosts | api-cand1.re8ch.com, api-cand3.re8ch.com, api-guangzhou24.re8ch.com, api-hangzhou95.re8ch.com, api-shanghai217.re8ch.com, api-shanghai77.re8ch.com, api-shanghai79.re8ch.com, api.jz.ynzs.com |
| qwen | Ingress | qianwen-prod-native-callback-code-prefix | api.jz.ynzs.com |
| qwen | Service | animationcomposer-upstream | ClusterIP ports=4317 |
| qwen | Service | cardcatdemo-upstream | ClusterIP ports=3000 |
| qwen | Service | nuclio-qianwen-agent-api | NodePort ports=8080,8090 |
| qwen | Service | nuclio-qianwen-mcp-gateway | NodePort ports=8080,8090 |
| qwen | Service | nuclio-qianwen-prod-analytics | ClusterIP ports=8080 |
| qwen | Service | nuclio-qianwen-prod-api-core | ClusterIP ports=8080 |
| qwen | Service | nuclio-qianwen-prod-assets-api | ClusterIP ports=8080 |
| qwen | Service | nuclio-qianwen-prod-feishu-agent | NodePort ports=8080 |
| qwen | Service | nuclio-qianwen-prod-image-api | ClusterIP ports=8080 |
| qwen | Service | nuclio-qianwen-prod-image-worker | ClusterIP ports=8080 |
| qwen | Service | nuclio-qianwen-prod-integrations-api | ClusterIP ports=8080,8090 |
| qwen | Service | nuclio-qianwen-prod-oss-sts | ClusterIP ports=8080 |
| qwen | Service | nuclio-qianwen-prod-reverse-image-prompt | ClusterIP ports=8080 |
| qwen | Service | nuclio-qianwen-prod-script-api | ClusterIP ports=8080 |
| qwen | Service | nuclio-qianwen-prod-storyboard-api | ClusterIP ports=8080 |
| qwen | Service | nuclio-qianwen-prod-tasks | ClusterIP ports=8080 |
| qwen | Service | nuclio-qianwen-prod-text-api | ClusterIP ports=8080 |
| qwen | Service | nuclio-qianwen-prod-video-api | ClusterIP ports=8080 |
| qwen | Service | nuclio-qianwen-prod-video-meta | ClusterIP ports=8080 |
| qwen | Service | nuclio-qianwen-prod-workflow-api | ClusterIP ports=8080 |
| qwen | Service | qianwen-region-metadata | ClusterIP ports=8080 |
| qwen | Service | qwen-edge-redirects | ClusterIP ports=8080 |
| qwen | Service | test-mgr-postgrest-upstream | ClusterIP ports=3080 |
| regional-routing | DaemonSet | public-edge-manager | 3/3 ready |
| regional-routing | DaemonSet | regional-edge-tls-proxy | 1/1 ready |
| regional-routing | Ingress | api-public-dns-authority | api.re8ch.com |
| regional-routing | Service | public-edge-manager | ClusterIP ports=8080 |
| rook-ceph | DaemonSet | rook-ceph.cephfs.csi.ceph.com-nodeplugin | 15/15 ready |
| rook-ceph | DaemonSet | rook-ceph.rbd.csi.ceph.com-nodeplugin | 15/15 ready |
| rook-ceph | Deployment | ceph-csi-controller-manager | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-crashcollector-a1-wsl-zt | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-crashcollector-b1-wsl-zt | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-crashcollector-r640 | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-exporter-a1-wsl-zt | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-exporter-b1-wsl-zt | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-exporter-r640 | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-mds-re8ch-files-a | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-mds-re8ch-files-b | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-mgr-a | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-mgr-b | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-mon-a | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-mon-b | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-mon-c | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-operator | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-osd-0 | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-osd-1 | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-osd-2 | 1/1 ready |
| rook-ceph | Deployment | rook-ceph-tools | 1/1 ready |
| rook-ceph | Deployment | rook-ceph.cephfs.csi.ceph.com-ctrlplugin | 2/2 ready |
| rook-ceph | Deployment | rook-ceph.rbd.csi.ceph.com-ctrlplugin | 2/2 ready |
| rook-ceph | Service | rook-ceph-exporter | ClusterIP ports=9926 |
| rook-ceph | Service | rook-ceph-mgr | ClusterIP ports=9283 |
| rook-ceph | Service | rook-ceph-mgr-dashboard | ClusterIP ports=8443 |
| storage-fabric | DaemonSet | csi-rclone-node | 1/1 ready |
| storage-fabric | Deployment | csi-rclone-controller | 2/2 ready |
| supabase-pg-re8ch | Deployment | supabase-auth | 1/1 ready |
| supabase-pg-re8ch | Deployment | supabase-imgproxy | 1/1 ready |
| supabase-pg-re8ch | Deployment | supabase-kong | 1/1 ready |
| supabase-pg-re8ch | Deployment | supabase-meta | 1/1 ready |
| supabase-pg-re8ch | Deployment | supabase-realtime | 1/1 ready |
| supabase-pg-re8ch | Deployment | supabase-rest | 0/1 ready |
| supabase-pg-re8ch | Deployment | supabase-storage | 1/1 ready |
| supabase-pg-re8ch | Deployment | supabase-studio | 1/1 ready |
| supabase-pg-re8ch | Ingress | supabase-pg-re8ch-studio | supabase.re8ch.com |
| supabase-pg-re8ch | Service | supabase-api-kong | ClusterIP ports=8000 |
| supabase-pg-re8ch | Service | supabase-auth | ClusterIP ports=9999 |
| supabase-pg-re8ch | Service | supabase-imgproxy | ClusterIP ports=5001 |
| supabase-pg-re8ch | Service | supabase-meta | ClusterIP ports=8080 |
| supabase-pg-re8ch | Service | supabase-pg-re8ch | ClusterIP ports=8000 |
| supabase-pg-re8ch | Service | supabase-realtime | ClusterIP ports=4000 |
| supabase-pg-re8ch | Service | supabase-rest | ClusterIP ports=3000 |
| supabase-pg-re8ch | Service | supabase-storage | ClusterIP ports=5000 |
| supabase-pg-re8ch | Service | supabase-studio | ClusterIP ports=3000 |
| toolhive-system | Deployment | cluster-infra-plugins-runtime | 1/1 ready |
| toolhive-system | Deployment | ops-admin-external | 2/2 ready |
| toolhive-system | Deployment | ops-admin-managed | 1/1 ready |
| toolhive-system | Deployment | ops-admin-vmcp-internal | 1/1 ready |
| toolhive-system | Deployment | ops-observe-external | 2/2 ready |
| toolhive-system | Deployment | ops-observe-managed | 1/1 ready |
| toolhive-system | Deployment | ops-observe-vmcp-internal | 1/1 ready |
| toolhive-system | Deployment | ops-operate-external | 2/2 ready |
| toolhive-system | Deployment | ops-operate-managed | 1/1 ready |
| toolhive-system | Deployment | ops-operate-vmcp-internal | 1/1 ready |
| toolhive-system | Deployment | re8ch-admin-vmcp | 2/2 ready |
| toolhive-system | Deployment | re8ch-plugin-runtime | 2/2 ready |
| toolhive-system | Deployment | re8ch-tools-vmcp | 2/2 ready |
| toolhive-system | Deployment | toolhive-cloud-ui | 2/2 ready |
| toolhive-system | Deployment | toolhive-operator | 2/2 ready |
| toolhive-system | Deployment | toolhive-registry-server | 2/2 ready |
| toolhive-system | Ingress | admin-tools-re8ch-com | admin.re8ch.com |
| toolhive-system | Ingress | operate-tools-re8ch-com | operate.tools.re8ch.com |
| toolhive-system | Ingress | toolhive-cloud-ui | tools.re8ch.com |
| toolhive-system | Ingress | tools-re8ch-com | tools.re8ch.com |
| toolhive-system | Ingress | vmcp-r640-backends | admin.re8ch.com |
| toolhive-system | Service | mcp-cluster-infra-plugins-runtime | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-cluster-infra-plugins-runtime-headless | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-cluster-infra-plugins-runtime-proxy | ClusterIP ports=8080 |
| toolhive-system | Service | mcp-ops-admin-external | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-admin-external-headless | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-admin-external-proxy | ClusterIP ports=8080 |
| toolhive-system | Service | mcp-ops-admin-managed | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-admin-managed-headless | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-admin-managed-proxy | ClusterIP ports=8080 |
| toolhive-system | Service | mcp-ops-admin-vmcp-internal | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-admin-vmcp-internal-headless | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-admin-vmcp-internal-proxy | ClusterIP ports=8080 |
| toolhive-system | Service | mcp-ops-observe-external | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-observe-external-headless | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-observe-external-proxy | ClusterIP ports=8080 |
| toolhive-system | Service | mcp-ops-observe-managed | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-observe-managed-headless | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-observe-managed-proxy | ClusterIP ports=8080 |
| toolhive-system | Service | mcp-ops-observe-vmcp-internal | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-observe-vmcp-internal-headless | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-observe-vmcp-internal-proxy | ClusterIP ports=8080 |
| toolhive-system | Service | mcp-ops-operate-external | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-operate-external-headless | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-operate-external-proxy | ClusterIP ports=8080 |
| toolhive-system | Service | mcp-ops-operate-managed | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-operate-managed-headless | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-operate-managed-proxy | ClusterIP ports=8080 |
| toolhive-system | Service | mcp-ops-operate-vmcp-internal | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-operate-vmcp-internal-headless | ClusterIP ports=8088 |
| toolhive-system | Service | mcp-ops-operate-vmcp-internal-proxy | ClusterIP ports=8080 |
| toolhive-system | Service | re8ch-plugin-runtime | ClusterIP ports=8088 |
| toolhive-system | Service | toolhive-auth-redis | ClusterIP ports=6379 |
| toolhive-system | Service | toolhive-cloud-ui | ClusterIP ports=80 |
| toolhive-system | Service | toolhive-registry-server | ClusterIP ports=8080 |
| toolhive-system | Service | vmcp-re8ch-admin-vmcp | ClusterIP ports=4483 |
| toolhive-system | Service | vmcp-re8ch-tools-vmcp | ClusterIP ports=4483 |
| toolhive-system | StatefulSet | cluster-infra-plugins-runtime | 1/1 ready |
| toolhive-system | StatefulSet | ops-admin-external | 2/2 ready |
| toolhive-system | StatefulSet | ops-admin-managed | 1/1 ready |
| toolhive-system | StatefulSet | ops-admin-vmcp-internal | 1/1 ready |
| toolhive-system | StatefulSet | ops-observe-external | 2/2 ready |
| toolhive-system | StatefulSet | ops-observe-managed | 1/1 ready |
| toolhive-system | StatefulSet | ops-observe-vmcp-internal | 1/1 ready |
| toolhive-system | StatefulSet | ops-operate-external | 2/2 ready |
| toolhive-system | StatefulSet | ops-operate-managed | 1/1 ready |
| toolhive-system | StatefulSet | ops-operate-vmcp-internal | 1/1 ready |
| toolhive-system | StatefulSet | toolhive-auth-redis | 0/0 ready |
| toolhive-system | StatefulSet | toolhive-auth-redis-ab | 1/1 ready |
| voice-observability | Ingress | homer-ui | phonycenter.re8ch.com |
| voice-observability | Service | homer-ui | ClusterIP ports=80 |
