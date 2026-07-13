# ZeroTier Custom Planet Build

This directory builds a patched ZeroTier One binary or container image whose
compiled default planet is the RE8CH private planet.

It is intended for RouterOS container or CHR experiments. It does not produce a
MikroTik native `.npk` package.

## Version Mapping

The live RB4011 is running RouterOS `7.9.2` with the MikroTik ZeroTier extra
package. RouterOS 7.9 upgraded MikroTik ZeroTier to upstream ZeroTier One
`1.10.3`, so this build pins:

```text
ZeroTierOne tag: 1.10.3
ZeroTierOne commit: dc762196dc65d3de0b7adc6b4ac8bece95f5cd9e
Target container architecture: linux/arm/v7
```

The local private planet file is supplied at build time and is not committed to
this repository.

## Build A Patched ARMv7 Container

From the repository root:

```bash
tools/zerotier-custom-planet/build-armv7.sh /Volumes/A1/planet
```

The build script copies the planet file into a temporary clean Docker build
context, then the Dockerfile clones `zerotier/ZeroTierOne` at tag `1.10.3`,
injects the planet bytes into `node/Topology.cpp`, and builds `zerotier-one`.

Local verification on 2026-07-09 produced:

```text
image: re8ch/zerotier-one-custom-planet:1.10.3-armv7
image platform: linux/arm/v7
image id: sha256:2d2b143743135edea67b0604f0738b20c4fecfcba8322440a4ac9f1345d0e640
zerotier-one -v: 1.10.3
binary: ELF 32-bit LSB pie executable, ARM, EABI5, armhf dynamic linker
embedded planet bytes: 276
```

## Run On A Normal Linux Host

The resulting image still needs the same runtime privileges as standard
ZeroTier containers:

```bash
docker run --rm -it \
  --name zt-re8ch-test \
  --cap-add NET_ADMIN \
  --device /dev/net/tun \
  re8ch/zerotier-one-custom-planet:1.10.3-armv7 \
  2a26a65d4ed79856
```

Check status:

```bash
docker exec zt-re8ch-test zerotier-cli info
docker exec zt-re8ch-test zerotier-cli listnetworks
```

## Patch A Checked-Out Source Tree

For debugging without Docker:

```bash
git clone --branch 1.10.3 --depth 1 https://github.com/zerotier/ZeroTierOne.git /tmp/zt-1.10.3
python3 tools/zerotier-custom-planet/inject_custom_planet.py \
  /tmp/zt-1.10.3 \
  /Volumes/A1/planet \
  --expected-ref 1.10.3 \
  --expected-commit dc762196dc65d3de0b7adc6b4ac8bece95f5cd9e
git -C /tmp/zt-1.10.3 diff -- node/Topology.cpp
```

## RouterOS Notes

The RB4011 currently has no `container` package installed and only 512 MiB of
NAND. RouterOS container tests should be done in a maintenance window or in CHR
first. ZeroTier containers require TUN/TAP-like support; RouterOS versions have
had community-reported regressions around this.
