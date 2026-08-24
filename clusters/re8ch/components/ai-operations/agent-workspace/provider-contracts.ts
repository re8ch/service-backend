export type Capability = {
  id: string;
  audience: "terminal" | "browser";
  subject: string;
  deviceRef: string;
  leaseRef: string;
  operations: string[];
  expiresAt: string;
};

export interface TerminalProvider {
  ensure(device: TerminalDeviceSpec): Promise<TerminalRuntime>;
  exec(runtime: TerminalRuntime, command: string[], capability: Capability): Promise<ExecResult>;
  pty(runtime: TerminalRuntime, capability: Capability): Promise<{ websocketUrl: string }>;
  releaseActivity(runtime: TerminalRuntime): Promise<void>;
  recreate(device: TerminalDeviceSpec, durableStateRef: string): Promise<TerminalRuntime>;
  inspect(runtime: TerminalRuntime): Promise<{ phase: "Active" | "Standby" | "Failed" }>;
}

export interface BrowserProvider {
  acquire(deviceRef: string, profileRef: string, capability: Capability): Promise<BrowserSession>;
  endpoint(session: BrowserSession, capability: Capability): Promise<{ cdpUrl?: string; playwrightUrl?: string; liveViewUrl?: string }>;
  release(session: BrowserSession): Promise<void>;
  reattach(profileRef: string, capability: Capability): Promise<BrowserSession>;
}

export interface DurableStateProvider {
  hydrate(stateRef: string, runtimeRef: string, expectedRevision: number): Promise<{ revision: number }>;
  checkpoint(runtimeRef: string, stateRef: string, baseRevision: number): Promise<{ revision: number }>;
  fence(stateRef: string, holderRef: string): Promise<void>;
}

export interface ConnectivityProvider {
  endpoint(sourceRef: string, targetServiceRef: string, capability: Capability): Promise<{ url: string; transport: "private" | "gateway" }>;
  reachable(sourceRef: string, endpoint: string): Promise<boolean>;
}

export type TerminalDeviceSpec = { name: string; image: string; memoryMiB: number; region: string; durableStateRef: string; credentialRefs: string[] };
export type TerminalRuntime = { provider: string; instanceRef: string; volumeRef?: string };
export type BrowserSession = { provider: string; sessionRef: string; profileRef: string; workerRef?: string };
export type ExecResult = { operationId: string; exitCode: number; stdout: string; stderr: string };

// CubeSandbox adapter semantics:
// - CubeAPI/CubeMaster/Cubelet own microVM creation, scheduling and snapshots.
// - lifecycle methods only translate the Cloud Platform contract; they must not
//   reproduce Cube scheduling, isolation or snapshot logic.
// - CephFS owns durable workspace state. Prefer a direct mount after the
//   K3s/Cilium/Ceph compatibility gate; otherwise use revisioned checkpointing.
// - OpenSandbox BatchSandbox remains the Kubernetes workload path. CubeSandbox
//   uses a thin adapter until it is an upstream OpenSandbox provider.
