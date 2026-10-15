"""Headless browser verification of the real synthetic IT HTTP/worker evidence flow."""
import json
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

ROOT=Path(__file__).resolve().parents[1]


def main():
    output=ROOT/'artifacts/agent-deepening/demo'
    session=json.loads((output/'session.json').read_text())
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            context=browser.new_context(viewport={'width':1440,'height':1000})
            context.add_cookies([{'name':session['cookie_name'],'value':session['cookie'],'url':session['origin']}])
            page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(session['origin']);page.wait_for_load_state('networkidle')
            with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/api/v1/conversations')) as created:
                page.get_by_role('button',name='新建会话',exact=True).click()
            conversation = created.value.json()
            expect(page.get_by_text(f"会话 {conversation['id'][:8]}", exact=False)).to_be_visible()
            page.wait_for_load_state('networkidle')
            expect(page.get_by_role('button',name='刷新会话',exact=True)).to_be_enabled()
            expect(page.get_by_label('任务模式')).to_be_visible()
            page.get_by_label('任务模式').select_option('investigate')
            expect(page.get_by_label('任务模式')).to_have_value('investigate')
            page.get_by_label('输入 IT 问题').fill('调查 checkout 在 2026-10-01T08:00:00Z 到 2026-10-01T09:00:00Z 的故障和变更')
            expect(page.get_by_label('任务模式')).to_have_value('investigate')
            with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/runs')) as submitted:
                page.get_by_role('button',name='发送请求',exact=True).click()
            assert submitted.value.request.post_data_json['task_mode'] == 'investigate'
            expect(page.get_by_role('region',name='只读调查证据')).to_be_visible(timeout=30000)
            expect(page.get_by_text('调查报告已生成',exact=True)).to_be_visible(timeout=30000)
            page.get_by_role('region',name='只读调查证据').get_by_text('服务状态 · 已取得观察',exact=True).click()
            health = page.get_by_role('region',name='只读调查证据').locator('details').filter(has=page.get_by_text('服务状态 · 已取得观察',exact=True))
            expect(health.get_by_text('历史快照 · 2026-10-01T08:30:00Z',exact=True)).to_be_visible()
            page.screenshot(path=str(output/'investigation-browser.png'),full_page=True)
            page.reload();page.wait_for_load_state('networkidle')
            expect(page.get_by_role('region',name='只读调查证据')).to_be_visible(timeout=10000)
            assert not errors,errors
        finally:
            browser.close()
    (output/'browser-evidence.json').write_text(json.dumps({'tier':'real-services','model_quality':'unverified',
        'investigation':'passed','snapshot_origin':'passed','reconnect':'passed','page_errors':errors},indent=2)+'\n',encoding='utf-8')
    print('Actual IT browser investigation, snapshot provenance, and reconnect passed.')


if __name__=='__main__':
    main()
