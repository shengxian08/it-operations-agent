import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, parseRunEvents, setSession } from "./api";

afterEach(() => { vi.unstubAllGlobals(); setSession(null); });

describe("production transport", () => {
  it("sends cookies and CSRF, preserving an idempotency key on retries", async () => {
    const fetcher = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ id: "run-1", status: "queued" }), { status: 202 }));
    vi.stubGlobal("fetch", fetcher);
    setSession({ id: "employee-1", display_name: "员工", role: "employee", csrf_token: "csrf-1" });
    await api.startRun("conversation-1", "VPN故障", "message-1", "same-key");
    await api.startRun("conversation-1", "VPN故障", "message-1", "same-key");
    for (const [, init] of fetcher.mock.calls) {
      expect(init.credentials).toBe("same-origin");
      expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-1");
      expect(new Headers(init.headers).get("Idempotency-Key")).toBe("same-key");
      expect(JSON.parse(init.body)).toEqual({ content: "VPN故障", client_message_id: "message-1" });
    }
  });

  it("invalidates all authenticated state once a request returns 401", async () => {
    const invalidate = vi.fn();
    window.addEventListener("session-expired", invalidate);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { code: "session_expired", message: "登录已过期", trace_id: "trace-401" } }), { status: 401 })));
    setSession({ id: "employee-1", display_name: "员工", role: "employee", csrf_token: "secret" });
    await expect(api.conversations()).rejects.toMatchObject({ status: 401, code: "session_expired", traceId: "trace-401" });
    expect(invalidate).toHaveBeenCalledOnce();
    window.removeEventListener("session-expired", invalidate);
  });

  it("exposes a structured error for non-JSON failures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("proxy failure", { status: 502, headers: { "X-Trace-Id": "trace-proxy" } })));
    await expect(api.me()).rejects.toBeInstanceOf(ApiError);
    await expect(api.me()).rejects.toMatchObject({ status: 502, traceId: "trace-proxy" });
  });

  it("parses fragmented UTF-8 events with monotonically numbered SSE ids", async () => {
    const bytes = new TextEncoder().encode('id: 3\r\nevent: final\r\ndata: {"answer":"中文回答","run_id":"r","final_state":"answered","message_id":"m","trace_id":"t"}\r\n\r\n');
    const stream = new ReadableStream<Uint8Array>({ start(controller) { for (let i = 0; i < bytes.length; i += 3) controller.enqueue(bytes.slice(i, i + 3)); controller.close(); } });
    const events = [];
    for await (const event of parseRunEvents(stream)) events.push(event);
    expect(events).toEqual([expect.objectContaining({ sequence: 3, type: "final", answer: "中文回答" })]);
  });
});
