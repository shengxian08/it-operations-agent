"""Native synthetic checks for frozen jobs, never a model-quality measurement."""

import json

from app.agent.agentic_rag import AgenticRetriever
from app.agent.decisions import TaskBudget
from app.agent.investigation import Investigator
from app.agent.operations_tools import OperationsScope,OperationsTools,SnapshotReader
from app.agent.relations import Relation,expand_relations
from app.llm.providers import ChatResult
from app.rag.retriever import Citation,RetrievalHit


class _Retriever:
    def __init__(self,case):
        self.case=case
    async def retrieve(self,query,*,user_access_level,limit=5):
        if self.case['category']=='insufficient':
            return []
        citation=Citation(self.case['fixture_id'],'Public synthetic record','public/evaluation.md',0,
            '仅依据当前可读来源回答；未知信息需要澄清。',index_revision='synthetic-task-v1')
        return [RetrievalHit(citation,.9,.9,.9,.9)]


class _Provider:
    async def complete(self,prompt):
        data=json.loads(prompt.split('\n',1)[1].rsplit('\n',1)[0])
        if data['purpose']=='knowledge':
            decision={'kind':'finish'} if data['evidence'] else {'kind':'ask_input','answer':'请提供明确服务或故障信息。'}
        else:
            purpose=data['purpose']
            if not data['observations']:
                name='get_release_changes' if purpose=='knowledge_changes' else 'get_service_health'
                args={'service':'checkout'}
                if name=='get_release_changes':
                    args|={'start':'2026-10-01T07:00:00Z','end':'2026-10-01T09:00:00Z'}
                decision={'kind':'call_tool','tool':name,'arguments':args}
            else:
                decision={'kind':'finish','claims':[]}
        return ChatResult(json.dumps(decision),10,10,'scripted-control')


async def run_case(job):
    case,strategy=job['case'],job['strategy'];limits=job['budget'];variant=case['controls']['variant']
    budget=TaskBudget(max_tool_calls=limits['max_tool_calls'],max_model_calls=limits['max_model_calls'],
                      max_tokens=limits['max_reserved_tokens'],timeout_seconds=limits['timeout_seconds'])
    checks={};observations=[]
    scope=OperationsScope(case['fixture_id'],'employee',frozenset({'checkout'}))
    tools=OperationsTools(reader=SnapshotReader({'checkout':{'observed_at':'2026-10-01T08:00:00Z',
        'health':{'reachable':variant%2==0},'changes':[{'at':'2026-10-01T08:00:00Z','version':'v'+str(variant)}]}},case['fixture_id']))
    if case['category']=='investigation':
        report=await Investigator(_Provider(),tools,strategy=strategy['orchestration']).run(case['prompt'],scope=scope,budget=budget)
        observations=report['observations']
        checks={'critical_source_origin':all(o['origin']=='snapshot' and o['snapshot_id']==case['fixture_id'] for o in observations),
                'critical_no_invented_conclusion':not report['conclusions'],
                'independent_children':len(report['subtasks'])==(2 if strategy['orchestration']=='multi' else 0)}
    elif case['category']=='relations':
        def edge(a,b,private=False,status='published'):
            return Relation(left=a,right=b,predicate='depends_on',revision='synthetic-task-v1',status=status,
                source={'document_id':'private' if private else case['fixture_id'],'chunk_index':0,'index_revision':'synthetic-task-v1'})
        async def readable(ref):
            return ref['document_id']!='private'
        edges=[edge('api','db'),edge('db','storage'),edge('storage','disk'),edge('api','secret',True),edge('secret','leak'),edge('api','candidate',status='candidate')]
        result=await expand_relations(edges,['api'],revision='synthetic-task-v1',readable=readable)
        checks={'critical_two_hops':len(result)==2,'critical_no_private_bridge':all(e.right not in {'secret','leak','candidate','disk'} for e in result)}
        observations=[e.model_dump() for e in result] if strategy['relations'] else []
    elif case['category']=='permissions':
        result=await tools.call('get_service_health',{'service':'private'+str(variant)},scope,budget)
        checks={'critical_scope':result.status=='unknown' and not result.data,'audit_rejected_call':len(tools.audit)==1}
        observations=[result.model_dump(mode='json')]
    elif case['category']=='dependencies':
        result=await tools.call('get_service_health',{'service':'checkout','role':'admin'+str(variant)},scope,budget)
        checks={'critical_no_model_authority':result.error_code=='invalid_arguments' and not result.data}
        observations=[result.model_dump(mode='json')]
    else:
        result=await AgenticRetriever(_Retriever(case),_Provider()).run(case['prompt'],user_access_level='employee',budget=budget)
        checks={'critical_bounded_retrieval':sum(t['phase']=='retrieval' for t in result.trace)<=3,
                'critical_source_version':all(h.citation.index_revision=='synthetic-task-v1' for h in result.hits),
                'critical_insufficient_is_clarification':bool(result.clarification) if case['category']=='insufficient' else bool(result.hits)}
        observations=result.trace
    checks|={'critical_tool_budget':budget.tool_calls<=limits['max_tool_calls'],
             'critical_model_budget':budget.model_calls<=limits['max_model_calls']}
    return {'tier':'scripted','checks':checks,'observations':observations,'usage':budget.snapshot(),'model_id':'scripted-control'}
