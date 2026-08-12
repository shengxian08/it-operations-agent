import { describe, expect, it, vi } from "vitest";

import { parseSseStream } from "./api";

function chunkedStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

describe("parseSseStream", () => {
  it("parses supported events split across CRLF chunks", async () => {
    const stream = chunkedStream([
      "event: run_started\r",
      "\ndata: {\"run_id\":\"run-1\",\"message_id\":\"msg-1\",\"trace_id\":\"trace-1\"}\r\n\r",
      "\nevent: final\ndata: {\"run_id\":\"run-1\",\"message_id\":null,\"answer\":\"完成\",\"final_state\":\"answered\",\"trace_id\":\"trace-1\"}\n\n",
    ]);

    const events = [];
    for await (const event of parseSseStream(stream)) events.push(event);

    expect(events).toEqual([
      {
        type: "run_started",
        run_id: "run-1",
        message_id: "msg-1",
        trace_id: "trace-1",
      },
      {
        type: "final",
        run_id: "run-1",
        message_id: null,
        answer: "完成",
        final_state: "answered",
        trace_id: "trace-1",
      },
    ]);
  });

  it("warns and skips unknown events without losing subsequent state", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const stream = chunkedStream([
      "event: made_up\ndata: {\"value\":1}\n\n",
      "event: citations\ndata: {\"citations\":[],\"trace_id\":\"trace-1\"}\n\n",
    ]);

    const events = [];
    for await (const event of parseSseStream(stream)) events.push(event);

    expect(warn).toHaveBeenCalledWith("Ignored unknown SSE event: made_up");
    expect(events).toEqual([{ type: "citations", citations: [], trace_id: "trace-1" }]);
    warn.mockRestore();
  });
});
