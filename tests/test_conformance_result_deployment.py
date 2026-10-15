from pathlib import Path
import json
import yaml

BASE=Path(__file__).parents[1]/"operations/api-conformance"


def test_result_proxy_scope_and_identity():
    docs=list(yaml.safe_load_all((BASE/"result-runtime.yaml").read_text()))
    d=next(x for x in docs if x["kind"]=="Deployment")
    assert d["metadata"]["namespace"]=="auth"
    pod=d["spec"]["template"]["spec"]
    c=pod["containers"][0]
    assert "@sha256:" in c["image"] and "REPLACE" not in c["image"]
    assert not pod.get("volumes") and not c.get("volumeMounts")
    assert pod["imagePullSecrets"]==[{"name":"harbor-auth-admin"}]
    env={x["name"]:x["value"] for x in c["env"]}
    assert env["POD_NAMESPACE"]=="api-conformance-system"
    p=json.loads(env["RESULT_OIDC_POLICY"])
    assert p["RepositoryID"]=="1248776882" and p["OwnerID"]=="283958313"
    assert len(p["WorkflowSHA"])==40
    public_targets = {x["metadata"]["name"]: x["spec"] for x in
                      yaml.safe_load_all((BASE / "public-acceptance.yaml").read_text())}
    assert p["WorkflowSHA"] == public_targets["payment-public-contract"]["commit"]
    role=next(x for x in docs if x["kind"]=="Role")
    assert role["metadata"]["namespace"]=="api-conformance-system"
    assert role["rules"]==[
        {"apiGroups":["conformance.re8ch.com"],"resources":["apiconformanceruns"],"verbs":["get"]},
        {"apiGroups":["conformance.re8ch.com"],"resources":["apiconformanceruns/status"],"verbs":["patch"]}]
    route=next(x for x in docs if x["kind"]=="HTTPRoute")
    assert route["spec"]["hostnames"]==["api.re8ch.com"]
    assert route["spec"]["rules"][0]["matches"][0]["path"]["value"]=="/api-conformance/v1/reports"
    for target in yaml.safe_load_all((BASE/"targets.yaml").read_text()):
        assert target["spec"]["suspend"] is True
