import importlib.util
from pathlib import Path
import sys

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/'operations/stalwart-github-intake'))
import compocv


def test_retry_keeps_operator_fields_and_reconciles_project(monkeypatch):
    c=object.__new__(compocv.ProjectGithub)
    c.base='https://api.github.com/repos/compocv/compocv';c.auth='synthetic'
    c.config={'project_id':'P','defaults':{'Status':{'field':'status','option':'todo'},'Environment':{'field':'env','option':'test'}}}
    monkeypatch.setattr(compocv.intake.Github,'reconcile',lambda self,item:'https://github.com/compocv/compocv/issues/9')
    monkeypatch.setattr(compocv.intake,'request',lambda *args:{'node_id':'I'})
    updates=[]
    def graph(query,variables):
        if 'addProjectV2ItemById' in query:return {'addProjectV2ItemById':{'item':{'id':'item'}}}
        if 'fieldValues' in query:return {'node':{'fieldValues':{'nodes':[{'field':{'id':'status'}}]}}}
        updates.append(variables);return {}
    c.graph=graph
    assert c.reconcile({}).endswith('/9')
    assert updates==[{'project':'P','item':'item','field':'env','option':'test'}]
