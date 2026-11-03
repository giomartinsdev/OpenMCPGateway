import { defineConfig, devices } from "@playwright/test";

// Real backend, real Postgres, real browser. No route stubbing: the point of
// this suite is that a hidden button and a refused request are the same
// permission, so anything mocked would prove nothing.
const PORT = Number(process.env.E2E_PORT ?? 8100);

export default defineConfig({
  testDir: "./e2e/specs",
  // One spec, one tenant: scenarios share the account the first one creates.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // The panel follows the browser's language and colour scheme, so both are
    // pinned: a developer on a pt-BR machine must not run the suite in
    // Portuguese, and the assertions below quote English copy.
    locale: "en-US",
    colorScheme: "dark",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "bash e2e/serve.sh",
    url: `http://127.0.0.1:${PORT}/healthz`,
    // A stale database on port 55432 would make the first-account assertions
    // fail for the wrong reason, so never reuse: the script owns the lifetime.
    reuseExistingServer: false,
    timeout: 120_000,
    env: { E2E_PORT: String(PORT) },
  },
});
