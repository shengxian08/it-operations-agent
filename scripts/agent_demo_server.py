"""Serve the real IT HTTP/worker stack on loopback with public synthetic identity/data.

Only the explicitly owned test compose project and its fixed isolated ports are allowed.
Never use this entry point for enterprise documents, OIDC, or real models.
"""
import asyncio
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
DATABASE='postgresql+asyncpg://itops_test:isolated-test-only@127.0.0.1:15932/itops_testing'
OUT=ROOT/'artifacts/agent-deepening/demo'


def main():
    expected={'postgres','redis','qdrant'}
    owned=subprocess.run(['docker','ps','--filter','label=com.docker.compose.project=itops-agent-deepening-test',
        '--format','{{.Label "com.docker.compose.service"}}'],capture_output=True,text=True,check=True).stdout.splitlines()
    if not expected.issubset(owned):
        raise ValueError('Start the owned itops-agent-deepening-test compose project first')
    os.environ.update({'ENVIRONMENT':'test','DEMO_ENABLED':'false','MODEL_MODE':'mock',
        'DATABASE_URL':DATABASE,'TEST_DATABASE_URL':DATABASE,'REDIS_URL':'redis://127.0.0.1:16379/15',
        'QDRANT_URL':'http://127.0.0.1:17333','PUBLIC_BASE_URL':'http://127.0.0.1:18201',
        'COOKIE_SECURE':'false','SESSION_SECRET':'public-synthetic-agent-deepening-demo-20261003',
        'MCP_ENABLED':'true','AGENTIC_RAG_ENABLED':'true','RELATION_RETRIEVAL_ENABLED':'true',
        'INVESTIGATION_STRATEGY':'multi','MODEL_MONTHLY_BUDGET':'100','MODEL_INPUT_PRICE':'1',
        'AGENT_TOKEN_LIMIT':'256000','MODEL_MAX_RETRIES':'0',
        'MODEL_OUTPUT_PRICE':'2','USER_RATE_LIMIT':'100',
        'KNOWLEDGE_STORAGE_PATH':str(OUT/'knowledge'),
        'OPERATIONS_SNAPSHOT_PATH':str(ROOT/'backend/app/data/operations-public-snapshot.json')})
    OUT.mkdir(parents=True,exist_ok=True)
    from app.main import app
    from app.db.models import User
    from app.production.identity_models import IdentityAccount
    from app.production.worker import Worker
    from starlette.staticfiles import StaticFiles
    import uvicorn
    original=app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            sessions=application.state.session_factory
            async with sessions.begin() as session:
                if await session.get(User,'public-agent-demo-reviewer') is None:
                    session.add(User(id='public-agent-demo-reviewer',display_name='公开合成演示管理员',access_level='admin'))
                    await session.flush()
                    session.add(IdentityAccount(user_id='public-agent-demo-reviewer',issuer='https://synthetic.demo.invalid',
                        subject='public-agent-demo-reviewer',enabled=True))
            identity=application.state.identity_service
            handle,csrf='public-synthetic-browser-agent-20261003','public-synthetic-csrf-20261003'
            await application.state.redis.set(identity._key('session',handle),json.dumps({'user_id':'public-agent-demo-reviewer','csrf_token':csrf}),ex=3600)
            (OUT/'session.json').write_text(json.dumps({'origin':'http://127.0.0.1:18201','cookie_name':identity.settings.session_cookie_name,
                'cookie':handle,'csrf_token':csrf,'data_classification':'public-synthetic'},indent=2),encoding='utf-8')
            worker=Worker(application.state.settings,application.state.run_service,application.state.ticket_service,application.state.knowledge_service)
            task=asyncio.create_task(worker.run())
            try:
                yield
            finally:
                worker.stop_event.set()
                await task
    app.router.lifespan_context=lifespan
    app.mount('/',StaticFiles(directory=str(ROOT/'frontend/dist'),html=True),name='public-agent-demo-web')
    uvicorn.run(app,host='127.0.0.1',port=18201,log_level='warning')


if __name__=='__main__':
    main()
