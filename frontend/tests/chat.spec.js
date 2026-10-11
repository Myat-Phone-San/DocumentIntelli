import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/conversations', route => route.fulfill({ json: { conversations: [] } }));
});

test('chat renders Markdown answers, uses server IDs, and restores database history', async ({ page }) => {
  const requests = [];
  let stored = [];
  const conversationId = 'a'.repeat(32);
  const answer = '**Approved answer**\n\n| Feature | Details |\n| --- | --- |\n| Withdrawals | Supported |';
  await page.route('**/api/conversations', route => route.fulfill({ json: { conversations: stored.length ? [{ id: conversationId, title: 'Can I withdraw?' }] : [] } }));
  await page.route(`**/api/conversations/${conversationId}`, route => route.fulfill({ json: { messages: stored, next_before: null } }));
  await page.route('**/api/conversations/chat', async route => {
    const payload = route.request().postDataJSON();
    requests.push(payload);
    const turn = [{ id: stored.length + 1, role: 'user', content: payload.question }, { id: stored.length + 2, role: 'assistant', content: answer }];
    stored = [...stored, ...turn];
    await route.fulfill({ json: { answer, conversation_id: conversationId, messages: turn } });
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'What would you like to understand?' })).toBeVisible();
  await page.screenshot({ path: 'test-results/chat-desktop.png', fullPage: true });
  await page.getByRole('textbox', { name: 'Your question' }).fill('Can I withdraw?');
  await page.getByRole('button', { name: 'Send question' }).click();
  await expect(page.getByRole('table')).toBeVisible();
  expect(requests[0].conversation_id).toBeNull();
  await page.getByRole('textbox', { name: 'Your question' }).fill('What about deposits?');
  await page.getByRole('button', { name: 'Send question' }).click();
  await expect(page.getByRole('table')).toHaveCount(2);
  expect(requests[1].conversation_id).toBe(conversationId);
  expect(requests[1].history).toBeUndefined();
  await page.evaluate(() => localStorage.removeItem('intelligence-chats'));
  await page.reload();
  await expect(page.getByRole('table')).toHaveCount(2);
  await page.getByRole('button', { name: 'New conversation' }).click();
  await expect(page.getByRole('heading', { name: 'What would you like to understand?' })).toBeVisible();
});

test('administrator panel requires a key and supports grouped uploads', async ({ page }) => {
  let uploaded = false;
  await page.route('**/api/admin/documents', async route => {
    if (route.request().headers()['x-admin-key'] !== 'correct-key') {
      await route.fulfill({ status: 403, json: { detail: 'Administrator access required.' } });
      return;
    }
    if (route.request().method() === 'POST') {
      uploaded = true;
      await route.fulfill({ json: { id: 'a'.repeat(32), name: 'accounts.txt', category: 'Accounts' } });
      return;
    }
    await route.fulfill({ json: { documents: uploaded ? [{ id: 'a'.repeat(32), name: 'accounts.txt', category: 'Accounts', created_at: '2026-10-11' }] : [] } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Administrator', exact: true }).click();
  await expect(page.getByText('Choose a document')).toHaveCount(0);
  await page.getByLabel('Administrator key').fill('wrong-key');
  await page.getByRole('button', { name: 'Unlock knowledge base' }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('Administrator access required.');
  await page.getByLabel('Administrator key').fill('correct-key');
  await page.getByRole('button', { name: 'Unlock knowledge base' }).click();
  await page.getByLabel('Document group').fill('Accounts');
  await page.locator('input[type=file]').setInputFiles({ name: 'accounts.txt', mimeType: 'text/plain', buffer: Buffer.from('Deposits are supported.') });
  await page.getByRole('button', { name: 'Add to knowledge base' }).click();
  await expect(page.getByRole('heading', { name: 'Accounts', exact: true })).toBeVisible();
  await expect(page.getByRole('status')).toContainText('Document added');
  await page.getByRole('button', { name: 'Close administrator panel' }).click();
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain('correct-key');
  await page.getByRole('button', { name: 'Administrator', exact: true }).click();
  await expect(page.getByLabel('Administrator key')).toHaveValue('');
});

test('mobile chat has no horizontal overflow and opens its navigation', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('textbox', { name: 'Your question' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/chat-mobile.png', fullPage: true });
  await page.getByRole('button', { name: 'Open sidebar' }).click();
  await expect(page.getByRole('button', { name: 'Administrator', exact: true })).toBeVisible();
});
