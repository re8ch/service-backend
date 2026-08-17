import { SandboxInstance } from "@blaxel/core";
import type { Capability, ExecResult, TerminalDeviceSpec, TerminalProvider, TerminalRuntime } from "./provider-contracts.js";

export class BlaxelTerminalProvider implements TerminalProvider {
  async ensure(device: TerminalDeviceSpec): Promise<TerminalRuntime> {
    const sandbox = await SandboxInstance.createIfNotExists({
      name: device.name,
      image: device.image,
      memory: device.memoryMiB,
      region: device.region,
      labels: { "workspace.re8ch.com/device": device.name },
      volumes: [{ name: device.durableStateRef, mountPath: "/workspace", readOnly: false }],
    });
    return { provider: "blaxel", instanceRef: device.name, volumeRef: device.durableStateRef };
  }

  async exec(runtime: TerminalRuntime, command: string[], capability: Capability): Promise<ExecResult> {
    if (capability.audience !== "terminal" || !capability.operations.includes("process.exec")) throw new Error("capability denied");
    const sandbox = await SandboxInstance.get(runtime.instanceRef);
    const operationId = `blaxel-${crypto.randomUUID()}`;
    const process = await sandbox.process.exec({
      name: operationId,
      command: command.map(shellQuote).join(" "),
      workingDir: "/workspace",
      waitForCompletion: true,
      timeout: 60_000,
    });
    return { operationId, exitCode: process.exitCode ?? 1, stdout: process.logs ?? "", stderr: "" };
  }

  async pty(): Promise<{ websocketUrl: string }> {
    throw new Error("PTY is obtained from the authenticated Blaxel terminal/SDK adapter; never expose a raw provider URL");
  }

  async releaseActivity(_runtime: TerminalRuntime): Promise<void> {
    // Blaxel has no explicit stop/standby SDK call. Closing process/PTY/WebSocket
    // activity allows the managed lifecycle to enter STANDBY automatically.
  }

  async recreate(device: TerminalDeviceSpec, durableStateRef: string): Promise<TerminalRuntime> {
    return this.ensure({ ...device, durableStateRef });
  }

  async inspect(runtime: TerminalRuntime): Promise<{ phase: "Active" | "Standby" | "Failed" }> {
    const sandbox = await SandboxInstance.get(runtime.instanceRef);
    const state = String((sandbox as unknown as { state?: string }).state ?? "");
    return { phase: state === "STANDBY" ? "Standby" : state === "RUNNING" ? "Active" : "Failed" };
  }
}

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'\\''`)}'`;
}
