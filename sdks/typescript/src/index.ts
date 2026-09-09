/**
 * Veridian TypeScript brick SDK.
 *
 * Speaks `veridian/1.0`: JSON-RPC 2.0, one message per NDJSON line, protocol on stdout, logs on
 * stderr. Thin by design — the wire format is defined by the JSON Schemas in `schemas/`, not here.
 *
 * Run a brick with:  node --experimental-strip-types brick.ts
 *
 *   import { serve } from "@veridian/sdk";
 *   await serve({
 *     name: "tools/git",
 *     version: "0.1.0",
 *     implements: { tools: ["list", "invoke"] },
 *     handlers: {
 *       "tools.list": async () => ({ tools: [] }),
 *       "tools.invoke": async (params, ctx) => ({ output: "…" }),
 *     },
 *   });
 */

import * as readline from "node:readline";

export const PROTOCOL_VERSION = "veridian/1.0";

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
export type Params = Record<string, Json>;

// Error codes — mirrors docs/specifications/protocol.md section 8.
export const ErrorCode = {
  ParseError: -32700,
  InvalidRequest: -32600,
  MethodNotFound: -32601,
  InvalidParams: -32602,
  InternalError: -32603,
  PermissionDenied: -32001,
  ContractNotBound: -32002,
  UnsupportedMethod: -32003,
  BrickInternalError: -32004,
  BrickUnavailable: -32005,
  Timeout: -32006,
  InvalidManifest: -32007,
  ProtocolVersionMismatch: -32008,
} as const;

export class BrickError extends Error {
  code: number;
  data?: Params;
  constructor(message: string, code: number = ErrorCode.BrickInternalError, data?: Params) {
    super(message);
    this.code = code;
    this.data = data;
  }
}

type RpcMessage = {
  jsonrpc: "2.0";
  id?: number | string | null;
  method?: string;
  params?: Params;
  result?: Json;
  error?: { code: number; message: string; data?: Params };
};

interface Pending {
  resolve: (value: Json) => void;
  reject: (err: Error) => void;
  timer?: NodeJS.Timeout;
}

class StreamCall {
  private queue: Params[] = [];
  private waiters: ((v: IteratorResult<Params>) => void)[] = [];
  private done = false;
  private finalValue?: Json;
  private finalErr?: Error;
  private resultWaiters: { resolve: (v: Json) => void; reject: (e: Error) => void }[] = [];

  pushDelta(p: Params): void {
    const w = this.waiters.shift();
    if (w) w({ value: p, done: false });
    else this.queue.push(p);
  }
  finishOk(v: Json): void {
    this.finalValue = v;
    this.done = true;
    for (const w of this.waiters) w({ value: undefined as never, done: true });
    this.waiters = [];
    for (const r of this.resultWaiters) r.resolve(v);
    this.resultWaiters = [];
  }
  finishErr(e: Error): void {
    this.finalErr = e;
    this.done = true;
    for (const w of this.waiters) w({ value: undefined as never, done: true });
    this.waiters = [];
    for (const r of this.resultWaiters) r.reject(e);
    this.resultWaiters = [];
  }
  [Symbol.asyncIterator](): AsyncIterator<Params> {
    return {
      next: (): Promise<IteratorResult<Params>> => {
        const q = this.queue.shift();
        if (q) return Promise.resolve({ value: q, done: false });
        if (this.done) return Promise.resolve({ value: undefined as never, done: true });
        return new Promise((resolve) => this.waiters.push(resolve));
      },
    };
  }
  result(): Promise<Json> {
    if (this.finalErr) return Promise.reject(this.finalErr);
    if (this.finalValue !== undefined) return Promise.resolve(this.finalValue);
    return new Promise((resolve, reject) => this.resultWaiters.push({ resolve, reject }));
  }
}

type RequestHandler = (method: string, params: Params) => Promise<Json>;

class Endpoint {
  private nextId = 0;
  private pending = new Map<number | string, Pending>();
  private streams = new Map<number | string, StreamCall>();
  private badRun = 0;
  private malformedThreshold = 5;
  onRequest: RequestHandler | null = null;
  onNotification: ((method: string, params: Params) => void) | null = null;

  start(): void {
    const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
    rl.on("line", (line: string) => this.onLine(line));
    rl.on("close", () => this.failAll(new BrickError("stdin closed", ErrorCode.BrickUnavailable)));
  }

  private write(msg: RpcMessage): void {
    process.stdout.write(JSON.stringify(msg) + "\n");
  }

  private onLine(line: string): void {
    const text = line.trim();
    if (!text) return;
    let msg: RpcMessage;
    try {
      msg = JSON.parse(text);
    } catch {
      if (++this.badRun >= this.malformedThreshold) process.exit(1);
      return;
    }
    if (typeof msg !== "object" || msg === null || msg.jsonrpc !== "2.0") {
      if (++this.badRun >= this.malformedThreshold) process.exit(1);
      return;
    }
    this.badRun = 0;
    this.dispatch(msg);
  }

  private dispatch(msg: RpcMessage): void {
    const hasId = msg.id !== undefined && msg.id !== null;
    const isResponse = "result" in msg || "error" in msg;
    if (isResponse && hasId) {
      const p = this.pending.get(msg.id as number | string);
      if (!p) return;
      this.pending.delete(msg.id as number | string);
      if (p.timer) clearTimeout(p.timer);
      if (msg.error) p.reject(new BrickError(msg.error.message, msg.error.code, msg.error.data));
      else p.resolve(msg.result ?? null);
      return;
    }
    if (msg.method && hasId) {
      void this.handleRequest(msg);
      return;
    }
    if (msg.method) {
      const params = msg.params ?? {};
      const rid = params["request_id"] as number | string | undefined;
      if (rid !== undefined && this.streams.has(rid)) {
        this.streams.get(rid)!.pushDelta(params);
        return;
      }
      if (this.onNotification) this.onNotification(msg.method, params);
    }
  }

  private currentRequestId: number | string | null = null;

  getCurrentRequestId(): number | string | null {
    return this.currentRequestId;
  }

  private async handleRequest(msg: RpcMessage): Promise<void> {
    const id = msg.id as number | string;
    const prev = this.currentRequestId;
    this.currentRequestId = id;
    try {
      if (!this.onRequest) throw new BrickError("no handler", ErrorCode.MethodNotFound);
      const result = await this.onRequest(msg.method as string, msg.params ?? {});
      this.write({ jsonrpc: "2.0", id, result });
    } catch (err) {
      const e = err as BrickError;
      this.write({
        jsonrpc: "2.0",
        id,
        error: { code: e.code ?? ErrorCode.InternalError, message: e.message, data: e.data },
      });
    } finally {
      this.currentRequestId = prev;
    }
  }

  call(method: string, params: Params = {}, timeoutMs = 30000): Promise<Json> {
    const id = ++this.nextId;
    return new Promise<Json>((resolve, reject) => {
      const timer =
        timeoutMs > 0
          ? setTimeout(() => {
              this.pending.delete(id);
              reject(new BrickError(`call ${method} timed out`, ErrorCode.Timeout));
            }, timeoutMs)
          : undefined;
      this.pending.set(id, { resolve, reject, timer });
      this.write({ jsonrpc: "2.0", id, method, params });
    });
  }

  callStream(method: string, params: Params = {}, timeoutMs = 300000): StreamCall {
    const id = ++this.nextId;
    const stream = new StreamCall();
    this.streams.set(id, stream);
    this.pending.set(id, {
      resolve: (v) => {
        this.streams.delete(id);
        stream.finishOk(v);
      },
      reject: (e) => {
        this.streams.delete(id);
        stream.finishErr(e);
      },
      timer:
        timeoutMs > 0
          ? setTimeout(() => {
              this.pending.delete(id);
              this.streams.delete(id);
              stream.finishErr(new BrickError(`stream ${method} timed out`, ErrorCode.Timeout));
            }, timeoutMs)
          : undefined,
    });
    this.write({ jsonrpc: "2.0", id, method, params });
    return stream;
  }

  notify(method: string, params: Params = {}): void {
    this.write({ jsonrpc: "2.0", method, params });
  }

  private failAll(err: Error): void {
    for (const [, p] of this.pending) {
      if (p.timer) clearTimeout(p.timer);
      p.reject(err);
    }
    this.pending.clear();
    for (const [, s] of this.streams) s.finishErr(err);
    this.streams.clear();
  }
}

export class HostProxy {
  private ep: Endpoint;
  constructor(ep: Endpoint) {
    this.ep = ep;
  }
  log(message: string, level: "debug" | "info" | "warning" | "error" = "info", fields?: Params): void {
    const params: Params = { level, message };
    if (fields) params["fields"] = fields;
    this.ep.notify("host.log", params);
  }
  async emit(type: string, payload?: Json): Promise<void> {
    await this.ep.call("host.event.emit", { type, payload: payload ?? null });
  }
  async requestPermission(capability: string, reason?: string): Promise<boolean> {
    const params: Params = { capability };
    if (reason) params["reason"] = reason;
    const res = (await this.ep.call("host.permission.request", params)) as { granted: boolean };
    return res.granted;
  }
  async contractCall(contract: string, method: string, params: Params = {}, timeoutMs = 120000): Promise<Json> {
    const res = (await this.ep.call(
      "host.contract.call",
      { contract, method, params },
      timeoutMs,
    )) as { result: Json };
    return res.result;
  }
}

export interface RequestContext {
  config: Params;
  workspaceRoot: string;
  host: HostProxy;
  emitDelta: (delta: Params) => void;
}

export interface BrickDef {
  name: string;
  version: string;
  implements: Record<string, string[]>;
  handlers: Record<string, (params: Params, ctx: RequestContext) => Promise<Json>>;
  onInitialize?: (init: {
    capabilities: string[];
    workspaceRoot: string;
    config: Params;
  }) => Promise<boolean> | boolean;
  onShutdown?: () => Promise<void> | void;
}

export function serve(def: BrickDef): Promise<void> {
  const ep = new Endpoint();
  const host = new HostProxy(ep);
  let config: Params = {};
  let workspaceRoot = ".";

  return new Promise<void>((resolveServe) => {
    ep.onRequest = async (method: string, params: Params): Promise<Json> => {
      if (method === "plugin.initialize") {
        if (params["protocol_version"] !== PROTOCOL_VERSION) {
          throw new BrickError(
            `${def.name} speaks ${PROTOCOL_VERSION}`,
            ErrorCode.ProtocolVersionMismatch,
          );
        }
        config = (params["config"] as Params) ?? {};
        workspaceRoot = (params["workspace_root"] as string) ?? ".";
        let ready = true;
        if (def.onInitialize) {
          ready = await def.onInitialize({
            capabilities: (params["capabilities"] as string[]) ?? [],
            workspaceRoot,
            config,
          });
        }
        return {
          protocol_version: PROTOCOL_VERSION,
          brick: { name: def.name, version: def.version },
          ready,
        };
      }
      if (method === "plugin.capabilities") {
        return {
          contracts: Object.entries(def.implements).map(([name, methods]) => ({
            name,
            version: "1.0",
            methods,
          })),
        };
      }
      if (method === "plugin.ping") {
        return "nonce" in params ? { nonce: params["nonce"] } : {};
      }
      if (method === "plugin.shutdown") {
        if (def.onShutdown) await def.onShutdown();
        setTimeout(() => {
          resolveServe();
          process.exit(0);
        }, 10);
        return {};
      }

      const handler = def.handlers[method];
      if (!handler) {
        throw new BrickError(`${def.name} does not implement ${method}`, ErrorCode.UnsupportedMethod);
      }
      const contract = method.split(".")[0]!;
      const rid = ep.getCurrentRequestId();
      const ctx: RequestContext = {
        config,
        workspaceRoot,
        host,
        emitDelta: (delta: Params) => {
          ep.notify(`${contract}.delta`, { request_id: rid, ...delta });
        },
      };
      try {
        return await handler(params, ctx);
      } catch (err) {
        if (err instanceof BrickError) throw err;
        const e = err as Error;
        throw new BrickError(`${e.name}: ${e.message}`);
      }
    };
    ep.start();
  });
}
