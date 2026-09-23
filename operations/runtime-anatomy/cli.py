#!/usr/bin/env python3
"""Create and inspect explicit Runtime Anatomy storage analyses."""
import argparse
import json
import subprocess
import sys
import time


GROUP = "anatomy.re8ch.com/v1alpha1"
TERMINAL = {"Ready", "Incomplete", "Blocked", "Failed"}
EXIT_CODES = {"Ready": 0, "Incomplete": 2, "Blocked": 3, "Failed": 4}


def kubectl(*args, stdin=None, output=True):
    result = subprocess.run(["kubectl", *args], input=stdin, text=True, capture_output=True)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"kubectl {' '.join(args)} failed ({result.returncode}): {detail[:2000]}")
    if not output:
        return result.stdout.strip()
    return json.loads(result.stdout)


def parse_volume(value):
    mount_path, separator, claim = value.partition("=")
    if not mount_path.startswith("/"):
        raise argparse.ArgumentTypeError("volume must be /mount/path or /mount/path=claim")
    result = {"mountPath": mount_path}
    if separator:
        result["claimName"] = claim
    return result


def parse_object_scope(value):
    # ENDPOINT/BUCKET/PREFIX is deliberately not inferred. The compact CLI form is BUCKET[:PREFIX].
    bucket, separator, prefix = value.partition(":")
    if not bucket:
        raise argparse.ArgumentTypeError("object scope must be BUCKET or BUCKET:PREFIX")
    return {"bucket": bucket, "prefix": prefix if separator else ""}


def resource(namespace, name):
    return kubectl("-n", namespace, "get", "runtimeanalysis", name, "-o", "json")


def start(args):
    name = args.name or f"analysis-{int(time.time())}"
    spec = {
        "target": {"podName": args.pod, "containerName": args.container},
        "durationSeconds": args.duration,
        "volumes": args.volume,
        "objectScopes": args.s3_scope,
        "evidencePolicy": {
            "captureTextSnippets": not args.no_snippets,
            "maxSnippetBytes": args.max_snippet_bytes,
            "maxSnippetCount": args.max_snippet_count,
            "maxTotalSnippetBytes": args.max_total_snippet_bytes,
            "retentionSeconds": args.retention,
        },
    }
    if args.trace_id:
        spec["traceId"] = args.trace_id.lower()
    body = {"apiVersion": GROUP, "kind": "RuntimeAnalysis", "metadata": {"name": name, "namespace": args.namespace}, "spec": spec}
    kubectl("apply", "-f", "-", stdin=json.dumps(body), output=False)
    print(name)
    if args.wait:
        return wait_for(args.namespace, name, args.timeout, args.json)
    return 0


def stop(args):
    patch = json.dumps({"spec": {"stop": True}})
    kubectl("-n", args.namespace, "patch", "runtimeanalysis", args.name, "--type=merge", "-p", patch, output=False)
    print(args.name)
    return 0


def wait_for(namespace, name, timeout, as_json=False):
    deadline = time.time() + timeout
    while time.time() < deadline:
        item = resource(namespace, name)
        phase = item.get("status", {}).get("phase")
        if phase in TERMINAL:
            render(item, as_json)
            return EXIT_CODES[phase]
        time.sleep(2)
    raise TimeoutError(f"RuntimeAnalysis {namespace}/{name} did not complete within {timeout}s")


def wait_command(args):
    return wait_for(args.namespace, args.name, args.timeout, args.json)


def show(args):
    item = resource(args.namespace, args.name)
    render(item, args.json, args.events)
    return EXIT_CODES.get(item.get("status", {}).get("phase"), 1)


def render(item, as_json=False, events=False):
    if as_json:
        print(json.dumps(item, indent=2, sort_keys=True))
        return
    metadata, spec, status = item.get("metadata", {}), item.get("spec", {}), item.get("status", {})
    target = spec.get("target", {})
    correlation = status.get("correlation", {})
    print(f"RuntimeAnalysis {metadata.get('namespace')}/{metadata.get('name')}")
    print(f"Phase: {status.get('phase', 'Pending')}")
    print(f"Target: {target.get('podName')}/{target.get('containerName')}")
    print(f"Correlation: {correlation.get('confidence', 'Pending')}" + (f" trace={correlation['traceId']}" if correlation.get("traceId") else ""))
    summary = status.get("summary", {})
    print(f"Events: {summary.get('eventCount', 0)}  Bytes: {summary.get('bytes', 0)}")
    for backend, count in sorted(summary.get("backendCounts", {}).items()):
        print(f"  {backend}: {count}")
    for operation, count in sorted(summary.get("operationCounts", {}).items()):
        print(f"  {operation}: {count}")
    for locator in status.get("topLocators", []):
        print(f"    {locator}")
    evidence = status.get("evidence", {})
    if evidence:
        print(f"Evidence: {evidence.get('digest')} {evidence.get('uri') or evidence.get('configMap')}")
    if events and evidence.get("configMap"):
        config = kubectl("-n", evidence.get("namespace", "runtime-anatomy"), "get", "configmap", evidence["configMap"], "-o", "json")
        for event in json.loads(config.get("data", {}).get("events.json", "[]")):
            locator = event.get("path") or f"s3://{event.get('bucket', '')}/{event.get('key', '')}"
            print(f"{event.get('operation', 'Unknown'):8} {locator}")
    for entry in status.get("conditions", []):
        if entry.get("status") == "False":
            print(f"Finding: {entry.get('reason')}: {entry.get('message', '')}")


def explain(args):
    item = resource(args.namespace, args.name)
    status = item.get("status", {})
    render(item, args.json)
    if args.json:
        return EXIT_CODES.get(status.get("phase"), 1)
    reason = next((condition.get("reason") for condition in reversed(status.get("conditions", []))), None)
    explanations = {
        "ObserverEvidenceUnavailable": "目标容器已解析，但没有收到可归因的文件系统或对象存储审计事件。检查节点 observer、cgroup v2/bpffs 和 S3 audit source。",
        "CaptureWindowExpired": "分析窗口结束前 observer 没有密封完整证据；现有结果只能作为部分证据。",
        "AnalysisFailed": "目标、mount、observer evidence 或策略验证失败；查看 condition message。",
        "EvidenceSealed": "证据已完成规范化、摘要和 digest 密封。",
    }
    print(explanations.get(reason, "分析仍在进行，或尚无更具体的解释。"))
    return EXIT_CODES.get(status.get("phase"), 1)


def build_parser():
    parser = argparse.ArgumentParser(prog="runtime-anatomy", description=__doc__)
    parser.add_argument("-n", "--namespace", default="default")
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze")
    actions = analyze.add_subparsers(dest="action", required=True)
    create = actions.add_parser("start")
    create.add_argument("--name")
    create.add_argument("--pod", required=True)
    create.add_argument("--container", required=True)
    create.add_argument("--trace-id")
    create.add_argument("--duration", type=int, default=60, choices=range(1, 901), metavar="SECONDS")
    create.add_argument("--volume", action="append", type=parse_volume, default=[])
    create.add_argument("--s3-scope", action="append", type=parse_object_scope, default=[])
    create.add_argument("--no-snippets", action="store_true")
    create.add_argument("--max-snippet-bytes", type=int, default=4096)
    create.add_argument("--max-snippet-count", type=int, default=100)
    create.add_argument("--max-total-snippet-bytes", type=int, default=1048576)
    create.add_argument("--retention", type=int, default=86400)
    create.add_argument("--wait", action="store_true")
    create.add_argument("--timeout", type=int, default=930)
    create.add_argument("--json", action="store_true")
    create.set_defaults(func=start)
    finish = actions.add_parser("stop")
    finish.add_argument("name")
    finish.set_defaults(func=stop)
    waiting = actions.add_parser("wait")
    waiting.add_argument("name")
    waiting.add_argument("--timeout", type=int, default=930)
    waiting.add_argument("--json", action="store_true")
    waiting.set_defaults(func=wait_command)
    showing = actions.add_parser("show")
    showing.add_argument("name")
    showing.add_argument("--events", action="store_true")
    showing.add_argument("--json", action="store_true")
    showing.set_defaults(func=show)
    explaining = actions.add_parser("explain")
    explaining.add_argument("name")
    explaining.add_argument("--json", action="store_true")
    explaining.set_defaults(func=explain)
    return parser


def main():
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except (RuntimeError, TimeoutError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
