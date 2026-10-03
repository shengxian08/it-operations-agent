import { lazy, Suspense } from "react";
import { ErrorBoundary } from "./production/ErrorBoundary";
import { ProductionApp } from "./production/ProductionApp";

const DemoApp = import.meta.env.VITE_DEMO_ENABLED === "true"
  ? lazy(() => import("./DemoApp").then((module) => ({ default: module.DemoApp })))
  : null;
export function App() {
  return <ErrorBoundary>{DemoApp ? <Suspense fallback={<p className="page-loading" role="status">正在加载演示工作台…</p>}><DemoApp /></Suspense> : <ProductionApp />}</ErrorBoundary>;
}
