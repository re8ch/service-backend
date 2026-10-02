# ICP ZeroTier planet cutover

The staged `kube-system/zerotier-planet` Deployment has zero replicas. It uses
the current ICP Docker image digest, encrypted environment, and the four
existing host data directories. It must stay on `vm-20-12-ubuntu`; another node
cannot use those paths or the current planet identity.

## Preconditions

1. Confirm `k3s.service` is masked and `k3s-agent`, Docker, and the host
   `zerotier-one` are active on ICP. Confirm the node is Ready without disk
   pressure and the four host paths exist.
2. Confirm the staged SOPS Secret is present in `kube-system` and the Deployment
   has zero replicas. Confirm the current Docker container remains healthy on
   TCP 3443 and UDP/TCP 9994; record the current image digest and data sizes.
3. Prepare a separately reviewed activation change that sets replicas to one.
   Do not run the two planet processes against the same `/var/lib/zerotier-one`
   data at once.

## Cutover

1. Immediately before activation, on ICP set the legacy container restart
   policy to `no`, then stop it gracefully. Do not remove the container or its
   bind-mounted data.
2. Archive the four data directories while stopped and verify the archive can
   be listed. Keep the archive outside Git.
3. Merge the activation change and allow Flux to start the pinned Deployment.
   Verify its Pod is Ready on ICP,
   that TCP 3443 returns HTTP 200, and that TCP/UDP 9994 are listening. Check a
   ZeroTier client can still reach the existing planet identity and network.
4. Compare the four host paths and identity files to the pre-cutover record.
   Monitor the node and Pod for restart, disk pressure, and network errors.

## Rollback

First return the GitOps Deployment to zero replicas and wait until its Pod has
stopped. Then start the preserved `myztplanet` Docker container with its original
bind mounts and restart policy. Restore the stopped-state archive only if the
new Pod changed the data in an incompatible way; never overwrite data while
either process is running.
