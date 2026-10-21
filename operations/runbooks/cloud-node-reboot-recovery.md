# Cloud node reboot recovery

Use `operations/scripts/recover-cloud-node.py` when both Kubernetes and SSH can no longer reach an inventory node. The tool reads the provider, product, CLI profile, region and instance ID from `inventory/nodes.yaml`; never infer these values from an old hostname or cost record.

```bash
# Inspect the selected route and health gate without changing cloud state.
python operations/scripts/recover-cloud-node.py muaya-ecs

# Reboot only when both the Node Ready condition and SSH probe fail.
python operations/scripts/recover-cloud-node.py muaya-ecs --execute
```

The dual-unreachable gate deliberately refuses a reboot when either Kubernetes reports the node `Ready` or SSH succeeds. Supported routes are Aliyun ECS, Aliyun Simple Application Server, Tencent Lighthouse and Tencent CVM. `--execute` is always required.

## K3s recovery after a control-plane replacement

The 2026-10-21 `muaya-ecs` incident established this recovery order:

1. Use the cloud reboot route when both ZeroTier/public SSH and Kubernetes are unavailable. A TCP connection to port 22 is not sufficient; SSH must complete its protocol and command.
2. Confirm `/etc/rancher/k3s/config.yaml` uses the current A1 server, `https://192.0.2.242:6443`.
3. Confirm the node can fetch `https://192.0.2.242:6443/cacerts`.
4. Remove `/var/lib/rancher/k3s/agent/etc/k3s-agent-load-balancer.json` and restart `k3s-agent`.
5. If the local agent keeps receiving `503 Service Unavailable` while retrieving agent configuration, compare the local `/etc/rancher/node/password` hash with the `kube-system/<node>.node-password.k3s` secret hash. Never print either password.
6. When the hashes differ, delete only that node's Kubernetes password secret, stop its agent, remove only `/etc/rancher/node/password`, and start the agent so K3s can register a new password.
7. Finish only after `kubectl get node <name>` reports `Ready`.

For `muaya-ecs`, the authoritative identity is Aliyun ECS instance `i-bp1auluadeyatron6lw4` in profile `default`, region `cn-hangzhou`, public IP `192.0.2.185`. Legacy `harbor` SSH and cost aliases do not make this node the Harbor service owner.
