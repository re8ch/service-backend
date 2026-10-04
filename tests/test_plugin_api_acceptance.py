import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).parents[1]
BASE = ROOT / 'operations/plugin-capabilities/api-acceptance'
spec = importlib.util.spec_from_file_location('api_acceptance', BASE / 'verify_report.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
CASES = json.loads((BASE / 'cases.json').read_text())['cases']

@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_case_is_bound_to_captured_finite_get(case):
    args = case['arguments']
    filename = args['groupVersion'].replace('/', '_') + '.json'
    document = json.loads((BASE.parent / 'openapi' / filename).read_text())
    assert hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest() == args['schemaHash']
    matches = [(path, method) for path, item in document['paths'].items()
               for method, op in item.items() if isinstance(op, dict) and op.get('operationId') == args['operationId']]
    assert len(matches) == 1
    path, method = matches[0]
    assert method == 'get' and '{' not in path and '/watch/' not in path
    assert args['parameters'] == {'query': {'limit': 1}}

def result():
    args = CASES[0]['arguments']
    report = dict(operationId=args['operationId'], schemaHash=args['schemaHash'], cluster='re8ch-k3s',
                  statusCode=200, engine='schemathesis', scope='one_finite_GET_response',
                  httpConformance='passed', bodyReturned=False, networkRequests=1,
                  auditRef='audit://operations/op_fixture',
                  checks=[{'check': c, 'status': 'passed'} for c in module.CHECKS])
    return {'caseId': CASES[0]['id'], 'result': {'structuredContent': report}}

def test_complete_valid_result_passes_and_missing_cases_fail():
    assert module.verify(CASES[:1], [result()])['complete']
    assert not module.verify(CASES, [result()])['complete']

@pytest.mark.parametrize('field,value', [('statusCode',401),('schemaHash','stale'),('operationId','other'),
    ('auditRef',''),('checks',[]),('httpConformance','failed'),('bodyReturned',True)])
def test_failed_or_unbound_response_cannot_pass(field, value):
    row = result()
    row['result']['structuredContent'][field] = value
    assert not module.verify(CASES[:1], [row])['complete']

def test_tool_error_duplicates_and_unknown_cases_fail():
    row = result()
    row['result']['isError'] = True
    assert not module.verify(CASES[:1], [row])['complete']
    assert not module.verify(CASES[:1], [result(), result()])['complete']
    row['caseId'] = 'unknown'
    assert not module.verify(CASES[:1], [row])['complete']
