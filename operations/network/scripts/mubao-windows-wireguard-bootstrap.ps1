$ErrorActionPreference = 'Stop'
$exe = 'C:\Program Files\WireGuard\wireguard.exe'
$directory = 'C:\ProgramData\RE8CH\wireguard'
$keyPath = Join-Path $directory 'mubao-host.key'
$configPath = Join-Path $directory 'mubao-r640.conf'
$tunnelService = 'WireGuardTunnel$mubao-r640'

if (-not (Test-Path -LiteralPath $exe)) { throw 'WireGuard is not installed' }
if (-not (Test-Path -LiteralPath $keyPath)) { throw 'MUBAO private key is missing' }
if (Get-Service -Name $tunnelService -ErrorAction SilentlyContinue) {
    throw 'MUBAO tunnel already exists; inspect before changing it'
}
if (Test-Path -LiteralPath $configPath) {
    throw 'MUBAO tunnel config already exists; inspect before changing it'
}

$key = (Get-Content -LiteralPath $keyPath -Raw).Trim()
if ($key -notmatch '^[A-Za-z0-9+/]{43}=$') { throw 'Private key has unexpected format' }
$config = @"
[Interface]
PrivateKey = $key
Address = 192.0.2.98/32
MTU = 1280

[Peer]
PublicKey = H6trdN+xorHMYEFsPV6G8CMLmhMZkObgnQfcXYUSNVQ=
AllowedIPs = 192.0.2.198/32, 192.0.2.179/32
Endpoint = 192.0.2.138:51893
PersistentKeepalive = 15
"@
[System.IO.File]::WriteAllText($configPath, $config, [System.Text.Encoding]::ASCII)
& icacls.exe $configPath /inheritance:r /grant:r 'SYSTEM:F' 'Administrators:F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict WireGuard config ACL' }
& $exe /installtunnelservice $configPath
Start-Sleep -Seconds 4
$service = Get-Service -Name $tunnelService -ErrorAction Stop
Get-Acl -LiteralPath $keyPath | Set-Acl -LiteralPath $configPath
Write-Output ('TunnelService={0} Status={1}' -f $service.Name, $service.Status)
