"""External reconciler decision kernel. Input evidence must be authenticated.

Produces plans, never credentials or authorization. No Kubernetes writes, local
desired-state cache, event ordering assumptions, or automatic live-to-Git import.
"""
import hashlib
import json


def decide(evidence):
    def result(action, reason, **extra):
        return dict(action=action, reason=reason, authorization=False, **extra)

    required = {
        'cluster', 'service', 'objectKey', 'uid', 'resourceVersion',
        'desiredRevision', 'acceptedRevision', 'policyRevision',
        'desiredInputDigest', 'acceptedInputDigest', 'acceptedValid',
        'acceptedSpecDigest', 'observedSpecDigest', 'observation',
        'ownership', 'hold', 'repairMode', 'changeClass', 'runtime',
    }
    if not isinstance(evidence, dict) or set(evidence) != required:
        return result('unavailable', 'invalid-evidence-shape')
    e = evidence
    enums = {
        'observation': {'fresh', 'stale', 'gap', 'disconnected'},
        'ownership': {'managed', 'runtime', 'frozen', 'unknown'},
        'hold': {'none', 'active', 'expired', 'unknown'},
        'repairMode': {'automatic', 'review'},
        'changeClass': {'expected', 'unattributed', 'conflict'},
        'runtime': {'healthy', 'unhealthy', 'unknown'},
    }
    for key, choices in enums.items():
        if not isinstance(e[key], str) or e[key] not in choices:
            return result('unavailable', 'invalid-' + key)
    text_fields = required - set(enums) - {'acceptedValid', 'uid', 'resourceVersion', 'observedSpecDigest'}
    if any(not isinstance(e[k], str) or not e[k] for k in text_fields):
        return result('unavailable', 'missing-authority-identity')
    if type(e['acceptedValid']) is not bool:
        return result('unavailable', 'unknown-acceptance-validity')
    # This first version does not plan creation/deletion: absent-object evidence
    # requires an independently complete relist and a separate policy predicate.
    if any(not isinstance(e[k], str) or not e[k]
           for k in ('uid', 'resourceVersion', 'observedSpecDigest')):
        return result('unavailable', 'incomplete-object-observation')
    if e['observation'] != 'fresh':
        return result('observe', 'fresh-snapshot-required')
    if not e['acceptedValid']:
        return result('blocked', 'accepted-chain-invalid')
    if e['hold'] != 'none':
        return result('held', 'explicit-release-required' if e['hold'] == 'expired'
                      else 'emergency-hold-' + e['hold'])
    # Unrelated main commits do not unnecessarily stop this service. Authority
    # resolver must independently compute complete service input digests.
    if e['desiredInputDigest'] != e['acceptedInputDigest']:
        return result('await-acceptance', 'service-desired-not-accepted')
    if e['ownership'] == 'unknown':
        return result('review', 'unknown-field-ownership')
    if e['ownership'] == 'runtime':
        return result('observe', 'runtime-owned-fields')
    if e['acceptedSpecDigest'] == e['observedSpecDigest']:
        return result('no-spec-change', 'spec-matches', runtime=e['runtime'])
    if e['ownership'] == 'frozen':
        return result('escalate', 'frozen-contract-drift')
    if e['changeClass'] != 'expected':
        return result('review', 'unattributed-or-conflicting-change')
    if e['repairMode'] != 'automatic':
        return result('review', 'service-requires-review')
    binding = {k: e[k] for k in (
        'cluster', 'service', 'objectKey', 'uid', 'resourceVersion',
        'desiredRevision', 'acceptedRevision', 'policyRevision',
        'acceptedInputDigest', 'acceptedSpecDigest', 'observedSpecDigest')}
    fingerprint = hashlib.sha256(json.dumps(binding, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    return result('plan-repair', 'eligible-for-external-approval',
                  planId=fingerprint, preconditions=binding)


if __name__ == '__main__':
    import sys
    print(json.dumps(decide(json.load(sys.stdin)), sort_keys=True))
