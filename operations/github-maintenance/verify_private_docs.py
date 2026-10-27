#!/usr/bin/env python3
"""Bounded private Project verification; never log document bodies or API errors."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def query(cursor=None):
    payload = {'query': '''query($cursor:String){organization(login:"re8ch"){
      projectV2(number:2){public items(first:100,after:$cursor){
        pageInfo{hasNextPage endCursor} nodes{id content{... on DraftIssue{id title body}}}
      }}}}''', 'variables': {'cursor': cursor}}
    result = subprocess.run(['gh', 'api', 'graphql', '--input', '-'],
                            input=json.dumps(payload), text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise ValueError('Project API unavailable')
    response = json.loads(result.stdout)
    if response.get('errors'):
        raise ValueError('Project API unavailable')
    project = response['data']['organization']['projectV2']
    if project is None or project['public']:
        raise ValueError('Project privacy check failed')
    return project['items']


def validate_document(row, item):
    content = item.get('content') or {}
    if content.get('id') != row['draftId'] or content.get('title') != row['path']:
        raise ValueError('Document identity mismatch')
    body = content.get('body', '')
    header, separator, original = body.partition('\n\n---\n\n')
    if not separator or f"Content SHA-256: `{row['sha256']}`" not in header:
        raise ValueError('Document metadata mismatch')
    if hashlib.sha256(original.encode()).hexdigest() != row['sha256']:
        raise ValueError('Document content mismatch')


def main():
    manifest = json.loads((ROOT / 'contracts/private-operations-docs/sources.json').read_text())
    items, cursor = {}, None
    for _ in range(20):
        page = query(cursor)
        items.update({item['id']: item for item in page['nodes']})
        if not page['pageInfo']['hasNextPage']:
            break
        cursor = page['pageInfo']['endCursor']
    else:
        raise ValueError('Project pagination limit exceeded')
    for row in manifest['documents']:
        validate_document(row, items[row['itemId']])
    print(json.dumps({'result': 'passed', 'documents': len(manifest['documents'])}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Private document verification failed; inspect through authenticated Project access') from None
