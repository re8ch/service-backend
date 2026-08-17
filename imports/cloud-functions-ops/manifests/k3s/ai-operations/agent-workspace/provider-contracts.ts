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

// Blaxel adapter semantics:
// - ensure() uses SandboxInstance.createIfNotExists/get.
// - an API/process connection resumes STANDBY automatically; there is no fake start().
// - releaseActivity() closes SDK/WebSocket activity and lets Blaxel enter standby.
// - recreate() attaches the durable Blaxel Volume to a new sandbox. CephFS is
//   synchronized by the control plane; it is not mounted directly by Blaxel.
// CiliumServiceGatewayBGPConnectivity is preferred only after the provider has
// private routing/tunneling that can learn/reach the BGP-advertised VIP.
