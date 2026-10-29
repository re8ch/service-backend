"""One bounded Actions invocation; GitHub issue markers persist deduplication."""
import json
import os
from pathlib import Path
import sqlite3
import intake


class ProjectGithub(intake.Github):
    def __init__(self, config, token):
        super().__init__(config['repository'], token)
        self.config=config
        project=self.graph('query($id:ID!){node(id:$id){... on ProjectV2{public owner{... on Organization{login}}}}}',{'id':config['project_id']})['node']
        if not project or project['public'] or project['owner']['login']!=config['project_owner']:
            raise ValueError('feedback Project must be private and belong to the declared owner')

    def graph(self, query, variables):
        result=intake.request('https://api.github.com/graphql',self.auth,{'query':query,'variables':variables})
        if result.get('errors'):raise RuntimeError('GitHub Projects operation failed')
        return result['data']

    def reconcile(self, item):
        url=super().reconcile(item)
        number=url.rsplit('/',1)[-1]
        if not number.isdigit():raise ValueError('invalid issue URL')
        issue=intake.request(self.base+'/issues/'+number,self.auth)
        added=self.graph('mutation($project:ID!,$content:ID!){addProjectV2ItemById(input:{projectId:$project,contentId:$content}){item{id}}}',{'project':self.config['project_id'],'content':issue['node_id']})['addProjectV2ItemById']['item']['id']
        fields=self.graph('query($id:ID!){node(id:$id){... on ProjectV2Item{fieldValues(first:100){nodes{... on ProjectV2ItemFieldSingleSelectValue{field{... on ProjectV2SingleSelectField{id}}}}}}}}',{'id':added})['node']['fieldValues']['nodes']
        populated={value['field']['id'] for value in fields if value.get('field')}
        # Never reset an operator's triage, horizon or environment on later scans.
        for value in self.config['defaults'].values():
            if value['field'] not in populated:
                self.graph('mutation($project:ID!,$item:ID!,$field:ID!,$option:String!){updateProjectV2ItemFieldValue(input:{projectId:$project,itemId:$item,fieldId:$field,value:{singleSelectOptionId:$option}}){projectV2Item{id}}}',{'project':self.config['project_id'],'item':added,'field':value['field'],'option':value['option']})
        return url


def main():
    config=json.loads(Path(__file__).with_name('compocv.json').read_text())
    mail=intake.Jmap(config,os.environ['STALWART_APP_PASSWORD'])
    github=ProjectGithub(config,os.environ['GH_TOKEN'])
    # Workflow concurrency serializes all runs. Every new invocation reconciles
    # GitHub's durable issue markers, including after ambiguous create responses.
    with sqlite3.connect(':memory:') as db:
        result=intake.run(config,mail,github,db)
    print(json.dumps({'messages':len(result),'rejected':sum(x['status']=='rejected' for x in result)}))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'status':'failed','error_type':type(error).__name__}));raise SystemExit(1)
