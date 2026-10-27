#!/usr/bin/env python3
"""Check a certified build commit against the current flat release source."""
import argparse
from pathlib import Path
import re
import subprocess
import yaml

def verify(commit, contract_path, root=Path('.')):
    if not re.fullmatch(r'[0-9a-f]{40}',commit):raise ValueError('reuse requires an exact build commit SHA')
    path=Path(contract_path)
    if path.is_absolute() or '..' in path.parts:raise ValueError('contract path escapes repository')
    contract=yaml.safe_load((root/path).read_text())
    source=Path(contract['spec']['source']['path'])
    if source.is_absolute() or '..' in source.parts:raise ValueError('source path escapes repository')
    kustomization=yaml.safe_load((root/source/'kustomization.yaml').read_text())
    if set(kustomization)-{'apiVersion','kind','resources'} or kustomization.get('resources')!=['resources.yaml']:
        raise ValueError('reuse requires a flat locked resource source without external rendering dependencies')
    if any(p.is_symlink() for p in (root/source).rglob('*')):
        raise ValueError('reuse source must not contain symlinks')
    for command in [['git','cat-file','-e',commit+'^{commit}'],['git','merge-base','--is-ancestor',commit,'HEAD'],['git','diff','--exit-code',commit,'HEAD','--',str(path),str(source)]]:
        result=subprocess.run(command,cwd=root,capture_output=True)
        if result.returncode:raise ValueError('certified build source is unavailable or differs from current release')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--commit',required=True);parser.add_argument('--contract',required=True);args=parser.parse_args()
    verify(args.commit,args.contract)
