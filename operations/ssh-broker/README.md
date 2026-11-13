# RE8CH Admin SSH broker

The SSH broker is the platform authority for audited node command transport. It
is deliberately separate from Kubernetes operations:

```text
Re8CH Admin OAuth -> tenant MCP dynamic module adapter
                  -> service-token authenticated SSH broker
                  -> reviewed target registry + managed SSH identity
                  -> node management endpoint
```

The broker does not mount a kubeconfig, does not use a Kubernetes service
account token, and never discovers targets from Kubernetes Node objects. Its
target registry and SSH client configuration are generated from the reviewed
encrypted infrastructure contracts. The private key remains mounted only in
the broker workload. API results contain the logical target and route class,
never credential material or node addresses.

The compatibility `platform_node_ssh_exec` tool remains at the Admin plugin
surface. The tenant MCP adapter authenticates the human Admin and delegates
the exact argv to this internal broker. The broker independently authenticates
that adapter with a service token and emits structured actor, target, route,
command-digest, result and duration audit events plus an
`audit://ssh-broker/...` reference. Raw commands are not copied into the audit
event.

## Reliability and growth

Two replicas and a disruption budget preserve availability. Each command is
executed exactly once: the broker does not automatically retry because argv may
be mutating. As the fleet grows, revisit the in-process executor in favor of a
durable operation queue and short-lived SSH certificates. Do not add Kubernetes
Node discovery or caller-supplied addresses as a shortcut.
