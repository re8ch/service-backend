# Internal operations documentation

Operational runbooks and historical notes live as draft items in the restricted
[Service Backend Internal Operations Project](https://github.com/orgs/re8ch/projects/2).
The public paths are navigation stubs. `sources.json` records their Project item
IDs and the SHA-256 of the original document bytes verified during migration.
These hashes are integrity checks, not encryption or proof of current runtime state.

The Project has no organization base access. Organization owners and explicitly
authorized collaborators can read it. A repository collaborator does not
automatically gain Project access. Keep the Project private and grant only the
required read/write role; do not convert these drafts into public repository issues.

## Reading and maintenance

Open the link in the public stub, or use GitHub's authenticated Projects API.
For an existing authorized CLI identity, obtain a draft with its `draftId` from
`sources.json`:

```sh
gh api graphql -f query='query($id:ID!){node(id:$id){... on DraftIssue{title body}}}' \
  -f id='<draftId>' --jq '.data.node.body'
```

Read this output only in a trusted local session. Do not send it to public Actions
logs, artifacts, Issues, PR bodies, generated MEMORY, or the public repository.
An App needs appropriate organization Projects read access; repository contents
permission alone does not provide that access. The existing Maintenance App has
organization Projects access. `private-operations-docs.yml` uses a downscoped
read-only installation token on a GitHub-hosted runner to verify all pinned bodies
without publishing them. Run it with:

```sh
gh workflow run private-operations-docs.yml --ref main
```

This migration does not expand App permissions or grant all organization members
access. The verification confirms private visibility and exact content; base-role
and collaborator changes still require review in the Project access settings.

The draft body preserves the source revision, original content digest and text.
Relative links in historical text describe its original checkout; use the public
path stubs or the Project's path titles to navigate to another migrated document.
Historical commands require review against authenticated live state before use.

For changes, preserve the prior revision as a separate restricted draft before
replacing a versioned document. Verify the new body through the API and update its
public pointer/digest through a signed PR. Projects is the human documentation
surface, not a runtime configuration store or a substitute for Git object backups.
Secrets and deployment inputs remain in their existing approved custody systems.

## Verification boundary

Public CI checks the pointer manifest and exact stub content without downloading
restricted documents. Migration acceptance independently compares every uploaded
body to the original bytes, including disaster-recovery safety requirements.
An anonymous request to the Project must fail. Current public-source migration
does not erase previous commits, forks, cached views, logs or artifacts.
