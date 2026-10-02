# MUBAO WSL uses the cluster Mihomos consumer on R640 during startup.
# Keep control-plane, ZeroTier, WireGuard and cluster destinations direct.
export HTTP_PROXY=http://10.250.0.128:17891
export HTTPS_PROXY=http://10.250.0.128:17891
export NO_PROXY=127.0.0.1,localhost,::1,10.250.0.29,10.250.0.128,10.250.0.147,10.250.0.125,.svc,.svc.cluster.local,.cluster.local,.re8ch.com,10.0.0.0/8,100.64.0.0/10,172.16.0.0/12,192.168.0.0/16
export http_proxy="$HTTP_PROXY"
export https_proxy="$HTTPS_PROXY"
export no_proxy="$NO_PROXY"
