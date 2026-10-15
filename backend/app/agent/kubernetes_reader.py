"""Readonly adapter for explicitly configured isolated kind targets.

No exec, arbitrary URL, shell, mutation, or environment discovery is exposed.
Deployment readiness is an observation, not proof of endpoint reachability.
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path


async def run_command(*argv):
    process=await asyncio.create_subprocess_exec(*argv,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:
        out,_=await process.communicate()
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode or len(out)>256000:
        raise LookupError('kubernetes_read_failed')
    return out


class KubernetesReader:
    def __init__(self,kubeconfig,context,targets,*,binary='kubectl',command=run_command):
        self.kubeconfig=Path(kubeconfig).resolve()
        if not self.kubeconfig.is_file() or not re.fullmatch(r'kind-[a-z0-9-]{1,80}',context):
            raise ValueError('An isolated kind kubeconfig/context is required')
        for target in targets.values():
            if set(target)!={'namespace','deployment','selector'} or not all(
                re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,62}',target[field]) for field in ('namespace','deployment')
            ) or not re.fullmatch(r'app=[a-z0-9][a-z0-9.-]{0,62}',target['selector']):
                raise ValueError('Invalid fixed Kubernetes target')
        self.targets,self.command=targets,command
        self.prefix=(binary,'--kubeconfig',str(self.kubeconfig),'--context',context,'--request-timeout=5s')

    async def _json(self,target,*args):
        data=await self.command(*self.prefix,'--namespace',target['namespace'],*args,'-o','json')
        return json.loads(data)

    async def read(self,tool,args):
        target=self.targets.get(args['service'])
        if target is None:
            raise LookupError('unconfigured_resource')
        if tool=='query_metrics' and args['metric']!='availability':
            raise LookupError('metrics_provider_not_configured')
        if tool in {'get_service_health','query_metrics'}:
            deployment=await self._json(target,'get','deployment',target['deployment'])
            now=datetime.now(timezone.utc)
            desired=deployment.get('spec',{}).get('replicas',0)
            ready=deployment.get('status',{}).get('readyReplicas',0)
            if tool=='get_service_health':
                data={'desired_replicas':desired,'ready_replicas':ready,
                      'deployment_available':desired>0 and ready>=desired,
                      'endpoint_reachability':'unknown','namespace':target['namespace']}
            else:
                if not args['start']<=now<=args['end'] or desired<1:
                    raise LookupError('current_metric_outside_window')
                data={'items':[{'at':now.isoformat(),'metric':'availability','value':ready/desired,
                                'unit':'ready_replica_ratio'}],'history_available':False,'truncated':False}
        elif tool=='get_release_changes':
            result=await self._json(target,'get','replicasets','--selector',target['selector'])
            rows=[]
            for replica in result.get('items',[])[:100]:
                meta=replica['metadata']; at=datetime.fromisoformat(meta['creationTimestamp'].replace('Z','+00:00'))
                if args['start']<=at<=args['end']:
                    rows.append({'at':at.isoformat(),'revision':meta.get('annotations',{}).get('deployment.kubernetes.io/revision'),
                        'images':[c['image'] for c in replica['spec']['template']['spec']['containers']],
                        'source':'replicaset_creation'})
            data={'items':rows[:args['limit']],'truncated':len(rows)>args['limit']}
            now=datetime.now(timezone.utc)
        elif tool=='query_error_logs':
            pods=await self._json(target,'get','pods','--selector',target['selector'])
            rows=[]
            for pod in pods.get('items',[])[:3]:
                name=pod['metadata']['name']
                if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}',name):
                    raise LookupError('invalid_pod_name')
                raw=await self.command(*self.prefix,'--namespace',target['namespace'],'logs',name,
                    '--timestamps=true','--tail=100','--since-time='+args['start'].isoformat())
                for line in raw.decode('utf-8').splitlines():
                    stamp,_,message=line.partition(' ')
                    try:
                        at=datetime.fromisoformat(stamp.replace('Z','+00:00'))
                    except ValueError:
                        continue
                    if args['start']<=at<=args['end'] and ('error' in message.lower() or 'exception' in message.lower()):
                        rows.append({'at':at.isoformat(),'message':message[:1000],'pod':name})
            data={'items':rows[:args['limit']],'truncated':len(rows)>args['limit'],
                  'coverage':'last_100_lines_per_pod_max_3_pods'}
            now=datetime.now(timezone.utc)
        else:
            raise LookupError('unsupported_kubernetes_read')
        return now,data,'live',None
