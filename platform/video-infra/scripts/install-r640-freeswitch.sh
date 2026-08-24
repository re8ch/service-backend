#!/usr/bin/env bash
set -euo pipefail

FS_VERSION="${FS_VERSION:-v1.11.1}"
FS_PREFIX="${FS_PREFIX:-/opt/freeswitch}"
SOURCE_DIR="${SOURCE_DIR:-/usr/local/src/freeswitch-${FS_VERSION}}"
REPO_ROOT="${REPO_ROOT:-/opt/video-infra-src}"
SPANDSP_REF=e29ef78944d905b935d1306fa622e2eb2dc8ad75
SOFIA_REF=a404c3109d3d4d0b2007c79205a6776aece15d46

[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 1; }
[[ -f "$REPO_ROOT/freeswitch/freeswitch.service" ]] || { echo "missing staged repository at $REPO_ROOT" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
UBUNTU_SOURCES="${UBUNTU_SOURCES:-/etc/apt/sources.list.d/node.list}"
apt-get update -o "Dir::Etc::sourcelist=$UBUNTU_SOURCES" -o Dir::Etc::sourceparts=-
apt-get install -y --no-install-recommends \
  autoconf automake build-essential ca-certificates cmake git libcurl4-openssl-dev \
  libedit-dev libldns-dev liblua5.4-dev libncurses-dev libopus-dev libpcre2-dev libxml2-utils \
  libsndfile1-dev libspeex-dev libspeexdsp-dev libsqlite3-dev libssl-dev libtool libtool-bin \
  libtiff-dev libuuid1 pkg-config python3 rsync uuid-dev yasm zlib1g-dev

build_autotools_dependency() {
  local name=$1 url=$2 ref=$3 branch=${4:-master}
  local dir="/usr/local/src/$name"
  if [[ ! -d "$dir/.git" ]]; then git clone --depth 1 --branch "$branch" "$url" "$dir"; fi
  cd "$dir"; git fetch --depth 1 origin "$ref"; git checkout --detach "$ref"
  ./bootstrap.sh
  ./configure --prefix=/usr/local --with-pic
  make -j"$(nproc)"; make install
}

export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:/usr/local/lib64/pkgconfig:${PKG_CONFIG_PATH:-}"
pkg-config --exists 'spandsp >= 3.0' || build_autotools_dependency freeswitch-spandsp https://github.com/freeswitch/spandsp.git "$SPANDSP_REF" packages
pkg-config --exists 'sofia-sip-ua >= 1.13.17' || build_autotools_dependency freeswitch-sofia-sip https://github.com/freeswitch/sofia-sip.git "$SOFIA_REF"
ldconfig

if [[ ! -d "$SOURCE_DIR/.git" ]]; then
  git clone --depth 1 --branch "$FS_VERSION" https://github.com/signalwire/freeswitch.git "$SOURCE_DIR"
fi
cd "$SOURCE_DIR"
git fetch --depth 1 origin "refs/tags/$FS_VERSION:refs/tags/$FS_VERSION"
git checkout --detach "$FS_VERSION"
# FreeSWITCH 1.11.1 unconditionally promotes warnings to errors.  GCC 15 adds
# new qualifier diagnostics for otherwise valid upstream code, so retain the
# warnings while allowing the pinned release to build on Ubuntu 26.04.
sed -i 's/APR_ADDTO(SWITCH_AM_CFLAGS, -Werror)/APR_ADDTO(SWITCH_AM_CFLAGS, -Wno-error)/g' configure.ac
./bootstrap.sh -j

# Build only the audited voice-gateway module set.
install -m 0644 "$REPO_ROOT/freeswitch/modules.conf" modules.conf
./configure --prefix="$FS_PREFIX" --disable-core-libedit-support
make -j"$(nproc)"
make install
make samples

getent group freeswitch >/dev/null || groupadd --system freeswitch
id freeswitch >/dev/null 2>&1 || useradd --system --gid freeswitch --home "$FS_PREFIX" --shell /usr/sbin/nologin freeswitch
getent group voice-gateway >/dev/null || groupadd --system voice-gateway
id voice-gateway >/dev/null 2>&1 || useradd --system --gid voice-gateway --home /var/lib/re8ch-voice --shell /usr/sbin/nologin voice-gateway

FS_CONF="$FS_PREFIX/etc/freeswitch"
FS_STATE="$FS_PREFIX/var/lib/freeswitch"
FS_LOG="$FS_PREFIX/var/log/freeswitch"
FS_RUN="$FS_PREFIX/var/run/freeswitch"
if [[ -d "$FS_CONF" && ! -e "$FS_CONF.factory" ]]; then cp -a "$FS_CONF" "$FS_CONF.factory"; fi
# Keep the runtime configuration auditable: do not leave sample SIP profiles
# (notably 5060/5080) enabled beside the two explicitly managed profiles.
rsync -a --delete "$REPO_ROOT/freeswitch/conf/" "$FS_CONF/"
install -d -o freeswitch -g freeswitch "$FS_LOG" "$FS_STATE/db" "$FS_STATE/recordings" "$FS_RUN"
chown -R freeswitch:freeswitch "$FS_CONF" "$FS_LOG" "$FS_STATE" "$FS_RUN"
install -m 0644 "$REPO_ROOT/freeswitch/freeswitch.service" /etc/systemd/system/freeswitch.service
install -d -m 0755 /opt/re8ch-voice /etc/re8ch-voice /var/lib/re8ch-voice
install -m 0755 "$REPO_ROOT/voice-gateway/voice_gateway.py" /opt/re8ch-voice/voice_gateway.py
install -m 0644 "$REPO_ROOT/voice-gateway/re8ch-voice-gateway.service" /etc/systemd/system/re8ch-voice-gateway.service
chown voice-gateway:voice-gateway /var/lib/re8ch-voice

systemctl daemon-reload
echo "installed $FS_VERSION; generate secrets and run deploy-r640-voice.sh to activate"
