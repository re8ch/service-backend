# GitHub security products rollout

This contract separates read-only detection, automated remediation and licensed
analysis. A repository being eligible for a product is not approval to enable
it, and an unavailable endpoint is not evidence that the product was disabled.

## Product boundaries

| Capability | Effect | Cost and rollout boundary |
| --- | --- | --- |
| Dependabot alerts | Read-only vulnerable-dependency findings | Enable broadly; public, private and internal repositories are eligible |
| Dependabot security updates | Creates remediation pull requests | Enable only after factor ownership, grouping, concurrency and private-registry access are declared |
| Secret scanning on public repositories | Detects supported credentials in GitHub surfaces and history | Native public coverage; close the public gap first |
| Push protection on public repositories | Blocks new pushes containing supported secrets and records bypass | Canary before organization rollout; use delegated review rather than personal exemption |
| Secret Protection on private/internal repositories | Licensed secret scanning and push protection | Preview incremental unique active committers and monthly cost before selecting repositories |
| Code Security / CodeQL | Licensed or public source analysis with workflow compute | Review languages, existing scanners and runner placement once per revision before enablement |
| AI secret detection and push protection | Detects unstructured secrets | Excluded from baseline; separately billed AI credits and preview behavior require a new Issue |

The default delivery profile is
[`static-first-profile.json`](static-first-profile.json). It uses GitHub's
native server-side repository settings and adds no repository workflow,
required check, CodeQL setup or build-minute consumer. The profile is validated
offline before review. Effective repository state is still read back after a
merged declaration is applied.

Dependabot alerts and security updates must never be represented as the same
control. Alerts can be enabled without creating pull requests. Update PRs enter
the normal Issue/Project/PR transaction model and must not bypass it merely
because the author is Dependabot.

## Repository selection

1. Enable free public secret scanning and push protection gaps, beginning with
   a low-activity documentation or examples canary.
2. Keep primary GitOps authorities covered by secret scanning and push
   protection. CodeQL remains repository-specific because manifest validation,
   dependency review and CodeQL detect different defect classes.
3. For private/internal repositories, run GitHub's cost preview and select Tier
   3 authorities first. Record both incremental active committers and the
   repositories whose enablement adds no additional license consumption.
4. Exclude archived repositories unless their retained history has an explicit
   scanning and incident-response owner.

## Canary contract

The first public canary is `advanced-fabrics/examples`, a public Tier 1
repository with no open pull requests at declaration time. Only secret scanning
and provider-pattern push protection may be enabled. Dependabot security-update
PRs, Code Security, non-provider patterns, validity checks and AI detection are
separate decisions.

Acceptance requires:

- read back `secret_scanning=enabled` and
  `secret_scanning_push_protection=enabled`;
- preserve read-only workflow-token and repository admission settings;
- record no alert, match or secret value in Git, logs or Issues;
- observe ordinary signed push and pull-request creation without bypass;
- record every push-protection bypass request through the approved reviewer
  path; and
- retain the reviewed disable sequence as rollback.

Existing history may generate alerts after scanning is enabled. Those alerts are
private security evidence and do not justify rewriting history or rotating a
credential without a separate exposure assessment.

The initial canary was applied after its declaration merged. Effective readback
confirmed secret scanning and provider-pattern push protection enabled while
Dependabot security updates, Code Security and AI detection remained disabled.
No synthetic pull request was created solely as evidence; the next legitimate
signed change supplies the developer-path observation.

The remaining eight active public gaps were subsequently applied as two
declared server-side batches. All eight read back with Secret Scanning and Push
Protection enabled, Dependabot alerts enabled, security-update pull requests
disabled, AI detection disabled and CodeQL default setup not configured. The
rollout triggered no repository Actions runs and added no workflow, required
check, CodeQL setup or repository build-minute consumer.

## Cost decision

Private/internal activation is blocked until an owner records GitHub's current
pricing preview. Billing is based on unique active committers to selected
repositories over the applicable activity window, not raw repository count.
The preview must include current licenses, incremental committers, monthly
estimate, billing mode and a disable/retention decision. No static dollar total
is committed because it becomes stale and can expose internal staffing data.

The current 80 private/internal gaps are candidates, not an activation plan.
Tier 3 is only the first review queue. A repository remains blocked if product
availability, active-committer overlap, incremental monthly estimate or an
accountable retention and rollback owner is missing.

## Maintenance

- Event-driven: new repository, visibility change, archive/unarchive, security
  configuration change or bypass.
- Daily: new critical alerts only, routed privately.
- Weekly: aggregate coverage drift without alert contents.
- Quarterly: cost preview, active-committer overlap, false-positive trends,
  bypass actors and duplicate scanner review.

The detailed repository classification and alert inventory remain outside the
public repository. This directory stores only aggregate evidence and reviewed
policy.
