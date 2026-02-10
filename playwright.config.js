// @ts-check
const { defineConfig, devices } = require('@playwright/test');

/**
 * Playwright configuration for bil-dir UI tests.
 *
 * Optimized for speed:
 * - Runs headless by default (faster)
 * - Parallel execution (4 workers)
 * - Shared test fixtures to avoid repeated setup
 * - Screenshot only on failure
 */
module.exports = defineConfig({
  testDir: './tests/e2e',

  /* Run tests in parallel */
  fullyParallel: true,
  workers: 4,

  /* Fail the build on CI if tests are committed */
  forbidOnly: !!process.env.CI,

  /* Retry on CI only */
  retries: process.env.CI ? 2 : 0,

  /* Reporter */
  reporter: [
    ['html', { open: 'never' }],
    ['list']
  ],

  /* Shared settings for all projects */
  use: {
    /* Base URL - tests can use relative paths */
    baseURL: 'http://localhost:5050',

    /* Collect trace only on failure */
    trace: 'on-first-retry',

    /* Screenshot only on failure */
    screenshot: 'only-on-failure',

    /* Faster navigation - don't wait for full network idle */
    waitForLoadState: 'domcontentloaded',
  },

  /* Configure projects for major browsers */
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },

    // Uncomment to test other browsers
    // {
    //   name: 'firefox',
    //   use: { ...devices['Desktop Firefox'] },
    // },
    // {
    //   name: 'webkit',
    //   use: { ...devices['Desktop Safari'] },
    // },
  ],

  /* Run local dev server before starting tests */
  webServer: {
    command: 'python app.py',
    url: 'http://localhost:5050',
    reuseExistingServer: !process.env.CI,
    timeout: 120 * 1000,
  },
});
