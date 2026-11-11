import { resolve } from 'node:path'
import { expect, test } from '@playwright/test'

const key = process.env.SOURCED_SIGNED_WIDGET_KEY
const tokenPattern = /^st_[A-Za-z0-9_-]+\.[0-9a-f]{64}$/
const secretPattern = /sk_[A-Za-z0-9_-]{43}/

test('signed-in hosted chat consumes and clears its fragment and requires a bearer for the preview', async ({ page, request, baseURL }) => {
  test.skip(!key, 'Run python -m frontend.e2e.run_live --signed-in.')
  await page.goto('/sign-in')
  await page.getByLabel('Email address').fill('admin@brightleaf.example.com')
  await page.getByLabel('Password').fill('fictional widget test passphrase')
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.getByRole('link', { name: 'Website widget' }).click()
  await expect(page.getByRole('radio', { name: /Only people signed in/ })).toBeChecked()
  await expect(page.getByRole('link', { name: 'Open hosted chat' })).toBeVisible()
  if (process.env.SOURCED_E2E_WIDGET_SCREENSHOTS === '1') {
    await page.evaluate(() => document.fonts.ready)
    await page.screenshot({ path: resolve('screenshots', 'widget-settings-signed-in.png'), fullPage: true, animations: 'disabled' })
  }

  await page.getByRole('button', { name: 'Generate server secret key' }).click()
  const displayed = page.getByText(secretPattern)
  const secret = (await displayed.textContent())?.match(secretPattern)?.[0]
  expect(!!secret).toBe(true)
  const widgetId = await page.getByLabel('Widget', { exact: true }).inputValue()
  await page.getByRole('button', { name: 'Dismiss key' }).click()
  await expect(displayed).toHaveCount(0)
  const issued = await request.post(baseURL + '/api/v1/widgets/' + widgetId + '/session-tokens', {
    headers: { Authorization: 'Bearer ' + secret }, data: { user_ref: 'fixture-member' },
  })
  expect(issued.status()).toBe(200)
  const token = (await issued.json()).token
  expect(tokenPattern.test(token)).toBe(true)

  const hosted = '/c/' + key
  // An accidental query credential is redacted in the real access log and cannot open a session.
  await page.goto(hosted + '?token=' + encodeURIComponent(token))
  await expect(page.getByText('Sign in through your site or app to start chatting.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Send' })).toBeDisabled()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(hosted + '#token=' + encodeURIComponent(token))
  await expect(page).toHaveURL(baseURL + hosted)
  await expect(page.getByText('Hi! What would you like to know about working at Brightleaf?')).toBeVisible()
  const sent = page.waitForRequest((incoming) => incoming.method() === 'POST' && incoming.url().includes('/public/chat'))
  const question = page.getByRole('textbox', { name: 'Question' })
  await question.fill('How many days of annual leave do I get?')
  await question.press('Enter')
  const chat = await sent
  expect(chat.url().includes(token)).toBe(false)
  expect((await chat.allHeaders()).authorization === 'Bearer ' + token).toBe(true)
  expect((await chat.allHeaders()).cookie).toBeUndefined()
  await expect(page.getByRole('log')).toContainText('24 days')

  const fetched = page.waitForRequest((incoming) => incoming.method() === 'GET' && incoming.url().includes('/public/previews/'))
  await page.getByRole('button', { name: 'Source 1' }).click()
  const preview = await fetched
  expect(preview.url().includes(token)).toBe(false)
  expect((await preview.allHeaders()).authorization === 'Bearer ' + token).toBe(true)
  await expect(page.getByRole('img', { name: 'Page 6 preview' })).toBeVisible()
  expect((await request.get(preview.url())).status()).toBe(401)
  await page.getByRole('button', { name: 'Close source' }).click()
  await expect(page.locator('dialog blockquote')).toHaveCount(0)

  // Rotating the server secret revokes the token; the page reports it and takes a fresh one from the fragment.
  await page.evaluate(() => {
    const expired: string[] = []
    Object.assign(window, { expired })
    document.addEventListener('sourced:session-expired', (event) => expired.push(Object.keys((event as CustomEvent).detail).join()))
  })
  const admin = await page.context().newPage()
  await admin.goto('/widgets')
  admin.once('dialog', (dialog) => void dialog.accept())
  await admin.getByRole('button', { name: 'Rotate server secret key' }).click()
  const rotated = (await admin.getByText(secretPattern).textContent())?.match(secretPattern)?.[0]
  expect(!!rotated && rotated !== secret).toBe(true)
  await admin.close()
  await question.fill('How many days of annual leave do I get?')
  await question.press('Enter')
  await expect(page.getByRole('log')).toContainText('Your chat session is unavailable or expired.')
  expect(await page.evaluate(() => (window as any).expired)).toEqual(['siteKey'])
  const renewed = await request.post(baseURL + '/api/v1/widgets/' + widgetId + '/session-tokens', {
    headers: { Authorization: 'Bearer ' + rotated }, data: { user_ref: 'fixture-member' },
  })
  const fresh = (await renewed.json()).token
  await page.evaluate((value) => { location.hash = 'token=' + value }, fresh)
  await expect(page).toHaveURL(baseURL + hosted)
  await expect(page.getByRole('log')).toHaveText('Hi! What would you like to know about working at Brightleaf?')
  const resent = page.waitForRequest((incoming) => incoming.method() === 'POST' && incoming.url().includes('/public/chat'))
  await question.fill('How many days of annual leave do I get?')
  await question.press('Enter')
  expect((await (await resent).allHeaders()).authorization === 'Bearer ' + fresh).toBe(true)
  await expect(page.getByRole('log')).toContainText('24 days')
  await page.reload()
  await expect(page.getByText('Sign in through your site or app to start chatting.')).toBeVisible()
})
