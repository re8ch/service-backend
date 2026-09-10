#!/bin/sh
set -eu

mode=${1:---check}
namespace=runtime-anatomy-canary
claim=runtime-anatomy-source-v3
rook_namespace=rook-ceph
node_secret=rook-csi-cephfs-node
filesystem=re8ch-files

case "$mode" in --check|--apply|--rollback) ;; *) echo "usage: $0 [--check|--apply|--rollback]" >&2; exit 2;; esac

pv=$(kubectl -n "$namespace" get pvc "$claim" -o jsonpath='{.spec.volumeName}')
test -n "$pv"
driver=$(kubectl get pv "$pv" -o jsonpath='{.spec.csi.driver}')
test "$driver" = rook-ceph.cephfs.csi.ceph.com
path=$(kubectl get pv "$pv" -o jsonpath='{.spec.csi.volumeAttributes.subvolumePath}')
case "$path" in /volumes/csi/csi-vol-*) ;; *) echo "refusing unexpected CephFS subvolume path" >&2; exit 1;; esac

user_id=$(kubectl -n "$rook_namespace" get secret "$node_secret" -o jsonpath='{.data.userID}' | base64 -d)
entity="client.$user_id"
tools=$(kubectl -n "$rook_namespace" get pod -l app=rook-ceph-tools -o jsonpath='{.items[0].metadata.name}')
test -n "$tools"
current=$(kubectl -n "$rook_namespace" exec "$tools" -- ceph auth get "$entity" -f json | jq -r '.[0].caps.mds')
desired="allow rws fsname=$filesystem path=$path, allow rw"

if [ "$mode" = --check ]; then
  printf 'current mds caps: %s\n' "$current"
  printf 'desired path: %s\n' "$path"
  test "$current" = "$desired"
  exit
fi

case "$current" in
  "allow rw"|"$desired"|"allow rws fsname=$filesystem path=/volumes/csi/csi-vol-"*", allow rw") ;;
  *) echo "refusing to replace unexpected MDS caps: $current" >&2; exit 1;;
esac

mds_caps=$desired
test "$mode" != --rollback || mds_caps="allow rw"
kubectl -n "$rook_namespace" exec "$tools" -- ceph auth caps "$entity" \
  mon 'allow r' \
  mgr 'allow rw' \
  mds "$mds_caps" \
  osd 'allow rwx tag cephfs metadata=*, allow rw tag cephfs data=*' >/dev/null

actual=$(kubectl -n "$rook_namespace" exec "$tools" -- ceph auth get "$entity" -f json | jq -r '.[0].caps.mds')
test "$actual" = "$mds_caps"
printf 'applied mds caps: %s\n' "$actual"
