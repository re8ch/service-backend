# Mihomos Cluster Headlamp plugin

Displays per-node consumer health, exit roles, directly observed public IP/region,
the pinned Geo policy direction, and CN/global service reachability reported by
the `mihomos-status-agent` DaemonSet installed by the Helm chart.

```bash
npm install
npm run build
npm run package
```

The plugin is read-only. It reads ConfigMaps labelled
`app.kubernetes.io/component=mihomos-node-status` in `egress-fabric`.
