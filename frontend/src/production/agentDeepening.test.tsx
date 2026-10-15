import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { applyRunEvent, emptyRunView } from "./runState";
import { ConversationWorkspace } from "./ConversationWorkspace";

afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); });
it("includes investigation mode without altering legacy request bodies", async () => {
  const fetcher=vi.fn().mockResolvedValue(new Response('{}',{status:202}));
  vi.stubGlobal("fetch",fetcher);
  await api.startRun("c","调查checkout","m","k","investigate");
  expect(JSON.parse(fetcher.mock.calls[0][1].body).task_mode).toBe("investigate");
});
it("projects progress once and removes inaccessible evidence on replay", () => {
  const event={type:"agent_progress" as const,sequence:2,trace_id:"t",agent:"logs_metrics",stage:"observing",observation:{id:"o",tool:"get_service_health",status:"ok" as const,origin:"snapshot" as const,observed_at:"2026-10-01T08:00:00Z",data:{reachable:false}}};
  const first=applyRunEvent(emptyRunView("r"),event);
  expect(first.observations).toHaveLength(1);
  expect(applyRunEvent(first,event)).toBe(first);
});
it("retains task mode across unknown acceptance and a retry", async () => {
  const attempts:Array<Record<string,unknown>>=[];
  const fetcher=vi.fn(async(url:string,init?:RequestInit)=>{
    const reply=(data:unknown,status=200)=>new Response(JSON.stringify(data),{status});
    if(init?.method==="POST" && url.endsWith("/runs")) {
      attempts.push(JSON.parse(init.body as string));
      if(attempts.length===1) throw new TypeError("network lost");
      return reply({id:"r",status:"completed",result:{answer:"调查完成",message_id:"m",final_state:"investigated"}},202);
    }
    if(url==="/api/v1/conversations") return reply({conversations:[{id:"c",title:"会话",status:"active"}],next_cursor:null});
    if(url.includes("/messages")) return reply({messages:[],next_cursor:null});
    if(url.includes("/ticket-drafts")) return reply({drafts:[]});
    if(url.endsWith("/runs")) return reply({runs:[],next_cursor:null});
    if(url==="/api/v1/runs/r") return reply({id:"r",status:"completed",result:{answer:"调查完成",message_id:"m",final_state:"investigated"}});
    throw new Error(url);
  });
  vi.stubGlobal("fetch",fetcher);
  const user=userEvent.setup();
  render(<ConversationWorkspace principal={{id:"u",display_name:"员工",role:"employee",csrf_token:"c"}} onOpenSource={vi.fn()} />);
  await user.selectOptions(await screen.findByLabelText("任务模式"),"investigate");
  await user.type(screen.getByLabelText("输入 IT 问题"),"调查checkout");
  await user.click(screen.getByRole("button",{name:"发送请求"}));
  await user.click(await screen.findByRole("button",{name:"恢复发送结果并重试"}));
  await waitFor(()=>expect(attempts).toHaveLength(2));
  expect(attempts[0]).toEqual(attempts[1]);
  expect(attempts[0].task_mode).toBe("investigate");
});
