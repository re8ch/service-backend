#!/usr/bin/env python3
"""Export GitLab pipeline metadata, traces, reports, and artifacts for audit."""

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = "collaboration.re8ch.com/pipeline-archive/v1"


class GitLab:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, path, *, query=None, binary=False):
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, headers={"PRIVATE-TOKEN": self.token})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
                return data if binary else json.loads(data)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise

    def pages(self, path, *, query=None):
        page = 1
        while page:
            params = {**(query or {}), "per_page": 100, "page": page}
            url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
            request = urllib.request.Request(url, headers={"PRIVATE-TOKEN": self.token})
            with urllib.request.urlopen(request, timeout=120) as response:
                yield from json.loads(response.read())
                page = int(response.headers.get("X-Next-Page") or 0)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def record_asset(root, path, kind):
    return {
        "path": str(path.relative_to(root)),
        "size": path.stat().st_size,
        "sha256": sha256(path),
        "kind": kind,
    }


def write_asset(root, relative, content, kind, assets):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n")
    assets.append(record_asset(root, path, kind))
    return str(relative)


def export_pipeline(client, project_id, pipeline, root, assets, metadata_only=False):
    pipeline_id = pipeline["id"]
    detail = client.request(f"/projects/{project_id}/pipelines/{pipeline_id}") or pipeline
    jobs = list(client.pages(f"/projects/{project_id}/pipelines/{pipeline_id}/jobs", query={"include_retried": "true"}))
    archived_jobs = []
    for job in jobs:
        job_id = job["id"]
        item = {
            "id": job_id,
            "name": job.get("name", ""),
            "stage": job.get("stage", ""),
            "status": job.get("status", "unknown"),
            "trace": None,
            "artifacts": [],
        }
        if not metadata_only:
            trace = client.request(f"/projects/{project_id}/jobs/{job_id}/trace", binary=True)
            if trace is not None:
                item["trace"] = write_asset(root, Path("pipelines") / str(pipeline_id) / "jobs" / f"{job_id}.trace", trace, "trace", assets)
            artifact_info = job.get("artifacts_file") or {}
            if artifact_info.get("filename"):
                artifact = client.request(f"/projects/{project_id}/jobs/{job_id}/artifacts", binary=True)
                if artifact is None:
                    item["missingReason"] = "unavailable/expired-at-source"
                else:
                    name = Path(artifact_info["filename"]).name
                    relative = Path("pipelines") / str(pipeline_id) / "jobs" / f"{job_id}-{name}"
                    item["artifacts"].append(write_asset(root, relative, artifact, "artifact", assets))
        archived_jobs.append(item)
    report = client.request(f"/projects/{project_id}/pipelines/{pipeline_id}/test_report")
    if report:
        write_asset(root, Path("pipelines") / str(pipeline_id) / "test-report.json", report, "test-report", assets)
    write_asset(root, Path("pipelines") / str(pipeline_id) / "metadata.json", detail, "metadata", assets)
    return {
        "id": pipeline_id,
        "sha": detail["sha"],
        "ref": detail.get("ref"),
        "source": detail.get("source"),
        "status": detail.get("status", "unknown"),
        "webUrl": detail.get("web_url", ""),
        "createdAt": detail.get("created_at"),
        "updatedAt": detail.get("updated_at"),
        "jobs": archived_jobs,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="reachievellc/service-backend")
    parser.add_argument("--base-url", default="https://gitlab.com/api/v4")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        raise SystemExit("GITLAB_TOKEN must be provided through the environment")
    args.output.mkdir(parents=True, exist_ok=True)
    project_id = urllib.parse.quote(args.project, safe="")
    client = GitLab(args.base_url, token)
    pipelines = {}
    for source in (None, "parent_pipeline"):
        query = {"source": source} if source else None
        for pipeline in client.pages(f"/projects/{project_id}/pipelines", query=query):
            pipelines[pipeline["id"]] = pipeline
    assets = []
    exported = []
    errors = []
    for pipeline_id in sorted(pipelines):
        try:
            exported.append(export_pipeline(client, project_id, pipelines[pipeline_id], args.output, assets, args.metadata_only))
        except Exception as error:  # preserve partial evidence and identify the exact pipeline
            errors.append({"pipelineId": pipeline_id, "type": type(error).__name__, "message": str(error)[:1000]})
    job_count = sum(len(pipeline["jobs"]) for pipeline in exported)
    downloaded = sum(len(job["artifacts"]) for pipeline in exported for job in pipeline["jobs"])
    missing = sum("missingReason" in job for pipeline in exported for job in pipeline["jobs"])
    manifest = {
        "schema": SCHEMA,
        "sourceProject": args.project,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "cutoffPipelineId": max(pipelines) if pipelines else None,
        "pipelines": exported,
        "assets": assets,
        "summary": {
            "pipelineCount": len(exported),
            "jobCount": job_count,
            "downloadedArtifacts": downloaded,
            "missingArtifacts": missing,
            "errors": len(errors),
        },
    }
    (args.output / "errors.json").write_text(json.dumps(errors, indent=2, sort_keys=True) + "\n")
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest["summary"], sort_keys=True))
    if errors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
