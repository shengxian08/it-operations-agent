"""Bounded acceptance workload for an isolated test/staging deployment."""
import argparse
import asyncio
import json
import math
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4
import httpx


def percentile(values, q):
    return sorted(values)[min(len(values)-1,math.ceil(len(values)*q)-1)] if values else None


async def exercise(args):
    base=urlsplit(args.base_url)
    if base.scheme != "https" and not (args.environment == "test" and base.hostname in {"localhost","127.0.0.1"}):
        raise ValueError("Staging requires HTTPS; HTTP is allowed only for a loopback test deployment")
    accounts=json.loads(Path(args.sessions).read_text(encoding="utf-8"))["accounts"]
    queries=json.loads(Path(args.queries).read_text(encoding="utf-8"))
    if len(accounts)<args.concurrency or not queries or any(not isinstance(q,str) or not q.strip() for q in queries):
        raise ValueError("Provide one authenticated account per concurrent dialogue and an approved query list")
    reads,answers,retrievals=[],[],[]
    failures=[]; completed=0; started=time.monotonic(); load_started=None
    clients=[httpx.AsyncClient(base_url=args.base_url,headers={"Cookie":account["cookie"],"X-CSRF-Token":account["csrf_token"],"Origin":args.base_url.rstrip("/")},timeout=150) for account in accounts[:args.concurrency]]
    async def get(client,path):
        tick=time.monotonic(); response=await client.get(path); reads.append(time.monotonic()-tick)
        response.raise_for_status(); return response.json()
    async def dialogue(index,client):
        nonlocal completed
        response=await client.post("/api/v1/conversations",json={"title":"Capacity acceptance"}); response.raise_for_status()
        conversation=response.json()["id"]
        count=0
        while time.monotonic()<end:
            tick=time.monotonic(); key=uuid4().hex
            try:
                async with asyncio.timeout(args.run_timeout):
                    await get(client,"/api/v1/tickets?limit=20")
                    response=await client.post(f"/api/v1/conversations/{conversation}/runs",headers={"Idempotency-Key":key},json={"content":queries[(index+count)%len(queries)],"client_message_id":key})
                    if response.status_code != 202:
                        failures.append({"operation":"submit","status":response.status_code}); await asyncio.sleep(args.think_seconds); continue
                    run_id=response.json()["id"]
                    async with client.stream("GET",f"/api/v1/runs/{run_id}/events") as stream:
                        stream.raise_for_status()
                        async for line in stream.aiter_lines():
                            if line.startswith("data:"):
                                data=json.loads(line[5:])
                                if data.get("node") == "retrieve_evidence" and "latency_ms" in data:
                                    retrievals.append(data["latency_ms"]/1000)
                    result=await get(client,f"/api/v1/runs/{run_id}")
                    if result["status"] != "completed" or result.get("result",{}).get("final_state") != "answered":
                        failures.append({"operation":"run","status":result["status"],"run_id":run_id})
                    else:
                        answers.append(time.monotonic()-tick); completed+=1
            except (httpx.HTTPError,ValueError,KeyError,TimeoutError) as error:
                failures.append({"operation":"transport","error_type":type(error).__name__})
            count+=1
            await asyncio.sleep(args.think_seconds)
    try:
        principals=await asyncio.gather(*(get(client,"/api/v1/me") for client in clients))
        if len({principal["id"] for principal in principals}) != args.concurrency:
            raise ValueError("Concurrent clients must use distinct authenticated accounts")
        docs=0; cursor=None
        while True:
            page=await get(clients[0],"/api/v1/knowledge/documents?limit=100"+(f"&cursor={cursor}" if cursor else ""))
            docs+=len(page["documents"]); cursor=page.get("next_cursor")
            if not cursor: break
        if docs<args.documents:
            raise ValueError("The target does not contain the required accessible knowledge document count")
        load_started=time.monotonic(); end=load_started+args.duration
        await asyncio.gather(*(dialogue(index,client) for index,client in enumerate(clients)))
    finally:
        await asyncio.gather(*(client.aclose() for client in clients))
    actual_load_seconds=time.monotonic()-load_started
    report={"environment":args.environment,"duration_seconds":round(time.monotonic()-started,2),"actual_load_seconds":round(actual_load_seconds,2),"concurrency":args.concurrency,"accessible_documents":docs,"completed_dialogues":completed,"failed_operations":len(failures),
        "read_p95_seconds":percentile(reads,.95),"retrieval_p95_seconds":percentile(retrievals,.95),"answer_p95_seconds":percentile(answers,.95),"failures":failures[:100],"real_model":args.real_model}
    report["capacity_passed"]=bool(actual_load_seconds>=1800 and args.duration>=1800 and args.concurrency==10 and docs>=500 and not failures and completed>=10 and report["read_p95_seconds"] is not None and report["read_p95_seconds"]<.5 and report["retrieval_p95_seconds"] is not None and report["retrieval_p95_seconds"]<2 and report["answer_p95_seconds"] is not None and report["answer_p95_seconds"]<30 and args.real_model)
    output=Path(args.output); output.parent.mkdir(parents=True,exist_ok=True); output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({key:value for key,value in report.items() if key != "failures"}))
    return report["capacity_passed"]


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url",required=True); parser.add_argument("--environment",choices=["test","staging"],required=True)
    parser.add_argument("--sessions",required=True); parser.add_argument("--queries",required=True)
    parser.add_argument("--duration",type=int,default=1800); parser.add_argument("--concurrency",type=int,choices=range(1,11),default=10)
    parser.add_argument("--documents",type=int,default=500); parser.add_argument("--think-seconds",type=float,default=13)
    parser.add_argument("--run-timeout",type=float,default=150,help="Total deadline per submitted dialogue, including SSE heartbeats; use run limit plus recovery margin.")
    parser.add_argument("--real-model",action="store_true"); parser.add_argument("--output",default="artifacts/capacity.json")
    args=parser.parse_args()
    if args.real_model and args.environment != "staging": parser.error("Real model capacity acceptance uses an approved staging configuration")
    if args.duration<=0 or args.think_seconds<0 or args.run_timeout<=0: parser.error("Duration/deadline must be positive and think time nonnegative")
    try:
        passed=asyncio.run(exercise(args))
    except (ValueError,httpx.HTTPError,KeyError) as error:
        parser.exit(1,f"Capacity precondition failed: {type(error).__name__}\n")
    raise SystemExit(0 if passed else 1)
