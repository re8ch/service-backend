# Runtime Anatomy Headlamp plugin

Console for the Runtime Anatomy final scope and runtime timeline resources. Its
presentation follows the Rook/Ceph view: backend and
control-plane health are shown separately from claims, snapshots and recovery
evidence.

The plugin reads:

- `runtime-anatomy/*-scope` ConfigMaps labelled
  `anatomy.re8ch.com/config-role=final-scope`;
- PVCs and `VolumeSnapshot` metadata visible to the signed-in Headlamp user;
- registered `CSIDriver` objects;
- future `Runtime`, `RuntimeCommit`, `RuntimeBranch` and `RuntimeCheckout`
  resources in `anatomy.re8ch.com/v1alpha1`.

It never reads Secrets, mounts CephFS, lists S3 objects, downloads commit
artifacts, displays cookies or calls Ceph administrative APIs. Kubernetes RBAC
remains the authorization boundary. Missing permission or absent alpha CRDs is
shown as unavailable data, not as an empty healthy timeline.

## Views

1. Overview: scope lifecycle, commit store, credential/GC safety and runtime
   integration state.
2. Storage: configured CephFS/RBD/S3/cache backends, CSI driver registration,
   matching PVC counts and visible snapshots.
3. State classes: filesystem, browser profile, exports and image-cache capture
   policy.
4. Images: immutable references and provenance policy.
5. Timeline: commits, parent digests, consistency, phase and branch heads once
   the Runtime Anatomy controller/CRDs exist.

Optional APIs are discovered through Kubernetes `/apis` before their list/watch
hooks are mounted. An absent VolumeSnapshot or Runtime Anatomy CRD is rendered
as an unavailable capability without issuing repeated 404 requests. The final
scope uses an exact namespaced ConfigMap GET rather than a cluster-wide list.

v0.2.0 can create RuntimeCommit, RuntimeDiff, RuntimeBranch, RuntimeCheckout and
RuntimeGarbageCollection requests after confirmation. The UI only creates CRs;
the controller enforces the labelled canary boundary. Restore always targets a
new PVC and garbage collection is fixed to DryRun.

```sh
npm ci
npm run tsc
npm run build
npm run package
```
