import { Component, type ErrorInfo, type ReactNode } from "react";
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error("Workspace rendering failed", error, info.componentStack); }
  render() { return this.state.failed ? <main className="login-page"><section className="login-card" role="alert"><span className="section-kicker">WORKSPACE</span><h1>页面暂时无法显示</h1><p>请重新加载工作台。会话和已受理的请求可从服务器恢复。</p><button className="primary-button" onClick={() => window.location.reload()}>重新加载工作台</button></section></main> : this.props.children; }
}
