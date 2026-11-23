# Resource deletion acknowledgments

Native resource deletion acknowledges the API request without waiting for Pod
grace periods or finalizers. A successful Cloud operation reports
`deletionAccepted: true, completionObserved: false`; this does not prove the
object has disappeared. Read back its UID/deletionTimestamp and the replacement
workload's readiness before treating a rebuild as complete. After a transport
timeout, inspect actual state before replaying a write. The operation deadline
is unchanged; ordinary graceful deletion no longer races that deadline.

The regression test uses real kubectl with an isolated local Kubernetes API
fixture that retains a finalizer after accepting DELETE. It verifies one
accepted write, a prompt acknowledgment, and a subsequent real GET that still
observes the same terminating object. No cluster credential is used.
