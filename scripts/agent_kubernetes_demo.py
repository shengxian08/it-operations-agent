"""Read actual owned kind health/log/release data with the IT readonly tool adapter."""
import argparse
import asyncio
import json
import sys
from datetime import datetime,timedelta,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))


async def main(args):
    from app.agent.decisions import TaskBudget
    from app.agent.kubernetes_reader import KubernetesReader
    from app.agent.operations_tools import OperationsTools,OperationsScope
    now=datetime.now(timezone.utc)
    reader=KubernetesReader(args.kubeconfig,'kind-agent-deepening-20261003',{
        'checkout':{'namespace':'agent-deepening','deployment':'api','selector':'app=api'}})
    tools=OperationsTools(reader=reader)
    scope=OperationsScope('public-synthetic-reader','employee',frozenset({'checkout'}))
    budget=TaskBudget(max_tool_calls=6)
    window={'service':'checkout','start':(now-timedelta(hours=1)).isoformat(),'end':(now+timedelta(minutes=1)).isoformat(),'limit':10}
    observations=[]
    for name,params in [('get_service_health',{'service':'checkout'}),('query_error_logs',window),
        ('get_release_changes',window),('query_metrics',{**window,'metric':'latency_ms'}),
        ('query_metrics',{**window,'metric':'availability'})]:
        observations.append((await tools.call(name,params,scope,budget)).model_dump(mode='json'))
    assert observations[0]['status']=='ok' and observations[0]['origin']=='live'
    assert observations[0]['data']['deployment_available'] is True
    assert observations[0]['data']['endpoint_reachability']=='unknown'
    assert observations[3]['status']=='unknown'  # No latency provider is configured in this minimal local environment.
    assert observations[4]['origin']=='live' and observations[4]['data']['history_available'] is False
    output=ROOT/'artifacts/agent-deepening/demo/kubernetes-evidence.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({'tier':'real-services','data_classification':'public-synthetic','model_quality':'unverified',
        'observations':observations,'budget':budget.snapshot()},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'readonly_kubernetes':'passed','unsupported_metric':'unknown','endpoint_reachability':'unknown','evidence':str(output)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--kubeconfig',type=Path,required=True)
    asyncio.run(main(parser.parse_args()))
