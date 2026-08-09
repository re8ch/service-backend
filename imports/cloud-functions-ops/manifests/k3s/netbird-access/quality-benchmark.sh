#!/bin/sh
set -u

INTERVAL_SECONDS="${INTERVAL_SECONDS:-900}"
DOWNLOAD_BYTES="${DOWNLOAD_BYTES:-5000000}"
GATEWAY_IP="${GATEWAY_IP:-192.0.2.234}"

run_once() {
  timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  ping_output="$(ping -c 10 -W 2 "$GATEWAY_IP" 2>&1 || true)"
  loss_pct="$(printf '%s\n' "$ping_output" | sed -n 's/.* \([0-9.]*\)% packet loss.*/\1/p' | tail -1)"
  rtt_avg_ms="$(printf '%s\n' "$ping_output" | awk -F'=' '/round-trip|rtt/{gsub(/ /,"",$2); split($2,a,"/"); print a[2]}' | tail -1)"
  dns_output="$(dig +time=8 +tries=1 www.google.com A 2>&1 || true)"
  dns_ms="$(printf '%s\n' "$dns_output" | awk '/Query time:/{print $4}' | tail -1)"
  dns_answer="$(printf '%s\n' "$dns_output" | awk '/^www\.google\.com\..*IN[[:space:]]+A[[:space:]]+/{print $NF; exit}')"
  google="$(curl -4 -sS -o /dev/null -w '%{http_code} %{time_total} %{remote_ip}' --max-time 20 https://www.google.com/generate_204 2>/dev/null || true)"
  download="$(curl -4 -sS -o /dev/null -w '%{http_code} %{size_download} %{speed_download} %{time_total} %{remote_ip}' --max-time 60 "https://speed.cloudflare.com/__down?bytes=${DOWNLOAD_BYTES}" 2>/dev/null || true)"
  set -- $google
  google_code="${1:-000}"; google_total="${2:-0}"; google_ip="${3:-}"
  set -- $download
  download_code="${1:-000}"; downloaded="${2:-0}"; download_Bps="${3:-0}"; download_total="${4:-0}"; download_ip="${5:-}"
  jq -nc \
    --arg timestamp "$timestamp" --arg gateway "$GATEWAY_IP" \
    --arg loss "${loss_pct:-100}" --arg rtt "${rtt_avg_ms:-0}" \
    --arg dns_ms "${dns_ms:-0}" --arg dns_answer "${dns_answer:-}" \
    --arg google_code "$google_code" --arg google_total "$google_total" --arg google_ip "$google_ip" \
    --arg download_code "$download_code" --arg downloaded "$downloaded" \
    --arg download_Bps "$download_Bps" --arg download_total "$download_total" --arg download_ip "$download_ip" \
    '{timestamp:$timestamp,gateway:$gateway,ping:{lossPct:($loss|tonumber),rttAvgMs:($rtt|tonumber)},dns:{queryMs:($dns_ms|tonumber),answer:$dns_answer},google:{httpCode:($google_code|tonumber),totalSeconds:($google_total|tonumber),remoteIp:$google_ip},download:{httpCode:($download_code|tonumber),bytes:($downloaded|tonumber),bytesPerSecond:($download_Bps|tonumber),megabitsPerSecond:(($download_Bps|tonumber)*8/1000000),totalSeconds:($download_total|tonumber),remoteIp:$download_ip}}'
}

while true; do
  run_once
  [ "${RUN_ONCE:-false}" = true ] && exit 0
  sleep "$INTERVAL_SECONDS"
done
