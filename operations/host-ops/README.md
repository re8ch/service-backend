# RE8CH Linux Host Operations (legacy rollback)

This short-lived certificate flow is retained only as rollback data. New Cloud
Platform Tool deployments use the permanent server-held jump-host identity in
`manifests/k3s/ai-operations/`; do not run this signer during normal operation.

Host access uses short-lived OpenSSH user certificates. The CA private key is
kept root-only on restricted-node-6dc3f140f9 and must never be copied into Kubernetes. Agents use the
`re8ch-ai` account and the fixed `re8ch-host-op` command; generic root login and
generic sudo are not part of this design.

Installation is deliberately two-step and does not happen as a side effect of
applying Kubernetes manifests:

1. On restricted-node-6dc3f140f9, run `install-ssh-user-ca.sh ca-init` and place the printed public
   key in the approved configuration channel.
2. On each reviewed Linux node, run `install-ssh-user-ca.sh node-enroll
   /path/to/ssh_user_ca.pub`.

The signer command accepts only public keys and emits a ten-minute certificate.
The private key remains in `/var/lib/re8ch-ssh-ca`. Node reboot orchestration
must still satisfy the approval and Kubernetes preflight policy in the platform
inventory; possession of a certificate is not approval.
