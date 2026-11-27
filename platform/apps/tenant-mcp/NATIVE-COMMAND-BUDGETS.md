# Native command time budgets

Admin `platform_kubectl_exec` defaults to a 600-second execution budget and
accepts explicit values from 1 to 3600 seconds. Short request deadlines remain
useful for read-only diagnostics; they are not the budget for cold cross-cluster
OpenAPI validation or a real SSA operation.

The shared MCP HTTP client has no separate total timeout. The calling context
controls HTTP cancellation and the native backend enforces its explicit command
budget. Module connection setup retains its existing bounded deadline. Failed
tool invocations are never automatically replayed: a lost response can follow
a committed write, so read back actual state first.

This repairs a mismatch where the interface accepted long command budgets but
its HTTP client truncated responses at 60 seconds. The regression inspects the
actual outgoing HTTP context deadline, so it detects that truncation without
waiting for a one-minute test timer.

Before replacing the current single-replica Recreate runtime, pull the exact
verified image using an unselected, temporary Pod on its actual node. Give it
no service-account token, credentials, application labels or state volume, and
run only `/bin/true`. Verify the image ID and successful completion, then deploy
the same digest through the signed GitOps PR. Keep the temporary Pod until the
new runtime is Ready, then remove it through the Admin preview/apply deletion
path. This avoids an image-download outage and
does not create a second image build or a persistent controller.
