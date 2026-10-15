"""Independent official MCP client: real stdio plus authenticated HTTP and source review."""
import asyncio
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))


async def main():
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    from mcp.client.streamable_http import streamable_http_client
    import httpx
    output=ROOT/'artifacts/agent-deepening/demo'
    result={'tier':'real-services','model_quality':'unverified','data_classification':'public-synthetic'}
    params=StdioServerParameters(command=sys.executable,args=['-m','app.agent.mcp_server','--snapshot',str(ROOT/'backend/app/data/operations-public-snapshot.json')],
        env={'PYTHONPATH':str(ROOT/'backend')},cwd=str(ROOT/'backend'))
    async with Client(params) as client:
        names={t.name for t in (await client.list_tools()).tools}
        assert len(names)==6
        observation=(await client.call_tool('get_service_health',{'params':{'service':'checkout'}})).structured_content
        assert observation['origin']=='snapshot' and observation['data']['reachable'] is False
        result['stdio']={'tools':sorted(names),'observation':observation}
    session=json.loads((output/'session.json').read_text())
    headers={'Cookie':session['cookie_name']+'='+session['cookie'],'Origin':session['origin'],'X-CSRF-Token':session['csrf_token']}
    async with httpx.AsyncClient(base_url=session['origin'],headers=headers,timeout=30) as http:
        assert (await http.post('/agent-tools/mcp',headers={'Cookie':''},json={})).status_code==401
        assert (await http.post('/agent-tools/mcp',headers={'X-CSRF-Token':''},json={})).status_code==403
        # Publish an explicit, reviewed source through the existing durable parser/indexer.
        document='# 公开 checkout 调查资料\n\ncheckout depends_on database\n\n## 故障调查\n发布后连接池配置变化可能导致连接等待，需要日志和指标验证，不能仅据时间先后断定根因。\n'
        upload=await http.post('/api/v1/knowledge/uploads',files={'file':('agent-public.md',document.encode(),'text/markdown')},data={'title':'公开合成调查资料','access_level':'employee'})
        upload.raise_for_status();job=upload.json()
        for _ in range(60):
            parsed=(await http.get('/api/v1/knowledge/jobs/'+job['id'])).json()
            if parsed['status']=='ready':break
            if parsed['status']=='failed':raise AssertionError(parsed)
            await asyncio.sleep(.2)
        assert parsed['status']=='ready'
        publish=await http.post('/api/v1/knowledge/jobs/'+job['id']+'/publish');publish.raise_for_status()
        index=publish.json()
        for _ in range(100):
            indexed=(await http.get('/api/v1/admin/knowledge/index-jobs/'+index['index_job_id'])).json()
            if indexed['status'] in {'succeeded','completed'}:break
            if indexed['status']=='failed':raise AssertionError(indexed)
            await asyncio.sleep(.2)
        assert indexed['status'] in {'succeeded','completed'},indexed
        graphs=(await http.get('/api/v1/agent/graphs')).json()['items']
        graph=next(g for g in graphs if g['knowledge_revision']==indexed['result']['revision_id'])
        preview=(await http.get('/api/v1/agent/graphs/'+graph['graph_version'])).json()
        assert preview['relations'] and preview['relations'][0]['source_available']
        (await http.post('/api/v1/agent/graphs/'+graph['graph_version']+'/publish')).raise_for_status()
        result['source_publication']={'job_id':job['id'],'index':indexed,'graph_preview':preview}
        async with Client(streamable_http_client(session['origin']+'/agent-tools/mcp',http_client=http)) as client:
            tools=(await client.list_tools()).tools
            evidence=(await client.call_tool('search_knowledge',{'params':{'query':'checkout database','limit':5}})).structured_content
            assert len(tools)==6 and evidence['status']=='ok'
            result['http']={'tool_names':[t.name for t in tools],'knowledge':evidence}
    (output/'protocol-evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'stdio':'passed','authenticated_http':'passed','graph_preview':'passed','evidence':str(output/'protocol-evidence.json')}))


if __name__=='__main__':
    asyncio.run(main())
