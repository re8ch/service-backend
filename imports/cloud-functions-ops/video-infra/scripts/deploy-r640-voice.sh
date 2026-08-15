#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/opt/video-infra-src}"
ENV_FILE=/etc/re8ch-voice/voice-gateway.env
FS_CONF=/opt/freeswitch/etc/freeswitch
[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 1; }
[[ -x /opt/freeswitch/bin/freeswitch ]] || { echo "FreeSWITCH not installed" >&2; exit 1; }

backup="/var/backups/re8ch-voice/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$backup"
systemctl cat kamailio >"$backup/kamailio.service.txt" || true
systemctl cat gxw-sip-autoanswer >"$backup/gxw-sip-autoanswer.service.txt" || true
ss -lntup >"$backup/listeners.txt"
cp -a /etc/kamailio "$backup/" 2>/dev/null || true

if [[ ! -f "$ENV_FILE" ]]; then
  api_secret=$(openssl rand -hex 32)
  esl_secret=$(openssl rand -hex 32)
  sed -e "s/REPLACE_API_HMAC_SECRET/$api_secret/" \
      -e "s/REPLACE_ESL_PASSWORD/$esl_secret/" \
      "$REPO_ROOT/voice-gateway/voice-gateway.env.example" >"$ENV_FILE"
  chmod 0600 "$ENV_FILE"
fi
if ! grep -q '^VOICE_AGENTS=' "$ENV_FILE"; then
  agents=""
  for id in agent01 agent02 agent03 agent04; do
    secret=$(openssl rand -hex 24)
    agents="${agents}${agents:+,}${id}:${secret}"
  done
  printf '\nVOICE_AGENTS=%s\n' "$agents" >>"$ENV_FILE"
fi
esl_password=$(sed -n 's/^FREESWITCH_ESL_PASSWORD=//p' "$ENV_FILE")
[[ ${#esl_password} -ge 32 ]] || { echo "invalid ESL password" >&2; exit 1; }
sed -i "s/REPLACE_ESL_PASSWORD/$esl_password/" "$FS_CONF/autoload_configs/event_socket.conf.xml"
chmod 0640 "$FS_CONF/autoload_configs/event_socket.conf.xml"
chown freeswitch:freeswitch "$FS_CONF/autoload_configs/event_socket.conf.xml"

agent_file="$FS_CONF/directory/agents.xml"
printf '%s\n' '<include><domain name="voice.internal"><params><param name="dial-string" value="{sip_invite_domain=${dialed_domain}}${sofia_contact(${dialed_user}@${dialed_domain})}"/></params><groups><group name="agents"><users>' >"$agent_file"
IFS=',' read -ra agent_pairs <<<"$(sed -n 's/^VOICE_AGENTS=//p' "$ENV_FILE")"
for pair in "${agent_pairs[@]}"; do
  id=${pair%%:*}; password=${pair#*:}
  [[ "$id" =~ ^[A-Za-z0-9_.-]{1,64}$ && "$password" =~ ^[A-Fa-f0-9]{32,128}$ ]] || { echo "invalid VOICE_AGENTS entry" >&2; exit 1; }
  printf '  <user id="%s"><params><param name="password" value="%s"/></params><variables><variable name="user_context" value="from-agents"/></variables></user>\n' "$id" "$password" >>"$agent_file"
done
printf '%s\n' '</users></group></groups></domain></include>' >>"$agent_file"
chmod 0640 "$agent_file"; chown freeswitch:freeswitch "$agent_file"

# Perform structural validation before touching the listener. FreeSWITCH has no
# side-effect-free config-check mode and 5160 is deliberately still occupied.
find "$FS_CONF" -type f -name '*.xml' -print0 | xargs -0 -n1 xmllint --noout

systemctl disable --now gxw-sip-autoanswer.service
if ! systemctl enable --now freeswitch.service; then
  systemctl enable --now gxw-sip-autoanswer.service
  echo "FreeSWITCH failed; restored gxw-sip-autoanswer" >&2
  exit 1
fi
systemctl enable --now re8ch-voice-gateway.service
echo "activated; backup=$backup"
