# restricted-node-6dc3f140f9 Pipecat and OpenStudio voice lab

Pinned versions:

- Pipecat `v1.6.0` (`08e871599904080cedad7ce5683676ab8481fa59`)
- OpenStudio `7db0cf2b7b7880414961552750e06f0ff7e44ef0` (`0.3.2`)

The services run in separate unprivileged LXD containers. Pipecat starts with a
keyless WebRTC echo pipeline so media can be verified before provider secrets
are installed. OpenStudio supplies the private-radio room, mix-minus, per-track
meters/recording and Icecast program output. Neither service is a production
FreeSWITCH media dependency yet.

Internal endpoints (self-signed TLS):

- Pipecat WebRTC lab: `https://192.0.2.219:8443`
- OpenStudio private radio: `https://192.0.2.213:8443`
- Existing LiveKit signaling: `ws://192.0.2.97:17880`

The browser must accept/trust each lab certificate before microphone access is
available. These endpoints are intentionally LAN/VPN-only; no public DNS or
MikroTik forwarding was added.

Secrets live only in `/etc/pipecat/pipecat.env` and
`/etc/openstudio/openstudio.env` in their respective containers. Do not commit
real model, LiveKit, TURN, Icecast or JWT credentials.

Rollback is `lxc stop voice-lab-pipecat` or
`lxc stop voice-lab-openstudio`; this does not alter FreeSWITCH, GXW, HOMER,
Jitsi or LiveKit.

Validation performed on restricted-node-6dc3f140f9:

- Pipecat `/healthz` reports `1.6.0` through the HTTPS proxy.
- OpenStudio signaling and room suites pass all 18 tests.
- OpenStudio reports FFmpeg/MP3 export available and Icecast active locally.
- Whisper and an LLM are not installed; these remain optional provider/model
  choices rather than hidden defaults.
