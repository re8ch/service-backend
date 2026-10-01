# User Hub release supervision

Use this workflow only when the authenticated person asks to change or monitor
User Hub. The product repository is `re8ch/user-hub`; first resolve the current
GitHub repository URL because ownership may change. Read its `AGENTS.md` when
present and use the repository's own worktree and pull request rules.

1. For a requested code change, prepare a task branch in the User Hub repository,
   run its relevant checks, and open a pull request. Report the PR and exact
   commit. A personal User Hub login is not GitHub write authorization: use the
   current computer's GitHub connection and its repository permissions.
2. Inspect required checks, reviews and deployment environments through GitHub.
   Identify which approval is pending and who can grant it. Submit a review or
   merge only when the user has authorized that action and GitHub permits it.
   Never approve your own change or bypass branch protection.
3. After merge, follow the workflow run for the merged `main` commit through a
   terminal conclusion. If the build publishes an image, record its immutable
   digest and check its signature and deployment declaration. If User Hub is
   published as static assets, verify the upload job and the public page; a
   successful container build alone does not prove the static site was updated.
4. On failure, read the failed job step and report the exact cause and affected
   stage. Inspect the environment and repository policy before retrying. Fix a
   reproducible source or workflow defect through a separate reviewed PR. Do
   not invent credentials or bypass a required approval.
5. Keep the user informed while a run is active. If they ask for monitoring
   after this session, arrange a follow-up using the available scheduler and
   include the PR or run URL. Finish by reporting the final check, approval,
   publication and public-site status with links.

As of 2026-10-02, User Hub `main` run `36878920133` failed at the
`registry.re8ch.com` login step with `Username and password required`. Treat
this as historical evidence to recheck, not as the cause of a future run.
