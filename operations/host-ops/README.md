# RE8CH Linux Host Operations (legacy rollback)

This short-lived certificate flow is retained only as rollback data. New Cloud
Platform Tool deployments use the permanent server-held jump-host identity in
`manifests/k3s/ai-operations/`; do not run this signer during normal operation.

Host access uses short-lived OpenSSH user certificates. The CA private key is
kept root-only on R640 and must never be copied into Kubernetes. Agents use the
`re8ch-ai` account and the fixed `re8ch-host-op` command; generic root login and
generic sudo are not part of this design.

Windows control-plane host recovery and the non-secret three-node management
SSH declaration live under `windows/` and `management-ssh-mesh/`. See
`runbooks/management-ssh-mesh.md`. Host recovery remains available when
Kubernetes is unavailable; cluster workloads may observe it but do not own
host-reboot credentials.

Treat the Windows host and its WSL/K3s guest as separate health domains. An
open Windows SSH port or a visibly powered-on host does not prove that the WSL
distribution, local etcd, kubelet or node lease is healthy. Conversely, failed
administrator authentication is not evidence that the Windows host is
unreachable. Check the declared Windows address, WSL management address,
Kubernetes node lease and local API readiness independently before selecting a
recovery action.

Installation is deliberately two-step and does not happen as a side effect of
applying Kubernetes manifests:

1. On R640, run `install-ssh-user-ca.sh ca-init` and place the printed public
   key in the approved configuration channel.
2. On each reviewed Linux node, run `install-ssh-user-ca.sh node-enroll
   /path/to/ssh_user_ca.pub`.

The signer command accepts only public keys and emits a ten-minute certificate.
The private key remains in `/var/lib/re8ch-ssh-ca`. Node reboot orchestration
must still satisfy the approval and Kubernetes preflight policy in the platform
inventory; possession of a certificate is not approval.
