import importlib
import json
from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_kubernetes_reader_uses_fixed_readonly_targets_and_labels_live_observations(tmp_path):
    module = importlib.import_module('app.agent.kubernetes_reader')
    calls = []
    async def command(*argv):
        calls.append(argv)
        return json.dumps({'spec':{'replicas':2},'status':{'readyReplicas':1}}).encode()
    config=tmp_path/'kubeconfig'
    config.write_text('public fixture')
    reader=module.KubernetesReader(config, 'kind-agent-deepening-test',
        {'checkout':{'namespace':'agent-deepening','deployment':'commerce-api','selector':'app=commerce-api'}},
        command=command)
    observed,data,origin,snapshot=await reader.read('get_service_health',{'service':'checkout'})
    assert origin=='live' and snapshot is None and observed.tzinfo is not None
    assert data['ready_replicas']==1 and data['desired_replicas']==2
    assert data['endpoint_reachability']=='unknown'
    assert 'get' in calls[0] and 'deployment' in calls[0]
    assert '--context' in calls[0] and 'kind-agent-deepening-test' in calls[0]
    with pytest.raises(LookupError):
        await reader.read('get_service_health',{'service':'other'})
    with pytest.raises(LookupError):
        await reader.read('query_metrics',{'service':'checkout','metric':'error_rate',
            'start':datetime.now(timezone.utc),'end':datetime.now(timezone.utc),'limit':10})
    assert len(calls)==1


def test_kubernetes_reader_rejects_unscoped_context_and_shell_like_targets(tmp_path):
    module=importlib.import_module('app.agent.kubernetes_reader')
    config=tmp_path/'kubeconfig';config.write_text('public fixture')
    with pytest.raises(ValueError):
        module.KubernetesReader(config,'production',{})
    with pytest.raises(ValueError):
        module.KubernetesReader(config,'kind-test',{'checkout':{
            'namespace':'test;exec','deployment':'api','selector':'app=api'}})
