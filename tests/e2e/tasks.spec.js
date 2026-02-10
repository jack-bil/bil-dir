// @ts-check
const { test, expect } = require('@playwright/test');

/**
 * Task management E2E tests.
 *
 * These tests verify critical UI workflows that can't be tested via API alone.
 * Keep E2E tests focused on user-facing scenarios - use API tests for logic.
 */

test.describe('Task Management UI', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to home page before each test
    await page.goto('/');
    await expect(page.locator('h1')).toContainText('bil-dir');
  });

  test('should open and close task creation modal', async ({ page }) => {
    // Click "Add Task" button
    await page.click('[data-testid="add-task-btn"]');

    // Modal should be visible
    await expect(page.locator('[data-testid="task-modal"]')).toBeVisible();

    // Close modal
    await page.click('[data-testid="close-modal-btn"]');

    // Modal should be hidden
    await expect(page.locator('[data-testid="task-modal"]')).not.toBeVisible();
  });

  test('should create a simple task via UI', async ({ page }) => {
    // Open modal
    await page.click('[data-testid="add-task-btn"]');

    // Fill form
    await page.fill('[name="name"]', 'E2E Test Task');
    await page.fill('[name="prompt"]', 'This is a test task');
    await page.selectOption('[name="provider"]', 'codex');

    // Submit
    await page.click('[data-testid="submit-task-btn"]');

    // Wait for modal to close
    await expect(page.locator('[data-testid="task-modal"]')).not.toBeVisible();

    // Verify task appears in list
    await expect(page.locator('text=E2E Test Task')).toBeVisible();
  });

  test('should validate required fields', async ({ page }) => {
    // Open modal
    await page.click('[data-testid="add-task-btn"]');

    // Try to submit without filling fields
    await page.click('[data-testid="submit-task-btn"]');

    // Should show validation errors
    // (Update selectors based on your actual error display)
    await expect(page.locator('.error, .text-danger, [role="alert"]')).toBeVisible();
  });

  test('should toggle task enable/disable', async ({ page }) => {
    // Assumes at least one task exists
    const taskRow = page.locator('[data-testid="task-row"]').first();

    // Get initial state
    const initialState = await taskRow.getAttribute('data-enabled');

    // Click toggle button
    await taskRow.locator('[data-testid="toggle-enabled-btn"]').click();

    // Wait for state change
    await page.waitForTimeout(500);

    // Verify state changed
    const newState = await taskRow.getAttribute('data-enabled');
    expect(newState).not.toBe(initialState);
  });

  test('should run a task manually', async ({ page }) => {
    // Assumes at least one task exists
    const taskRow = page.locator('[data-testid="task-row"]').first();

    // Click run button
    await taskRow.locator('[data-testid="run-task-btn"]').click();

    // Should show running indicator
    await expect(taskRow.locator('[data-status="running"]')).toBeVisible({ timeout: 1000 });

    // Wait for completion (with timeout)
    await expect(taskRow.locator('[data-status="running"]')).not.toBeVisible({ timeout: 10000 });
  });
});

test.describe('Task Schedule Configuration', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.click('[data-testid="add-task-btn"]');
  });

  test('should configure daily schedule', async ({ page }) => {
    // Fill basic info
    await page.fill('[name="name"]', 'Daily Task');
    await page.fill('[name="prompt"]', 'Run daily');

    // Select daily schedule
    await page.selectOption('[name="schedule-type"]', 'daily');

    // Time field should appear
    await expect(page.locator('[name="schedule-time"]')).toBeVisible();

    // Set time
    await page.fill('[name="schedule-time"]', '09:00');

    // Submit
    await page.click('[data-testid="submit-task-btn"]');

    // Verify task created with schedule
    await expect(page.locator('text=Daily Task')).toBeVisible();
    await expect(page.locator('text=Daily at 09:00')).toBeVisible();
  });

  test('should configure weekly schedule', async ({ page }) => {
    await page.fill('[name="name"]', 'Weekly Task');
    await page.fill('[name="prompt"]', 'Run weekly');

    // Select weekly schedule
    await page.selectOption('[name="schedule-type"]', 'weekly');

    // Day checkboxes should appear
    await expect(page.locator('[name="days-of-week"]')).toBeVisible();

    // Select Monday, Wednesday, Friday
    await page.check('[value="1"]'); // Monday
    await page.check('[value="3"]'); // Wednesday
    await page.check('[value="5"]'); // Friday

    await page.fill('[name="schedule-time"]', '14:00');

    // Submit
    await page.click('[data-testid="submit-task-btn"]');

    // Verify
    await expect(page.locator('text=Weekly Task')).toBeVisible();
  });

  test('should configure monthly schedule', async ({ page }) => {
    await page.fill('[name="name"]', 'Monthly Task');
    await page.fill('[name="prompt"]', 'Run monthly');

    // Select monthly schedule
    await page.selectOption('[name="schedule-type"]', 'monthly');

    // Day of month field should appear
    await expect(page.locator('[name="day-of-month"]')).toBeVisible();

    // Set day and time
    await page.fill('[name="day-of-month"]', '15');
    await page.fill('[name="schedule-time"]', '10:00');

    // Submit
    await page.click('[data-testid="submit-task-btn"]');

    // Verify
    await expect(page.locator('text=Monthly Task')).toBeVisible();
  });
});
