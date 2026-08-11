export function App() {
  return (
    <main className="shell">
      <section className="hero" aria-labelledby="page-title">
        <span className="eyebrow">LOCAL DEMO · MOCK MODE</span>
        <h1 id="page-title">企业 IT 运维知识助手</h1>
        <p>
          项目基线已启动。后续任务将在这里加入可信知识检索、工单查询与经确认的工单创建流程。
        </p>
        <div className="status" role="status">
          <span className="status-dot" aria-hidden="true" />
          前端服务运行中
        </div>
      </section>
    </main>
  );
}
