import { resolve } from 'node:path'
import { expect, test } from '@playwright/test'
import type { Answer, Citation } from '../src/api'

const handbook = 'Employee_Handbook_v3.2.pdf'
const date = new Date().toISOString().slice(0, 10)
const member = { workspace_id: 'sample', workspace_name: 'Brightleaf Studio', email: 'admin@brightleaf.example' }
const citation: Citation = {
  number: 1, document_id: '76f13c80-b1a5-4250-afd4-95c044f8be35', source: handbook, page: 4, verified: true,
  quote: 'Lead, Principal and Director roles | 8 weeks',
  preview_url: null,
}
const answers: Answer[] = [
  {
    status: 'answered', text: 'As a Director, you must give 8 weeks’ notice [1]. Return your laptop and other equipment within 5 working days of your last day [2].',
    citations: [citation, { ...citation, number: 2, page: 15, quote: 'Return equipment within 5 working days.' }],
    judgement: { verdict: 'supported', judge: 'GroqCloud', problems: [] },
    partial: false, searched_for: '', reason: '', passages: [],
  },
  { status: 'not_found', text: 'The documents don’t cover a dress code.', citations: [], judgement: null,
    partial: false, searched_for: '', reason: '', passages: [] },
]

test('capture each dashboard and account page with fictional fixture data', async ({ page }) => {
  const preview = process.env.SOURCED_PREVIEW_PNG
  test.skip(!preview, 'Run python -m frontend.e2e.run_live --screenshots to capture screenshots.')
  await page.setViewportSize({ width: 1280, height: 900 })
  let signedIn = false
  let asked = 0
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    switch (path) {
      case '/auth/me': return signedIn ? route.fulfill({ json: member }) : route.fulfill({ status: 401, json: {} })
      case '/documents/settings':
        return route.fulfill({ json: { max_bytes: 10 * 1024 ** 2, max_pages: 80, embedding_provider: 'OpenAI embeddings' } })
      case '/documents':
        return route.fulfill({ json: [
          { id: 'handbook', filename: handbook, pages: 16, status: 'ready', progress: 100, error: null, created_at: '2026-11-02T10:00:00Z' },
          { id: 'travel', filename: 'Travel_and_Expenses_Policy.pdf', pages: 9, status: 'ready', progress: 100, error: null, created_at: '2026-11-02T10:00:00Z' },
          { id: 'security', filename: 'IT_Security_Guidelines.pdf', pages: 36, status: 'processing', progress: 61, error: null, created_at: date + 'T14:02:00Z' },
          { id: 'scan', filename: 'Office_floorplan_scan.pdf', pages: 4, status: 'failed', progress: 0,
            error: 'Scanned pages without text. OCR isn’t available yet.', created_at: date + 'T13:40:00Z' },
        ] })
      case '/providers': return route.fulfill({ json: [
        { name: 'ollama', label: 'Ollama · qwen3.6:35b-a3b (private)', ready: true },
        { name: 'groq', label: 'GroqCloud · gpt-oss-120b', ready: true },
        { name: 'openai', label: 'OpenAI · gpt-6.1-terra', ready: true },
      ] })
      case '/chat': return route.fulfill({ json: answers[asked++] })
      case '/usage': return route.fulfill({ json: [
        { day: date, channel: 'ask_test', questions: 18, answered: 14, not_found: 3, blocked: 1 },
        { day: '2026-11-10', channel: 'ask_test', questions: 12, answered: 9, not_found: 2, blocked: 1 },
        { day: '2026-11-09', channel: 'ask_test', questions: 8, answered: 6, not_found: 2, blocked: 0 },
      ] })
      default: return path.endsWith('.png') || path.endsWith('/highlight')
        ? route.fulfill({ path: preview!, contentType: 'image/png' }) : route.fulfill({ status: 404 })
    }
  })
  const save = async (name: string) => {
    await page.evaluate(() => document.fonts.ready)
    await page.screenshot({ path: resolve('screenshots', name + '.png'), fullPage: true, animations: 'disabled' })
  }
  await page.goto('/sign-in')
  await expect(page.getByRole('heading', { name: 'Sign in to Sourced' })).toBeVisible()
  await save('sign-in')
  await page.goto('/sign-up')
  await expect(page.getByRole('heading', { name: 'Create your workspace' })).toBeVisible()
  await save('sign-up')
  signedIn = true
  await page.goto('/documents')
  await expect(page.getByText('Couldn’t read')).toBeVisible()
  await save('documents')
  await page.goto('/ask')
  await expect(page.getByText('Ready', { exact: true })).toBeVisible()
  const question = page.getByLabel('Question', { exact: true })
  await question.fill('I’m a Director and I’m resigning. How much notice must I give, and how soon must I return my laptop?')
  await question.press('Enter')
  await expect(page.getByRole('article', { name: 'Answer' })).toContainText('8 weeks')
  await question.fill('What’s the dress code for client meetings?')
  await question.press('Enter')
  await expect(page.getByText('The documents don’t cover a dress code.')).toBeVisible()
  const firstCitation = page.getByRole('button', { name: 'Source 1' })
  const metrics = await firstCitation.evaluate((button) => {
    const box = button.getBoundingClientRect()
    const range = document.createRange()
    range.setStart(button.nextSibling!, 0)
    range.setEnd(button.nextSibling!, 1)
    return {
      punctuation: range.toString(),
      gap: range.getBoundingClientRect().left - box.right,
      height: box.height,
      lineHeight: parseFloat(getComputedStyle(button.parentElement!).lineHeight),
    }
  })
  expect(metrics.punctuation).toBe('.')
  expect(Math.abs(metrics.gap)).toBeLessThan(1)
  expect(metrics.height).toBeLessThan(metrics.lineHeight)
  await firstCitation.click()
  await expect(page.getByRole('img', { name: 'Page 4 preview' })).toHaveJSProperty('complete', true)
  await save('ask')
  await page.goto('/usage')
  await expect(page.getByRole('table')).toBeVisible()
  await save('usage')
})
