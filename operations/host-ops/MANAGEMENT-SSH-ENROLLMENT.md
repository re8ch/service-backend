# Repair a Cloud Platform SSH enrollment

The native tool uses the public key corresponding to the existing server-held
`ai-operations/cloud-platform-runtime` identity. A1, B1, and R640 already accept
that identity. During the October recovery, Mubao and domain-ex had no
`re8ch-ai` account; zan had that account but no authorized-keys directory.

Run `enroll-management-ssh-key.py` as root on the specifically reviewed node,
with **only the existing public key** on standard input. Obtain it with
`ssh-keygen -y` inside the Cloud Platform runtime. Never copy the private key
out of its Secret mount. Use an existing authorized host session or the cluster
management plane to execute the enrollment on that node.

The helper creates the standard unprivileged account only when absent, preserves
existing keys, adds the public key with OpenSSH `restrict` options, and repairs
SSH directory/file ownership and modes. It adds no sudo rule and changes no SSH
daemon policy. The result contains only the public-key fingerprint and whether
an entry was added. An incompatible existing account or symlinked SSH path stops
the operation for review. Retest through `node.ssh.exec` after enrollment.

Mubao's SSH config explicitly matches both its inventory name and its declared
address, since native execution honors inventory `spec.address`.
