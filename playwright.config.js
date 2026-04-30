const { defineConfig } = require("@playwright/test");

process.env.DATABASE_URL = "";
process.env.GEMINI_API_KEY = "";

module.exports = defineConfig({
  testDir: "tests/e2e",
  timeout: 30000,
  use: {
    baseURL: "http://127.0.0.1:8001",
    headless: true,
  },
  webServer: {
    command: "python manage.py runserver 127.0.0.1:8001 --noreload",
    url: "http://127.0.0.1:8001",
    reuseExistingServer: false,
    timeout: 120000,
  },
});
