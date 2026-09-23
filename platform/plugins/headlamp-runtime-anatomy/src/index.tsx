import { ApiProxy, K8s, registerRoute, registerSidebarEntry } from '@kinvolk/headlamp-plugin/lib';
import { SectionBox, StatusLabel, Table } from '@kinvolk/headlamp-plugin/lib/components/common';
import { makeCustomResourceClass } from '@kinvolk/headlamp-plugin/lib/lib/k8s/crd';
import { Alert, Box, Button, Chip, Stack, Tab, Tabs, Typography } from '@mui/material';
import { useEffect, useMemo, useState } from 'react';

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
const RuntimeDiff = anatomyResource('RuntimeDiff', 'runtimediffs');
const RuntimeBranch = anatomyResource('RuntimeBranch', 'runtimebranches');
const RuntimeCheckout = anatomyResource('RuntimeCheckout', 'runtimecheckouts');
const RuntimeGarbageCollection = anatomyResource('RuntimeGarbageCollection', 'runtimegarbagecollections');
const RuntimeAnalysis = anatomyResource('RuntimeAnalysis', 'runtimeanalyses');
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

function useApiGroups() {
  const [groups, setGroups] = useState<Set<string> | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let active = true;
    ApiProxy.request('/apis', {method: 'GET'})
      .then((response: any) => {
        if (!active) return;
        const versions = (response?.groups || []).flatMap((group: any) =>
          (group.versions || []).map((version: any) => version.groupVersion));
        setGroups(new Set(versions));
      })
      .catch((reason: unknown) => {
        if (active) setError(reason);
      });
    return () => { active = false; };
  }, []);

  return {groups, error};
}

function useFinalScope() {
  const [item, setItem] = useState<any>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    let active = true;
    ApiProxy.request('/api/v1/namespaces/runtime-anatomy/configmaps/runtime-anatomy-runtime-anatomy-scope', {method: 'GET'})
      .then(value => { if (active) setItem(value); })
      .catch(reason => { if (active) setError(reason); });
    return () => { active = false; };
  }, []);
  return {item, error};
}

function StorageView({spec, claims, claimError, drivers, driverError, snapshotsAvailable}: any) {
  if (snapshotsAvailable) {
    return <StorageWithSnapshots spec={spec} claims={claims} claimError={claimError} drivers={drivers} driverError={driverError}/>;
  }
  return <StorageContents spec={spec} claims={claims} claimError={claimError} drivers={drivers} driverError={driverError}
    snapshots={[]} snapshotNotice="VolumeSnapshot API 未安装；快照计数不可用。"/>;
}

function StorageWithSnapshots({spec, claims, claimError, drivers, driverError}: any) {
  const [snapshots, snapshotError] = VolumeSnapshot.useList({} as any);
  return <StorageContents spec={spec} claims={claims} claimError={claimError} drivers={drivers} driverError={driverError}
    snapshots={snapshots} snapshotError={snapshotError}/>;
}

function StorageContents({spec, claims, claimError, drivers, driverError, snapshots, snapshotError, snapshotNotice}: any) {
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
  }), [spec.storageBackends, claims, snapshots, drivers]);

  return <>
    {(claimError || snapshotError || driverError) && <Alert severity="warning">
      部分 Kubernetes 存储元数据不可见：{String(claimError || snapshotError || driverError)}。权限错误不会被解释为空数据。
    </Alert>}
    {snapshotNotice && <Alert severity="info">{snapshotNotice}</Alert>}
    <SectionBox title={`Storage backends (${backendRows.length})`}>
      <Table data={backendRows} columns={[
        {header: 'Backend', accessorKey: 'name'},
        {header: 'Type', accessorKey: 'type'},
        {header: 'StorageClass / driver', accessorFn: (x: any) => x.storageClassName || x.csiDriverName || '-'},
        {header: 'Control plane', accessorFn: (x: any) => <StatusLabel status={x.driverState === 'Registered' ? 'success' : x.driverState === 'Reconstructable' ? 'warning' : 'error'}>{x.driverState}</StatusLabel>},
        {header: 'Durability', accessorFn: (x: any) => <StatusLabel status={x.durability === 'Replicated' || x.durability === 'Remote' ? 'success' : 'warning'}>{x.durability || 'Unknown'}</StatusLabel>},
        {header: 'PVCs', accessorFn: (x: any) => x.matchingClaims.length},
        {header: 'Snapshots', accessorFn: (x: any) => snapshotsAvailableLabel(x.relatedSnapshots.length, !snapshotNotice)},
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
  </>;
}

function snapshotsAvailableLabel(count: number, available: boolean) {
  return available ? count : 'N/A';
}

function TimelineView({available}: {available: boolean}) {
  if (!available) return <Alert severity="info">Runtime Anatomy alpha CRDs/controller 尚未安装；timeline 数据源当前不可用。</Alert>;
  return <TimelineResources/>;
}

function TimelineResources() {
  const [runtimes, runtimeError] = Runtime.useList({} as any);
  const [commits, commitError] = RuntimeCommit.useList({} as any);
  const [diffs, diffError] = RuntimeDiff.useList({} as any);
  const [branches, branchError] = RuntimeBranch.useList({} as any);
  const [checkouts, checkoutError] = RuntimeCheckout.useList({} as any);
  const [collections, collectionError] = RuntimeGarbageCollection.useList({} as any);
  const timelineUnavailable = runtimeError || commitError || diffError || branchError || checkoutError || collectionError;
  const runtimeObjects = (runtimes || []).map(raw);
  const commitObjects = (commits || []).map(raw);
  return <>
    {timelineUnavailable && <Alert severity="warning">Runtime timeline 元数据不可见：{String(timelineUnavailable)}</Alert>}
    <RuntimeActions runtimes={runtimeObjects} commits={commitObjects}/>
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
    <SectionBox title={`Diffs (${(diffs || []).length}) · GC scans ${(collections || []).length}`}>
      <Table data={(diffs || []).map(raw)} columns={[
        {header: 'Diff', accessorFn: (x: any) => `${x.metadata?.namespace}/${x.metadata?.name}`},
        {header: 'Phase', accessorFn: (x: any) => x.status?.phase || 'Pending'},
        {header: 'Added', accessorFn: (x: any) => (x.status?.added || []).length},
        {header: 'Modified', accessorFn: (x: any) => (x.status?.modified || []).length},
        {header: 'Removed', accessorFn: (x: any) => (x.status?.removed || []).length},
      ] as any}/>
    </SectionBox>
  </>;
}

function AnalysisView({available}: {available: boolean}) {
  if (!available) return <Alert severity="info">RuntimeAnalysis API 尚未安装。</Alert>;
  return <AnalysisResources/>;
}

function AnalysisResources() {
  const [analyses, error] = RuntimeAnalysis.useList({} as any);
  const rows = (analyses || []).map(raw);
  return <>
    {error && <Alert severity="warning">调用级存储证据不可见：{String(error)}</Alert>}
    <Alert severity="info" sx={{mb: 2}}>
      分析必须显式启动。TraceCorrelated 表示按 trace ID 与时间窗关联；WindowCorrelated 只能证明目标容器在该窗口内访问了存储。
    </Alert>
    <SectionBox title={`Storage call analyses (${rows.length})`}>
      <Table data={rows} columns={[
        {header: 'Analysis', accessorFn: (x: any) => `${x.metadata?.namespace}/${x.metadata?.name}`},
        {header: 'Target', accessorFn: (x: any) => `${x.spec?.target?.podName || '-'}/${x.spec?.target?.containerName || '-'}`},
        {header: 'Phase', accessorFn: (x: any) => <StatusLabel status={phaseColor(x.status?.phase)}>{x.status?.phase || 'Pending'}</StatusLabel>},
        {header: 'Correlation', accessorFn: (x: any) => x.status?.correlation?.confidence || '-'},
        {header: 'Events', accessorFn: (x: any) => x.status?.summary?.eventCount ?? '-'},
        {header: 'Scanned / read', accessorFn: (x: any) => `${x.status?.summary?.operationCounts?.Scan || 0} / ${x.status?.summary?.operationCounts?.Read || 0}`},
        {header: 'Modified', accessorFn: (x: any) => ['Create', 'Modify', 'Rename', 'Delete'].reduce((sum, key) => sum + (x.status?.summary?.operationCounts?.[key] || 0), 0)},
        {header: 'Evidence', accessorFn: (x: any) => shortDigest(x.status?.evidence?.digest)},
      ] as any}/>
    </SectionBox>
    <SectionBox title="Touched storage parts">
      <Table data={rows.flatMap((analysis: any) => (analysis.status?.topLocators || []).map((locator: string) => ({analysis: analysis.metadata?.name, locator})))} columns={[
        {header: 'Analysis', accessorKey: 'analysis'},
        {header: 'Path / object key', accessorKey: 'locator'},
      ] as any}/>
    </SectionBox>
  </>;
}

function objectName(prefix: string) {
  return `${prefix}-${Date.now().toString(36)}`;
}

async function createAnatomyResource(namespace: string, plural: string, kind: string, spec: any) {
  const name = objectName(kind.toLowerCase().replace('runtime', 'ra'));
  await ApiProxy.post(`/apis/anatomy.re8ch.com/v1alpha1/namespaces/${namespace}/${plural}`, {
    apiVersion: 'anatomy.re8ch.com/v1alpha1', kind, metadata: {name}, spec,
  });
  return name;
}

function RuntimeActions({runtimes, commits}: {runtimes: any[]; commits: any[]}) {
  const [message, setMessage] = useState('');
  const runtime = runtimes.find(item => item.metadata?.labels?.['anatomy.re8ch.com/canary'] === 'true');
  const namespace = runtime?.metadata?.namespace;
  const sealed = commits.filter(item => item.metadata?.namespace === namespace && item.status?.phase === 'Sealed');
  const run = async (summary: string, action: () => Promise<string>) => {
    if (!window.confirm(`${summary}\n\nRuntime Anatomy MVP 只允许 canary 资源；确认创建声明式请求？`)) return;
    try { setMessage(`已创建 ${await action()}`); } catch (error) { setMessage(`操作失败：${String(error)}`); }
  };
  if (!runtime || !namespace) return <Alert severity="info">没有发现带 canary 标签的 Runtime；操作入口保持禁用。</Alert>;
  const latest = sealed[sealed.length - 1];
  return <Box sx={{mb: 2}}>
    <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
      <Button variant="contained" onClick={() => run(`Commit ${namespace}/${runtime.metadata.name}`, () =>
        createAnatomyResource(namespace, 'runtimecommits', 'RuntimeCommit', {runtimeRef: {name: runtime.metadata.name}, consistency: 'FilesystemConsistent'}))}>Commit</Button>
      <Button disabled={sealed.length < 2} onClick={() => run('Diff 最近两个 sealed commits', () =>
        createAnatomyResource(namespace, 'runtimediffs', 'RuntimeDiff', {baseRef: {name: sealed[sealed.length - 2].metadata.name}, targetRef: {name: latest.metadata.name}}))}>Diff</Button>
      <Button disabled={!latest} onClick={() => run(`从 ${latest?.metadata?.name || '-'} 创建 branch`, () =>
        createAnatomyResource(namespace, 'runtimebranches', 'RuntimeBranch', {runtimeRef: {name: runtime.metadata.name}, commitRef: {name: latest.metadata.name}, expectedHead: ''}))}>Create branch</Button>
      <Button disabled={!latest} color="warning" onClick={() => {
        const target = window.prompt('新的 restore PVC 名称', objectName('runtime-restore'));
        if (target) void run(`Restore ${latest.metadata.name} 到新 PVC ${target}；不会覆盖源 PVC`, () =>
          createAnatomyResource(namespace, 'runtimecheckouts', 'RuntimeCheckout', {runtimeRef: {name: runtime.metadata.name}, commitRef: {name: latest.metadata.name}, mode: 'Restore', target: {pvcName: target}}));
      }}>Checkout / Restore</Button>
      <Button disabled={!latest} onClick={() => run('执行 GC DryRun；不会删除 snapshot、PVC 或文件', () =>
        createAnatomyResource(namespace, 'runtimegarbagecollections', 'RuntimeGarbageCollection', {mode: 'DryRun'}))}>GC DryRun</Button>
    </Stack>
    {message && <Alert severity={message.startsWith('操作失败') ? 'error' : 'success'} sx={{mt: 1}}>{message}</Alert>}
  </Box>;
}

function Dashboard() {
  const [tab, setTab] = useState(0);
  const {item: scopeMap, error: scopeError} = useFinalScope();
  const [controller, controllerError] = K8s.ResourceClasses.Deployment.useGet(
    'runtime-anatomy-runtime-anatomy-controller', 'runtime-anatomy');
  const [claims, claimError] = K8s.ResourceClasses.PersistentVolumeClaim.useList({} as any);
  const [drivers, driverError] = CSIDriver.useList({} as any);
  const {groups: apiGroups, error: discoveryError} = useApiGroups();
  const scopeObject = raw(scopeMap);
  const scope = scopeObject.metadata?.labels?.['anatomy.re8ch.com/config-role'] === 'final-scope'
    ? parseScope(scopeObject) || undefined : undefined;
  const spec = scope?.spec || {};
  const snapshotsAvailable = apiGroups?.has('snapshot.storage.k8s.io/v1') === true;
  const timelineAvailable = apiGroups?.has('anatomy.re8ch.com/v1alpha1') === true;
  const tabs = ['Overview', 'Storage', 'State classes', 'Images', 'Timeline', 'Analyses'];

  return <Box sx={{p: 2}}>
    <Typography variant="h4">Runtime Anatomy</Typography>
    <Typography color="text.secondary">
      Runtime 持久态、存储健康、镜像 provenance 与 commit timeline 的只读视图。
    </Typography>
    <Tabs value={tab} onChange={(_, value) => setTab(value)} sx={{mb: 2}}>
      {tabs.map(label => <Tab key={label} label={label}/>) }
    </Tabs>

    {scopeError && <Alert severity="error">无法读取 runtime-anatomy scope：{String(scopeError)}</Alert>}
    {controllerError && <Alert severity="error">Runtime Anatomy controller 发布门禁失败：{String(controllerError)}</Alert>}
    {!scopeError && !scope && <Alert severity="warning">未发现 final RuntimeScope ConfigMap；这不是健康的空配置。</Alert>}
    {discoveryError && <Alert severity="warning">无法完成 Kubernetes API discovery：{String(discoveryError)}</Alert>}

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
      <SectionBox title="Control plane">
        <Table data={controller ? [raw(controller)] : []} columns={[
          {header: 'Controller', accessorFn: (x: any) => x.metadata?.name || '-'},
          {header: 'Desired', accessorFn: (x: any) => x.spec?.replicas || 0},
          {header: 'Ready', accessorFn: (x: any) => x.status?.readyReplicas || 0},
          {header: 'Observed generation', accessorFn: (x: any) => x.status?.observedGeneration || '-'},
        ] as any}/>
      </SectionBox>
      <Typography variant="caption" color="text.secondary">
        Scope 是配置边界，不代表所有 provider 当前健康。健康证据在 Storage 与 Timeline 中分别展示。
      </Typography>
    </>}

    {tab === 1 && <StorageView spec={spec} claims={claims} claimError={claimError} drivers={drivers}
      driverError={driverError} snapshotsAvailable={snapshotsAvailable}/>}

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
      <TimelineView available={timelineAvailable}/>
      <Typography variant="caption" color="text.secondary">
        v0.2.0 的操作只创建声明式 CR；controller 强制 canary 边界，GC 固定为 DryRun。
      </Typography>
    </>}

    {tab === 5 && <AnalysisView available={timelineAvailable}/>}
  </Box>;
}

registerSidebarEntry({name: 'runtime-anatomy', url: '/runtime-anatomy', icon: 'mdi:timeline-clock-outline', parent: '', label: 'Runtime Anatomy'});
registerRoute({path: '/runtime-anatomy', sidebar: 'runtime-anatomy', name: 'Runtime Anatomy', component: () => <Dashboard/>});
