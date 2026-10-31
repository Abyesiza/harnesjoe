import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  use: { baseURL: "http://127.0.0.1:3100", channel: "chrome" },
  webServer: { command: "npx next start -p 3100", url: "http://127.0.0.1:3100", reuseExistingServer: true },
});
