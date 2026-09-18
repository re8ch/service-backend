# IncidentRelay

IncidentRelay is the on-call delivery and responsibility engine for RE8CH. It
owns rotations, escalation timing, Telegram delivery and authenticated
ACK/Resolve/Shelve actions. Secretary remains the evidence and recovery
verification control plane; it must not duplicate Telegram polling or on-call
scheduling.

## Supply-chain relationship

Flux reads `OCIRepository/incidentrelay` in `flux-system`, pinned to the
reviewed upstream IncidentRelay chart `2.2.0` by OCI digest. The matching
`HelmRelease` installs that chart into the `incidentrelay` namespace. No copy of
the upstream chart is stored in this repository.

The chart uses the SOPS-managed `incidentrelay-config` Secret and the shared
Pigsty primary Service. Bot tokens and Telegram chat/user IDs are configured in
the IncidentRelay HTTPS administration UI and never committed to Git.
Telegram Bot API traffic uses the cluster-local Mihomos mixed proxy explicitly;
the namespace policy permits only TCP/17891 to `egress-fabric` for that path.

## Initial configuration

- UI: `https://oncall.tools.re8ch.com`
- Group: `RE8CH`
- Team: `Platform`
- Service: `RE8CH Cluster`
- Telegram group: `Re8ch Incident Response`
- Telegram bot: `@re8ch_inci_resp_bot`

After first login, rotate the bootstrap password, configure the Telegram
channel, send `/register` in the group, bind the numeric Telegram user ID to the
operator profile, and run a critical-alert ACK/escalation game day. A provider
acceptance or Telegram delivery result is not human responsibility evidence;
only an attributed IncidentRelay action is an ACK.
