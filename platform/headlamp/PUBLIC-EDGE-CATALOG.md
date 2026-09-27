# Public Edge external provider catalog

`public-edge-catalog.yaml` is a read-only snapshot displayed by the Headlamp
Public Edge plugin. Refresh it from an operator workstation with configured
`tccli` and `aliyun` read access:

```sh
python operations/scripts/refresh-public-edge-catalog.py
```

The refresh reads Tencent EdgeOne zones and acceleration domains, plus Aliyun
ESA sites and their DNS records. ESA records show `CDN proxied` or `DNS only`;
the latter do not claim CDN delivery. Record values are not exported, including
TXT verification values. An inaccessible Tencent zone is reported and its
existing catalog entries are retained.

Other externally delivered domains can be added to the `sites` array with
`sourceOfTruth: manual`. Refresh preserves those entries. Review the generated
diff before committing. This catalog does not change provider DNS or routing.
