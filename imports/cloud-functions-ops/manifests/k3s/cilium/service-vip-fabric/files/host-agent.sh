#!/bin/sh
set -eu
trap 'rc=$?; [ "$rc" -eq 0 ] || echo "advanced-fabric-agent: node=${NODE_NAME:-unknown} exit=${rc} line=${LINENO:-unknown}" >&2' EXIT

NODE_FILE="/desired/${NODE_NAME}.json"
READY=/run/advanced-fabric-ready
rm -f "${READY}"

host() { nsenter -t 1 -n chroot /host "$@"; }
transaction() { jq -c '.transaction.spec' "${NODE_FILE}"; }

validate_transaction() {
  [ "$(jq -r '.transaction.algorithm' "${NODE_FILE}")" = sha256 ]
  expected=$(jq -r '.transaction.checksum' "${NODE_FILE}")
  actual=$(printf '%s' "$(transaction)" | sha256sum | awk '{print $1}')
  [ "${actual}" = "${expected}" ]
  transaction | jq -e --arg node "${NODE_NAME}" '.version == 1 and .node == $node and
    (.vip | test("^10\\.250\\.0\\.[0-9]{1,3}/32$")) and .vipInterface == "lo" and
    (.frrPrefixSequence >= 1 and .frrPrefixSequence <= 999)' >/dev/null
}

manage_fallback_routes() {
  action=$1
  transaction | jq -c '.fallbackRoutes[]?' | while read -r route; do
    destination=$(printf '%s' "${route}" | jq -r '.destination')
    via=$(printf '%s' "${route}" | jq -r '.via // empty')
    dev=$(printf '%s' "${route}" | jq -r '.dev')
    src=$(printf '%s' "${route}" | jq -r '.src // empty')
    metric=$(printf '%s' "${route}" | jq -r '.metric')
    if [ "${action}" = apply ]; then
      if [ -n "${via}" ] && [ -n "${src}" ]; then
        host ip route replace "${destination}" via "${via}" dev "${dev}" src "${src}" metric "${metric}"
      elif [ -n "${via}" ]; then
        host ip route replace "${destination}" via "${via}" dev "${dev}" metric "${metric}"
      else
        host ip route replace "${destination}" dev "${dev}" metric "${metric}"
      fi
    else
      host ip route del "${destination}" dev "${dev}" metric "${metric}" 2>/dev/null || true
    fi
  done
}

manage_wireguard_allowed_ips() {
  action=$1
  transaction | jq -r '.wireguardInterfaces[]?' | while read -r iface; do
    peers=$(host wg show "${iface}" peers)
    [ "$(printf '%s\n' "${peers}" | sed '/^$/d' | wc -l)" -eq 1 ]
    transaction | jq -r '.wireguardAllowedPrefixes[]?' | while read -r allowed; do
      prefix=+${allowed}; [ "${action}" = apply ] || prefix=-${allowed}
      host wg set "${iface}" peer "${peers}" allowed-ips "${prefix}"
    done
  done
}

frr_vip() {
  action=$1
  vip=$(transaction | jq -r '.vip')
  asn=$(transaction | jq -r '.localAsn')
  sequence=$(transaction | jq -r '.frrPrefixSequence')
  [ "${asn}" != null ] || return 0
  if [ "${action}" = apply ]; then network="network ${vip}"; else network="no network ${vip}"; fi
  host vtysh -c 'configure terminal' -c "router bgp ${asn}" -c 'address-family ipv4 unicast' -c "${network}" -c end >/dev/null
  transaction | jq -r '.frrExportPrefixLists[]?' | while read -r list; do
    if [ "${action}" = apply ]; then command="ip prefix-list ${list} seq ${sequence} permit ${vip}"; else command="no ip prefix-list ${list} seq ${sequence}"; fi
    host vtysh -c 'configure terminal' -c "${command}" -c end >/dev/null
  done
}

withdraw_vip() {
  frr_vip remove || true
  delay=$(jq -r '.controlPlaneApi.healthCheck.withdrawDelaySeconds' "${NODE_FILE}")
  [ "${delay}" -eq 0 ] || sleep "${delay}"
  host ip address del "$(transaction | jq -r '.vip')" dev "$(transaction | jq -r '.vipInterface')" 2>/dev/null || true
}

announce_vip() {
  host ip address replace "$(transaction | jq -r '.vip')" dev "$(transaction | jq -r '.vipInterface')"
  frr_vip apply
}

api_healthy() {
  timeout_seconds=$(jq -r '.controlPlaneApi.healthCheck.timeoutSeconds' "${NODE_FILE}")
  host timeout "${timeout_seconds}" k3s kubectl --server=https://127.0.0.1:6443 get --raw=/readyz 2>/dev/null | grep -qx ok
}

while [ ! -s "${NODE_FILE}" ]; do sleep 2; done
validate_transaction
host systemctl is-active --quiet frr
successes=0; failures=0; announced=false

while :; do
  apply=$(jq -r '.mode == "guarded-apply" and .applyEnabled == true' "${NODE_FILE}")
  guarded=$(transaction | jq -r '.guarded')
  if [ "${apply}" != true ]; then
    withdraw_vip; manage_wireguard_allowed_ips remove; manage_fallback_routes remove
    successes=0; failures=0; announced=false
  else
    manage_fallback_routes apply
    manage_wireguard_allowed_ips apply
    if [ "${guarded}" != true ]; then
      withdraw_vip; successes=0; failures=0; announced=false
    elif api_healthy; then
      successes=$((successes + 1)); failures=0
      threshold=$(jq -r '.controlPlaneApi.healthCheck.successThreshold' "${NODE_FILE}")
      if [ "${announced}" = false ] && [ "${successes}" -ge "${threshold}" ]; then announce_vip; announced=true; fi
    else
      failures=$((failures + 1)); successes=0
      threshold=$(jq -r '.controlPlaneApi.healthCheck.failureThreshold' "${NODE_FILE}")
      if [ "${announced}" = true ] && [ "${failures}" -ge "${threshold}" ]; then withdraw_vip; announced=false; fi
    fi
  fi
  touch "${READY}"
  sleep "$(jq -r '.controlPlaneApi.healthCheck.intervalSeconds' "${NODE_FILE}")"
done
