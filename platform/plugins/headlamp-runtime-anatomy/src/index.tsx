import { K8s, registerRoute, registerSidebarEntry } from '@kinvolk/headlamp-plugin/lib';
import { SectionBox, StatusLabel, Table } from '@kinvolk/headlamp-plugin/lib/components/common';
import { makeCustomResourceClass } from '@kinvolk/headlamp-plugin/lib/lib/k8s/crd';
import { Alert, Box, Chip, Tab, Tabs, Typography } from '@mui/material';
import { useMemo, useState } from 'react';

const VolumeSnapshot = makeCustomResourceClass({
  apiInfo: [{group: 'snapshot.storage.k8s.io', version: 'v1'}],
  kind: 'VolumeSnapshot',
  pluralName: 'volumesnapshots',
  singularName: 'volumesnapshot',
  isNamespaced: true,
});
const CSIDriver = makeCustomResourceClass({
  apiInfo: [{group: 'storage.k8s.io', version: 'v1'}],
  kind: 'CSIDriver',
  pluralName: 'csidrivers',
  singularName: 'csidriver',
  isNamespaced: false,
});

function anatomyResource(kind: string, pluralName: string) {
  return makeCustomResourceClass({
    apiInfo: [{group: 'anatomy.re8ch.com', version: 'v1alpha1'}],
    kind,
    pluralName,
    singularName: pluralName.replace(/s$/, ''),
    isNamespaced: true,
  });
}

const Runtime = anatomyResource('Runtime', 'runtimes');
const RuntimeCommit = anatomyResource('RuntimeCommit', 'runtimecommits');
const RuntimeBranch = anatomyResource('RuntimeBranch', 'runtimebranches');
const RuntimeCheckout = anatomyResource('RuntimeCheckout', 'runtimecheckouts');
const raw = (item: any) => item?.jsonData || item || {};

type RuntimeScope = {
  metadata?: {name?: string};
  spec?: {
    lifecycle?: string;
    consistencyDefault?: string;
    maxCaptureSkew?: string;
    manifestStore?: Record<string, any>;
    storageBackends?: any[];
    stateClasses?: any[];
    images?: {policy?: Record<string, any>; catalog?: any[]};
    integrations?: Record<string, any>;
    credentials?: Record<string, any>;
    observation?: Record<string, any>;
    retention?: Record<string, any>;
  };
};

function parseScope(item: any): RuntimeScope | null {
  try {
    return JSON.parse(raw(item)?.data?.['scope.json'] || '');
  } catch {
    return null;
  }
}

function shortDigest(value?: string) {
  if (!value) return '-';
  const digest = value.includes('@') ? value.split('@').pop()! : value;
  return digest.length > 24 ? `${digest.slice(0, 18)}…${digest.slice(-6)}` : digest;
}

function phaseColor(value?: string): 'success' | 'warning' | 'error' | '' {
  if (['Ready', 'Sealed', 'Healthy', 'Verified', 'Active'].includes(value || '')) return 'success';
  if (['Failed', 'Invalid', 'Blocked', 'Unhealthy'].includes(value || '')) return 'error';
  return value ? 'warning' : '';
}

function KeyValueChips({values}: {values: string[]}) {
  return <Box sx={{display: 'flex', gap: .5, flexWrap: 'wrap'}}>
    {(values || []).map(value => <Chip key={value} size="small" label={value}/>) }
  </Box>;
}

function Dashboard() {
  const [tab, setTab] = useState(0);
  const [maps, scopeError] = K8s.ResourceClasses.ConfigMap.useList({namespace: 'runtime-anatomy'} as any);
  const [claims, claimError] = K8s.ResourceClasses.PersistentVolumeClaim.useList({} as any);
  const [snapshots, snapshotError] = VolumeSnapshot.useList({} as any);
  const [drivers, driverError] = CSIDriver.useList({} as any);
  const [runtimes, runtimeError] = Runtime.useList({} as any);
  const [commits, commitError] = RuntimeCommit.useList({} as any);
  const [branches, branchError] = RuntimeBranch.useList({} as any);
  const [checkouts, checkoutError] = RuntimeCheckout.useList({} as any);

  const scopeMaps = (maps || []).map(raw).filter((item: any) =>
    item.metadata?.labels?.['anatomy.re8ch.com/config-role'] === 'final-scope');
  const scope = scopeMaps.map(parseScope).find(Boolean) as RuntimeScope | undefined;
  const spec = scope?.spec || {};
  const driverNames = new Set((drivers || []).map(raw).map((item: any) => item.metadata?.name));
  const claimObjects = (claims || []).map(raw);
  const snapshotObjects = (snapshots || []).map(raw);

  const backendRows = useMemo(() => (spec.storageBackends || []).map((backend: any) => {
    const expectedDriver = backend.csiDriverName ||
      (backend.type === 'CephFS' ? 'rook-ceph.cephfs.csi.ceph.com' :
       backend.type === 'RBD' ? 'rook-ceph.rbd.csi.ceph.com' : undefined);
    const matchingClaims = claimObjects.filter((claim: any) =>
      backend.storageClassName && claim.spec?.storageClassName === backend.storageClassName);
    const relatedSnapshots = snapshotObjects.filter((snapshot: any) => {
      const source = snapshot.spec?.source?.persistentVolumeClaimName;
      return matchingClaims.some((claim: any) =>
        claim.metadata?.name === source && claim.metadata?.namespace === snapshot.metadata?.namespace);
    });
    const driverState = backend.type === 'LocalCache' ? 'Reconstructable' :
      expectedDriver && driverNames.has(expectedDriver) ? 'Registered' : 'Unavailable';
    return {...backend, expectedDriver, matchingClaims, relatedSnapshots, driverState};
  }), [spec.storageBackends, claimObjects, snapshotObjects, drivers]);

  const timelineUnavailable = runtimeError || commitError || branchError || checkoutError;
  const tabs = ['Overview', 'Storage', 'State classes', 'Images', 'Timeline'];

  return <Box sx={{p: 2}}>
    <Typography variant="h4">Runtime Anatomy</Typography>
    <Typography color="text.secondary">
      Runtime 持久态、存储健康、镜像 provenance 与 commit timeline 的只读视图。
    </Typography>
    <Tabs value={tab} onChange={(_, value) => setTab(value)} sx={{mb: 2}}>
      {tabs.map(label => <Tab key={label} label={label}/>) }
    </Tabs>

    {scopeError && <Alert severity="error">无法读取 runtime-anatomy scope：{String(scopeError)}</Alert>}
    {!scopeError && !scope && <Alert severity="warning">未发现 final RuntimeScope ConfigMap；这不是健康的空配置。</Alert>}

    {tab === 0 && <>
      <SectionBox title="Final scope">
        <Table data={scope ? [scope] : []} columns={[
          {header: 'Scope', accessorFn: (x: any) => x.metadata?.name || '-'},
          {header: 'Lifecycle', accessorFn: (x: any) => <StatusLabel status={x.spec?.lifecycle === 'Production' ? 'success' : 'warning'}>{x.spec?.lifecycle || '-'}</StatusLabel>},
          {header: 'Consistency', accessorFn: (x: any) => x.spec?.consistencyDefault || '-'},
          {header: 'Capture skew', accessorFn: (x: any) => x.spec?.maxCaptureSkew || '-'},
          {header: 'Commit store', accessorFn: (x: any) => x.spec?.manifestStore?.type || '-'},
          {header: 'GC', accessorFn: (x: any) => <StatusLabel status={x.spec?.retention?.gcMode === 'DryRun' ? 'warning' : 'success'}>{x.spec?.retention?.gcMode || '-'}</StatusLabel>},
          {header: 'Credentials', accessorFn: (x: any) => x.spec?.credentials?.captureValues ? <StatusLabel status="error">Values captured</StatusLabel> : <StatusLabel status="success">References only</StatusLabel>},
        ] as any}/>
      </SectionBox>
      <SectionBox title="Runtime integrations">
        <Table data={Object.entries(spec.integrations || {}).map(([name, value]: any) => ({name, ...value}))} columns={[
          {header: 'Integration', accessorKey: 'name'},
          {header: 'Enabled', accessorFn: (x: any) => <StatusLabel status={x.enabled ? 'success' : 'warning'}>{x.enabled ? 'Enabled' : 'Disabled'}</StatusLabel>},
          {header: 'Namespace', accessorFn: (x: any) => x.namespace || '-'},
          {header: 'Ownership', accessorFn: (x: any) => x.ownership || '-'},
          {header: 'Blockers', accessorFn: (x: any) => <KeyValueChips values={x.blockers || []}/>},
        ] as any}/>
      </SectionBox>
      <Typography variant="caption" color="text.secondary">
        Scope 是配置边界，不代表所有 provider 当前健康。健康证据在 Storage 与 Timeline 中分别展示。
      </Typography>
    </>}

    {tab === 1 && <>
      {(claimError || snapshotError || driverError) && <Alert severity="warning">
        部分 Kubernetes 存储元数据不可见：{String(claimError || snapshotError || driverError)}。权限错误不会被解释为空数据。
      </Alert>}
      <SectionBox title={`Storage backends (${backendRows.length})`}>
        <Table data={backendRows} columns={[
          {header: 'Backend', accessorKey: 'name'},
          {header: 'Type', accessorKey: 'type'},
          {header: 'StorageClass / driver', accessorFn: (x: any) => x.storageClassName || x.csiDriverName || '-'},
          {header: 'Control plane', accessorFn: (x: any) => <StatusLabel status={x.driverState === 'Registered' ? 'success' : x.driverState === 'Reconstructable' ? 'warning' : 'error'}>{x.driverState}</StatusLabel>},
          {header: 'Durability', accessorFn: (x: any) => <StatusLabel status={x.durability === 'Replicated' || x.durability === 'Remote' ? 'success' : 'warning'}>{x.durability || 'Unknown'}</StatusLabel>},
          {header: 'PVCs', accessorFn: (x: any) => x.matchingClaims.length},
          {header: 'Snapshots', accessorFn: (x: any) => x.relatedSnapshots.length},
          {header: 'Capabilities', accessorFn: (x: any) => <KeyValueChips values={x.capabilities || []}/>},
        ] as any}/>
      </SectionBox>
      <SectionBox title={`Visible Runtime Anatomy PVCs (${claimObjects.filter((x: any) => x.metadata?.labels?.['app.kubernetes.io/part-of'] === 'runtime-anatomy').length})`}>
        <Table data={claimObjects.filter((x: any) => x.metadata?.labels?.['app.kubernetes.io/part-of'] === 'runtime-anatomy')} columns={[
          {header: 'Namespace / PVC', accessorFn: (x: any) => `${x.metadata?.namespace}/${x.metadata?.name}`},
          {header: 'State class', accessorFn: (x: any) => x.metadata?.labels?.['anatomy.re8ch.com/state-class'] || '-'},
          {header: 'StorageClass', accessorFn: (x: any) => x.spec?.storageClassName || '-'},
          {header: 'Capacity', accessorFn: (x: any) => x.status?.capacity?.storage || x.spec?.resources?.requests?.storage || '-'},
          {header: 'Phase', accessorFn: (x: any) => <StatusLabel status={x.status?.phase === 'Bound' ? 'success' : 'warning'}>{x.status?.phase || 'Unknown'}</StatusLabel>},
        ] as any}/>
      </SectionBox>
    </>}

    {tab === 2 && <SectionBox title={`State classes (${(spec.stateClasses || []).length})`}>
      <Table data={spec.stateClasses || []} columns={[
        {header: 'State class', accessorKey: 'name'},
        {header: 'Component', accessorKey: 'componentKind'},
        {header: 'Backend', accessorKey: 'backendRef'},
        {header: 'Required', accessorFn: (x: any) => x.required ? 'Yes' : 'No'},
        {header: 'Default size', accessorFn: (x: any) => x.defaultSize || '-'},
        {header: 'Capture', accessorFn: (x: any) => x.capturePolicy || '-'},
        {header: 'Writer', accessorFn: (x: any) => x.exclusiveWriter ? 'Exclusive + fenced' : 'Backend policy'},
      ] as any}/>
    </SectionBox>}

    {tab === 3 && <>
      <SectionBox title={`Images (${(spec.images?.catalog || []).length})`}>
        <Table data={spec.images?.catalog || []} columns={[
          {header: 'Image', accessorKey: 'name'},
          {header: 'Role', accessorKey: 'role'},
          {header: 'Repository', accessorFn: (x: any) => (x.reference || '').split('@')[0]},
          {header: 'Digest', accessorFn: (x: any) => shortDigest(x.reference)},
          {header: 'Immutable', accessorFn: (x: any) => <StatusLabel status={(x.reference || '').includes('@sha256:') ? 'success' : 'error'}>{(x.reference || '').includes('@sha256:') ? 'Digest pinned' : 'Mutable'}</StatusLabel>},
        ] as any}/>
      </SectionBox>
      <Alert severity={spec.images?.policy?.requireDigest ? 'success' : 'warning'}>
        Registry authority: {spec.images?.policy?.registryAuthority || '-'} · signatures {spec.images?.policy?.recordSignature ? 'recorded' : 'not recorded'} · SBOM {spec.images?.policy?.recordSbom ? 'recorded' : 'not recorded'}
      </Alert>
    </>}

    {tab === 4 && <>
      {timelineUnavailable && <Alert severity="info">
        Runtime Anatomy alpha CRDs/controller 尚未安装或当前身份无读取权限：{String(timelineUnavailable)}
      </Alert>}
      <SectionBox title={`Runtime commits (${(commits || []).length})`}>
        <Table data={(commits || []).map(raw)} columns={[
          {header: 'Commit', accessorFn: (x: any) => x.metadata?.name || '-'},
          {header: 'Runtime', accessorFn: (x: any) => x.spec?.runtimeRef?.name || '-'},
          {header: 'Digest', accessorFn: (x: any) => shortDigest(x.status?.manifest?.digest)},
          {header: 'Parent(s)', accessorFn: (x: any) => <KeyValueChips values={(x.spec?.parents || []).map(shortDigest)}/>},
          {header: 'Consistency', accessorFn: (x: any) => x.status?.achievedConsistency || x.spec?.consistency || '-'},
          {header: 'Phase', accessorFn: (x: any) => <StatusLabel status={phaseColor(x.status?.phase)}>{x.status?.phase || 'Pending'}</StatusLabel>},
          {header: 'Window', accessorFn: (x: any) => x.status?.captureWindow?.completedAt || '-'},
        ] as any}/>
      </SectionBox>
      <SectionBox title={`Branches (${(branches || []).length}) · Runtimes ${(runtimes || []).length} · Checkouts ${(checkouts || []).length}`}>
        <Table data={(branches || []).map(raw)} columns={[
          {header: 'Branch', accessorFn: (x: any) => x.metadata?.name || '-'},
          {header: 'Runtime', accessorFn: (x: any) => x.spec?.runtimeRef?.name || '-'},
          {header: 'Head', accessorFn: (x: any) => shortDigest(x.status?.head || x.spec?.head)},
          {header: 'Updated', accessorFn: (x: any) => x.status?.updatedAt || '-'},
        ] as any}/>
      </SectionBox>
      <Typography variant="caption" color="text.secondary">
        v0.1.0 不提供 commit、checkout、restore、branch move 或 GC 按钮；写操作必须经过 planner、独立 RBAC 与确认界面。
      </Typography>
    </>}
  </Box>;
}

registerSidebarEntry({name: 'runtime-anatomy', url: '/runtime-anatomy', icon: 'mdi:timeline-clock-outline', parent: '', label: 'Runtime Anatomy'});
registerRoute({path: '/runtime-anatomy', sidebar: 'runtime-anatomy', name: 'Runtime Anatomy', component: () => <Dashboard/>});
