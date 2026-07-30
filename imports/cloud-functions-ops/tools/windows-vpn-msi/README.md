# RE8CH Per-Device Windows VPN MSI Builder

Builds a device-specific Windows MSI containing:

- the RE8CH IKEv2 root CA;
- one device's PKCS#12 client identity;
- a no-prompt certificate and VPN profile installer;
- a guarded connection test with a 90-second independent disconnect watchdog;
- a standard MSI uninstall action.

The generated MSI is itself a private credential because it contains the
device's private key. Store and transfer it accordingly.

Use `--profile-mode management` (default) for the private-network split tunnel.
Use `--profile-mode egress` for the independent public Egress profile. Egress
packages install a one-minute Fleet safety watchdog and use a separate
certificate identity; do not reuse a management certificate for Egress.

## Build

```bash
tools/windows-vpn-msi/build-device-msi.py \
  --device-id harry-wincloud \
  --display-name "Harry Windows Cloud" \
  --pfx local/windows-vpn/re8ch-harry-wincloud/re8ch-harry-wincloud-v1.p12 \
  --pfx-password-file local/windows-vpn/re8ch-harry-wincloud/.p12-password \
  --ca local/windows-vpn/re8ch-harry-wincloud/re8ch-ikev2-ca.crt \
  --output-dir local/windows-vpn/msi
```

The builder uses `fleetdm/wix:latest` through Docker. It writes only to a
temporary build directory and the selected output directory.

## Safety defaults

- Manual connection only.
- Split tunneling enabled.
- No default-route takeover.
- No DNS takeover.
- Only the explicitly configured RE8CH private routes use the VPN.
- The MSI never runs the connection test automatically.
- The packaged test always disconnects and has an independent 90-second
  watchdog.
- The client private key is imported as non-exportable.

Default private routes:

```text
192.0.2.16/24
192.0.2.139/24
192.0.2.136/23
192.0.2.253/24
```

Use repeated `--route` arguments to replace that default set.

## Output

For a device ID such as `harry-wincloud`:

```text
RE8CH-VPN-harry-wincloud-<version>.msi
RE8CH-VPN-harry-wincloud-<version>.manifest.json
RE8CH-VPN-harry-wincloud-<version>.sha256
```

The manifest deliberately excludes the PKCS#12 password.
