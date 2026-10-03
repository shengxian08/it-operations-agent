"""Read-only graph probes: controlled dependencies, no database or model API.

Run with backend on PYTHONPATH. These probes expose conversation gaps; they do
not certify production ticket writes, permissions, or operator receipt.
"""
import argparse
import asyncio
import json
from pathlib import Path

from app.agent.graph import GraphDependencies, build_graph
from app.schemas import TicketStatusResult


class EmptyKnowledge:
    async def retrieve(self, *args, **kwargs):
        return []


class UnusedModel:
    async def complete(self, prompt):
        raise AssertionError("audit probe must not call a model")


class ProbeTickets:
    def __init__(self):
        self.lookups, self.drafts = [], []

    async def get_ticket_status(self, user_id, ticket_number):
        self.lookups.append({"user_id": user_id, "ticket_number": ticket_number})
        return TicketStatusResult(found=False, latest_update="受控探针：无业务数据库调用。")

    async def issue_confirmation_token(self, conversation_id, draft, **kwargs):
        self.drafts.append(draft.model_dump(mode="json"))
        return "probe-only-no-business-write"


async def inspect_paths():
    cases = [
        ("QUERY-001", "查询我上次那个工单的进度", [
            {"role": "user", "content": "查询工单 IT-2026-0001"},
            {"role": "assistant", "content": "工单 IT-2026-0001 正在处理"}], None),
        ("QUERY-001-CONTROLLED", "查询我上次那个工单的进度", [],
            {"user_id": "audit-user", "conversation_id": "audit-conversation", "candidates": ["IT-2026-0001"],
             "pending_candidates": None, "recent_lookup": True, "overflow": False}),
        ("QUERY-001-AMBIGUOUS", "查询上次那个工单进度", [],
            {"user_id": "audit-user", "conversation_id": "audit-conversation", "candidates": ["IT-2026-0001", "IT-2026-0002"],
             "pending_candidates": None, "recent_lookup": True, "overflow": False}),
        ("KNOWLEDGE-001", "VPN如何重新连接", [], None),
        ("CREATE-001", "创建工单", [], None),
        ("HANDOFF-002", "请转人工", [], None),
    ]
    findings = []
    for identifier, message, history, context in cases:
        tickets = ProbeTickets()
        graph = build_graph(GraphDependencies(EmptyKnowledge(), UnusedModel(), tickets))
        state = {"user_id": "audit-user", "conversation_id": "audit-conversation",
                 "message": message, "history": history, "step_count": 0}
        if context is not None:
            state["ticket_context"] = context
        result = await graph.ainvoke(state)
        findings.append({"id": identifier, "message": message, "history": history,
            "actual_final_state": result["final_state"], "handoff_reason": result.get("handoff_reason"),
            "controlled_context": context, "ticket_lookup": result.get("ticket_lookup"),
            "ticket_intake": result.get("ticket_intake"),
            "answer": result["answer"], "lookups": tickets.lookups, "drafts": tickets.drafts})
    return {"kind": "deterministic_graph_audit_no_business_writes", "findings": findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional local JSON evidence file")
    args = parser.parse_args()
    report = asyncio.run(inspect_paths())
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
