#!/bin/sh
set -eu

: "${KUBECTL:=kubectl}"

control_planes="r640 a1-wsl-zt b1-wsl-zt"
for node in ${control_planes}; do
  ready="$(${KUBECTL} get node "${node}" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')"
  [ "${ready}" = True ] || { echo "${node}: not Ready" >&2; exit 1; }
done

endpoints="$(${KUBECTL} -n default get endpointslices -l kubernetes.io/service-name=kubernetes \
  -o jsonpath='{range .items[*].endpoints[*]}{.addresses[0]}{"\n"}{end}')"
printf '%s\n' "${endpoints}"

case "${ROLLOUT_STAGE:-observe}" in
  observe) : ;;
  api)
    for address in 10.250.0.125 10.250.0.128 10.250.0.147; do
      printf '%s\n' "${endpoints}" | grep -Fx "${address}" >/dev/null || {
        echo "missing API EndpointSlice address ${address}" >&2
        exit 1
      }
    done
    ;;
  *) echo "unknown ROLLOUT_STAGE=${ROLLOUT_STAGE}" >&2; exit 2 ;;
esac

${KUBECTL} get --raw '/readyz?verbose' >/dev/null
echo "control-plane API fabric gate passed (${ROLLOUT_STAGE:-observe})"
