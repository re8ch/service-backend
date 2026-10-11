# DNS Authority admission notes

DNS Authority and Public Edge Candidate are independent roles. Public Edge
requires measured ingress capacity and a working Gateway path. DNS Authority
requires a Ready node, a global public IP, and authoritative SOA and address
answers over both public UDP and TCP port 53. The controller elects at most
three healthy, region-diverse authorities and reconciles Cloudflare child-zone
NS records. PowerDNS cloud nodes are listed as external authorities so that
the embedded Public Edge DNS DaemonSet does not contend for host port 53.

**R640–MikroTik: actively excluded from DNS Authority admission.** Unless the
China Telecom upstream has opened public UDP and TCP port 53 and independent
external probes confirm both protocols, do not recommend or elect it as a DNS
Authority. Its Public Edge Candidate status is evaluated separately.

The four cloud PowerDNS instances retain local zone files and refresh from
the existing public authorities using AXFR. They can serve their last received
zones during a primary outage. This stage does not yet perform independent
per-node AdvFab routing calculations in PowerDNS; that requires a separate
local backend before the cloud nodes fully match the independent-authority
contract used by RouterOS. Public DNS probes and actual Cloudflare delegation
must be checked after the GitOps rollout. Provider security groups must allow
inbound UDP and TCP 53 before a cloud node can enter the NS set.
