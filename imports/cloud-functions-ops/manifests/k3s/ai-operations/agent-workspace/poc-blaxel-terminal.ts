import { BlaxelTerminalProvider } from "./blaxel-terminal-provider.js";
import type { Capability, TerminalDeviceSpec } from "./provider-contracts.js";

for (const name of ["BL_WORKSPACE", "BL_API_KEY", "BLAXEL_VOLUME_NAME"] as const) {
  if (!process.env[name]) throw new Error(`${name} is required; register it through Credential Broker, never commit it`);
}

const device: TerminalDeviceSpec = {
  name: process.env.TERMINAL_DEVICE_NAME ?? "re8ch-terminal-poc",
  image: process.env.BLAXEL_IMAGE ?? "blaxel/base-image:latest",
  memoryMiB: 4096,
  region: process.env.BL_REGION ?? "us-was-1",
  durableStateRef: process.env.BLAXEL_VOLUME_NAME!,
  credentialRefs: [],
};
const capability: Capability = {
  id: crypto.randomUUID(), audience: "terminal", subject: "poc",
  deviceRef: device.name, leaseRef: "poc-lease", operations: ["process.exec"],
  expiresAt: new Date(Date.now() + 60_000).toISOString(),
};

const provider = new BlaxelTerminalProvider();
const started = performance.now();
const runtime = await provider.ensure(device);
const result = await provider.exec(runtime, ["sh", "-lc", "printf terminal-ready; test -d /workspace"], capability);
await provider.releaseActivity(runtime);
console.log(JSON.stringify({ runtime, resumeOrCreateMs: Math.round(performance.now() - started), exitCode: result.exitCode, stdout: result.stdout }));
