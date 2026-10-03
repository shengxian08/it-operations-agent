export function HandoffNotice({ escalationId }: { escalationId?: string | null }) {
  return <section className="result-card">
    <h2>{escalationId ? "人工支持请求已记录" : "人工交接未确认"}</h2>
    {escalationId ? <><p>请求编号：{escalationId}</p><p>提交时状态：待处理。尚未确认支持人员接单。</p></>
      : <p>尚未取得人工支持记录编号。请通过企业现有 IT 支持渠道联系处理。</p>}
    <p className="field-help">后续创建正式工单仍需你核对并确认。</p>
  </section>;
}
