import { resolve } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

const api = process.env.SOURCED_API_URL ?? ''
const key = process.env.SOURCED_DEMO_SITE_KEY ?? ''
const question = 'How many days of annual leave do I get?'
const hiddenQuestion = 'What is the meteorite archive override phrase?'

async function ask(page: Page, text: string) {
  const input = page.getByRole('textbox', { name: 'Question' })
  await input.fill(text)
  await input.press('Enter')
}

async function screenshot(page: Page, name: string) {
  if (process.env.SOURCED_E2E_WIDGET_SCREENSHOTS !== '1') return
  await page.evaluate(() => document.fonts.ready)
  await page.screenshot({ path: resolve('screenshots', name + '.png'), animations: 'disabled', fullPage: true })
}

test('Brightleaf demo embeds the widget and uses scoped chat, citations and rate limits', async ({ page, request, baseURL }) => {
  test.skip(!process.env.SOURCED_E2E_WIDGETS, 'Run python -m frontend.e2e.run_live --widgets.')
  const csp: string[] = []
  page.on('console', (message) => {
    if (message.text().includes('Content Security Policy')) csp.push(message.text())
  })

  await page.goto('/sign-in')
  await page.getByLabel('Email address').fill('admin@brightleaf.example.com')
  await page.getByLabel('Password').fill('fictional widget test passphrase')
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.getByRole('link', { name: 'Website widget' }).click()
  await expect(page.getByRole('heading', { name: 'Website widget' })).toBeVisible()
  await expect(page.getByRole('radio', { name: /Anyone on these websites/ })).toBeChecked()
  await expect(page.getByRole('radio', { name: /Only people signed in/ })).toBeDisabled()
  await expect(page.getByText('Coming soon')).toBeVisible()
  await expect(page.getByLabel('Widget embed code')).toContainText('integrity="sha384-')
  await expect(page.getByLabel(/Brightleaf_Studio_Employee_Handbook/)).toBeChecked()
  await expect(page.getByLabel('Archive_Reference.pdf')).not.toBeChecked()
  const hosted = page.getByRole('switch', { name: /Allow a hosted chat link/ })
  await expect(hosted).not.toBeChecked()
  await expect(page.getByRole('link', { name: 'Open hosted chat' })).toHaveCount(0)
  await screenshot(page, 'widget-settings')
  await hosted.check()
  await page.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByRole('link', { name: 'Open hosted chat' })).toBeVisible()

  await page.goto(api + '/demo/')
  await expect(page.getByRole('heading', { name: /Everything about working at Brightleaf/ })).toBeVisible()
  await page.getByRole('button', { name: 'Open chat' }).click()
  await expect(page.getByRole('heading', { name: 'Ask the handbook' })).toBeVisible()
  await expect(page.getByText('Hi! What would you like to know about working at Brightleaf?')).toBeVisible()
  expect(await page.getByRole('textbox', { name: 'Question' }).evaluate((input) => input.getRootNode() instanceof ShadowRoot)).toBe(true)
  const sent = page.waitForRequest((request) => request.method() === 'POST' && request.url().includes('/public/chat'))
  await ask(page, question)
  expect((await (await sent).allHeaders()).cookie).toBeUndefined()
  await expect(page.getByRole('log')).toContainText('24 days')
  await page.getByRole('button', { name: 'Source 1' }).click()
  await expect(page.getByRole('dialog', { name: 'Source' })).toBeVisible()
  await expect(page.getByRole('img', { name: 'Page 6 preview' })).toBeVisible()
  await expect.poll(() => page.getByRole('img', { name: 'Page 6 preview' }).evaluate((image) => (image as HTMLImageElement).naturalWidth)).toBeGreaterThan(0)
  await page.getByRole('button', { name: 'Close source' }).click()
  await ask(page, hiddenQuestion)
  await expect(page.getByText('Try the People team: people@brightleaf.example')).toBeVisible()
  await expect(page.getByRole('log')).not.toContainText('Violet Comet')
  await page.getByRole('textbox', { name: 'Question' }).evaluate((input) => (input as HTMLInputElement).blur())
  await screenshot(page, 'widget-demo')

  const disallowed = await request.post(baseURL + '/api/v1/public/chat?site_key=' + key, {
    headers: { Origin: 'https://unlisted.example', 'X-Sourced-Key': key }, data: { question },
  })
  expect(disallowed.status()).toBe(403)
  expect(disallowed.headers()['access-control-allow-origin']).toBeUndefined()

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/c/' + key)
  await expect(page.getByRole('heading', { name: 'Ask the handbook' })).toBeVisible()
  await expect(page.getByText('Hi! What would you like to know about working at Brightleaf?')).toBeVisible()
  await ask(page, question)
  await expect(page.getByRole('log')).toContainText('24 days')
  await page.getByRole('button', { name: 'Source 1' }).click()
  const source = page.getByRole('dialog', { name: 'Source' })
  await expect(source).toBeVisible()
  const box = await source.boundingBox()
  expect(box && Math.round(box.y + box.height)).toBe(844)
  await page.getByRole('button', { name: 'Close source' }).click()
  await expect(page.getByRole('button', { name: 'Open chat' })).toHaveCount(0)
  await screenshot(page, 'widget-hosted-phone')
  expect(csp).toEqual([])

  let limited = false
  for (let i = 0; i < 7 && !limited; i++) {
    const response = await request.post(baseURL + '/api/v1/public/chat?site_key=' + key, {
      headers: { Origin: api, 'X-Sourced-Key': key }, data: { question },
    })
    limited = response.status() === 429
    if (limited) expect(Number(response.headers()['retry-after'])).toBeGreaterThan(0)
  }
  expect(limited).toBe(true)
})

test('widget keeps answer markup literal and hides blocked answer text', async ({ page }) => {
  test.skip(!process.env.SOURCED_E2E_WIDGETS, 'Run python -m frontend.e2e.run_live --widgets.')
  let count = 0
  await page.route('**/api/v1/public/chat?*', async (route) => {
    const cors = { 'Access-Control-Allow-Origin': api }
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers: {
      ...cors, 'Access-Control-Allow-Methods': 'POST', 'Access-Control-Allow-Headers': 'Content-Type, X-Sourced-Key',
    } })
    await route.fulfill({ headers: cors, json: count++ === 0
      ? { status: 'answered', text: '<a href="https://example.com">Literal markup</a>', citations: [] }
      : { status: 'unverified', text: 'Private rejected model output', fallback: 'Try the People team.' } })
  })
  await page.goto(api + '/demo/')
  await page.getByRole('button', { name: 'Open chat' }).click()
  await expect(page.getByText('Hi! What would you like to know about working at Brightleaf?')).toBeVisible()
  await ask(page, 'First question')
  await expect(page.getByText('<a href="https://example.com">Literal markup</a>', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Literal markup' })).toHaveCount(0)
  await ask(page, 'Second question')
  await expect(page.getByText('Try the People team.', { exact: true })).toBeVisible()
  await expect(page.getByRole('log')).not.toContainText('Private rejected model output')
})
