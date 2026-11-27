#!/usr/bin/env python3
"""Deterministic OCI evidence checks. No network, credentials or orchestration."""
import hashlib
import json
import re
import sys
from pathlib import Path


def evidence(args):
    import hashlib, json, re, sys
    from pathlib import Path
    name, digest, source = args
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or not re.fullmatch(r"[0-9a-f]{40}", source):
        sys.exit("Invalid immutable digest or source revision")
    raw = Path(name + ".index.json").read_bytes()
    if digest[7:] not in {hashlib.sha256(raw).hexdigest(), hashlib.sha256(raw.removesuffix(b"\n")).hexdigest()}:
        sys.exit("Published index digest does not match returned bytes")
    index = json.loads(raw)
    images = [m for m in index.get("manifests", []) if m.get("platform", {}).get("os") == "linux" and m.get("platform", {}).get("architecture") == "amd64"]
    if len(images) != 1:
        sys.exit("Published index must contain exactly one linux/amd64 image")
    subject = images[0]["digest"]
    if not any(m.get("annotations", {}).get("vnd.docker.reference.type") == "attestation-manifest" and m.get("annotations", {}).get("vnd.docker.reference.digest") == subject for m in index["manifests"]):
        sys.exit("Published index lost its image-bound attestation manifest")
    provenance_doc = json.loads(Path(name + ".provenance.json").read_text())
    sbom_doc = json.loads(Path(name + ".sbom.json").read_text())
    # Buildx uses an unkeyed stub for one runtime platform, a map for multiple.
    provenance = provenance_doc.get("linux/amd64", provenance_doc).get("SLSA", {})
    sbom = sbom_doc.get("linux/amd64", sbom_doc).get("SPDX", {})
    # BuildKit's supported SLSA v0.2 and v1 use different native metadata shapes.
    if "buildDefinition" in provenance:
        build_type = provenance.get("buildDefinition", {}).get("buildType")
        metadata = provenance.get("runDetails", {}).get("metadata", {}).get("buildkit_metadata", {})
    else:
        build_type = provenance.get("buildType")
        metadata = provenance.get("metadata", {}).get("https://mobyproject.org/buildkit@v1#metadata", {})
    build_types = {"https://mobyproject.org/buildkit@v1", "https://github.com/moby/buildkit/blob/master/docs/attestations/slsa-definitions.md"}
    if build_type not in build_types or metadata.get("vcs", {}).get("revision") != source:
        sys.exit("Published provenance is absent or bound to a different source revision")
    if not metadata.get("source", {}).get("infos"):
        sys.exit("Published provenance does not retain max-mode source metadata")
    if not str(sbom.get("spdxVersion", "")).startswith("SPDX-") or not sbom.get("packages"):
        sys.exit("Published image lost its SPDX SBOM")
    print("Published OCI index retained source-bound max provenance and SPDX SBOM")


def layout(args):
    folder, digest = args
    root = Path(folder)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        sys.exit("Invalid expected layout digest")
    index = json.loads((root / "index.json").read_text())
    if digest not in {d.get("digest") for d in index.get("manifests", [])}:
        sys.exit("Cold layout does not contain expected root digest")
    count = total = 0
    for path in (root / "blobs" / "sha256").iterdir():
        if not path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", path.name):
            sys.exit("Invalid cold-layout blob path")
        check = hashlib.sha256()
        with path.open("rb") as stream:
            while data := stream.read(1024 * 1024):
                check.update(data)
                total += len(data)
        if check.hexdigest() != path.name:
            sys.exit("Cold-layout blob digest mismatch")
        count += 1
    pending = list(index["manifests"])
    seen = set()
    while pending:
        descriptor = pending.pop()
        key = descriptor.get("digest", "")
        if key in seen:
            continue
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", key):
            sys.exit("Unsupported layout descriptor digest")
        seen.add(key)
        path = root / "blobs" / "sha256" / key[7:]
        if not path.is_file() or path.stat().st_size != descriptor.get("size"):
            sys.exit("Cold-layout referenced blob missing or wrong size")
        media = descriptor.get("mediaType", "")
        if media in {"application/vnd.oci.image.index.v1+json", "application/vnd.oci.image.manifest.v1+json",
                     "application/vnd.docker.distribution.manifest.list.v2+json", "application/vnd.docker.distribution.manifest.v2+json"}:
            value = json.loads(path.read_text())
            pending.extend(value.get("manifests", []))
            pending.extend(value.get("layers", []))
            for field in ("config", "subject"):
                if value.get(field):
                    pending.append(value[field])
    print(json.dumps({"digest": digest, "blobs": count, "bytes": total, "verifiedDescriptors": len(seen), "allBlobHashesVerified": True}))


def claim(args):
    filename, digest, *repositories = args
    values = json.loads(Path(filename).read_text())
    refs = {v.get("critical", {}).get("identity", {}).get("docker-reference") for v in values
            if v.get("critical", {}).get("image", {}).get("docker-manifest-digest") == digest}
    if not set(repositories) <= refs:
        sys.exit("Verified signatures do not contain the required repository claims")


if __name__ == "__main__":
    if sys.argv[1] == "evidence":
        evidence(sys.argv[2:])
    elif sys.argv[1] == "layout":
        layout(sys.argv[2:])
    elif sys.argv[1] == "claim":
        claim(sys.argv[2:])
    else:
        sys.exit("Unknown verification mode")
