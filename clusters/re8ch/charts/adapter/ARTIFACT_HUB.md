# Artifact Hub and OCI delivery

The public package identity is `adapter`; its OCI repository is:

```text
oci://registry.re8ch.com/helm/adapter
```

Package and publish with `operations/adapter/release-oci.sh`. The script emits
the immutable manifest digest that must be reviewed in a separate GitOps lock
change. The cluster never consumes a mutable chart tag.

Register the OCI URL once in the Artifact Hub control panel. Artifact Hub then
returns the repository ID used to prove ownership. Do not invent or commit a
placeholder ID. Supply the real ID and owner data to the release script through
an untracked metadata file if the `artifacthub.io` ownership layer is published.

The Chart annotations contain package metadata, changes, links and immutable
default images. Repository credentials and Artifact Hub account data are never
stored in this repository.
