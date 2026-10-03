import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  timeout: 30_000,
  use: { trace: "retain-on-failure" },
  webServer: process.env.E2E_BASE_URL ? undefined : [
    { command: "npm run dev -- --host 127.0.0.1 --port 4173 --strictPort", url: "http://127.0.0.1:4173", env: { VITE_DEMO_ENABLED: "true" }, reuseExistingServer: !process.env.CI },
    { command: "npm run dev -- --host 127.0.0.1 --port 4174 --strictPort", url: "http://127.0.0.1:4174", env: { VITE_DEMO_ENABLED: "false" }, reuseExistingServer: !process.env.CI },
  ],
  projects: [
    { name: "demo", testMatch: "workspace.spec.ts", use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:4173" } },
    { name: "production-mock", testMatch: "production-mock.spec.ts", use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:4174" } },
    { name: "production-live", testMatch: "production-live.spec.ts", use: { ...devices["Desktop Chrome"], baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:4174", ignoreHTTPSErrors: true, launchOptions: { args: ["--host-resolver-rules=MAP keycloak 127.0.0.1"] } }, timeout: 120_000 },
  ],
});
