"""Stable public tool catalog; transport code delegates to these methods."""

TOOLS = {
    "credentials.stalwart.ensure": "stalwart_bootstrap.call",
    "stalwart.admin.read": "stalwart_admin.call",
    "stalwart.admin.plan": "stalwart_admin.call",
    "stalwart.admin.apply": "stalwart_admin.call",
    "credentials.webmail-session.ensure": "ensure_webmail_session_secret",
    "mihomos.controllers.list": "mihomos_capabilities.controllers",
    "mihomos.controller.version": "mihomos_capabilities.controller_version",
    "mihomos.workloads.list": "mihomos_capabilities.workloads",
    "plugin.operations.list": "plugin_capabilities.operations",
    "plugin.operation.read": "plugin_capabilities.read_operation",
    "plugin.capabilities.list": "plugin_capabilities.catalog",
    "plugin.openapi.get": "plugin_capabilities.openapi",
    "anatomy.runtimediff.create": "anatomy_diff_operation.call",
    "anatomy.runtimediff.delete": "anatomy_diff_operation.delete",
    "nodes.list": "nodes_list",
    "nodes.get": "nodes_get",
    "nodes.reachability": "node_reachability",
    "node.ssh.exec": "node_ssh_exec",
    "kube.nodes": "kube_simple",
    "kube.namespaces": "kube_simple",
    "kube.pods": "kube_simple",
    "kube.deployments": "kube_simple",
    "kube.services": "kube_simple",
    "kube.endpoints": "kube_simple",
    "kube.secrets": "kube_simple",
    "kube.resource.get": "kube_resource",
    "kube.resource.list": "kube_resource",
    "kube.resource.watch": "kube_resource",
    "kube.resource.apply": "kube_resource",
    "kube.resource.patch": "kube_resource",
    "kube.resource.delete": "kube_resource",
    "kube.events.query": "kube_events",
    "kube.events.watch": "kube_events",
}

# Session and transfer tools are provided by the existing audited SSH transport.
# They are listed here so schema discovery is stable while implementation is
# promoted behind the same Credential Broker boundary.
SSH_STREAM_TOOLS = {
    "credentials.stalwart.ensure": "stalwart_bootstrap.call",
    "node.ssh.session.start",
    "node.ssh.session.poll",
    "node.ssh.session.write",
    "node.ssh.session.terminate",
    "node.ssh.upload",
    "node.ssh.download",
}

# Controller tools derive their exact native routes from the reviewed contract.
from mihomos_capabilities import TOOLS as MIHOMOS_TOOLS
for tool in MIHOMOS_TOOLS:
    if tool["name"] not in TOOLS:
        TOOLS[tool["name"]] = "mihomos_capabilities.controller_read"
