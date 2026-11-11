import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test, type Page, type Route } from '@playwright/test'

// The widget's page events, against the newest released widget.js with a stubbed public API.
const dist = resolve('..', 'widget', 'dist')
const releases = JSON.parse(readFileSync(resolve(dist, 'releases.json'), 'utf-8')) as Record<string, unknown>
const newest = Object.keys(releases).sort((a, b) => {
  const [x, y] = [a, b].map((version) => version.split('.').map(Number))
  return x[0] - y[0] || x[1] - y[1] || x[2] - y[2]
}).at(-1)!
const sourced = 'https://sourced.test'
const host = 'https://host.test'
const key = 'pk_' + 'A'.repeat(32)
const token = (name: string) => `st_${name}.` + 'a'.repeat(64)
const preview = '/api/v1/public/previews/grant.' + 'f'.repeat(64) + '.png'
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64')
const greeting = 'Hi! Ask me anything about the handbook.'
const expiredText = 'Your chat session is unavailable or expired. Reopen chat from your site or app.'

function tag(mode?: string) {
  const hosted = mode ? ` data-mode="${mode}"` : ''
  return `<script async src="${sourced}/widget-assets/${newest}/widget.js" data-site-key="${key}"${hosted}></script>`
}

/** Serves the released assets and a signed-in public API that accepts only tokens not yet revoked. */
async function stubSourced(page: Page, revoked: Set<string>, used: string[]) {
  await page.route(sourced + '/**', async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    const cors = { 'access-control-allow-origin': request.headers().origin ?? '*' }
    if (request.method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: {
        ...cors, 'access-control-allow-methods': 'GET, POST', 'access-control-allow-headers': 'Authorization, Content-Type',
      } })
    }
    const asset = url.pathname.match(/^\/widget-assets\/[\d.]+\/(widget\.(?:js|css))$/)
    if (asset) {
      return route.fulfill({
        body: readFileSync(resolve(dist, newest, asset[1])),
        contentType: asset[1].endsWith('.js') ? 'application/javascript' : 'text/css',
      })
    }
    if (url.pathname === '/c/' + key) {
      return route.fulfill({ contentType: 'text/html', body: `<!doctype html><title>Chat</title><body>${tag('hosted')}</body>` })
    }
    if (url.pathname === `/api/v1/public/widgets/${key}/config`) {
      return route.fulfill({ headers: cors, json: {
        title: 'Ask the handbook', greeting, brand_color: '#1F5C46', access_mode: 'signed_in',
      } })
    }
    const bearer = (request.headers().authorization ?? '').replace(/^Bearer /, '')
    used.push(url.pathname.split('/').at(-1) + ' ' + bearer)
    if (!bearer || revoked.has(bearer)) {
      return route.fulfill({ status: 401, headers: cors, json: { detail: 'A valid widget session token is required.' } })
    }
    if (url.pathname === '/api/v1/public/chat') {
      return route.fulfill({ headers: cors, json: {
        status: 'answered', text: 'You get 24 days [1].', partial: false, searched_for: '', reason: '', fallback: '',
        citations: [{ number: 1, document_id: 'doc', source: 'handbook.pdf', page: 6, quote: '24 days', verified: true, preview_url: preview }],
        passages: [], judgement: null,
      } })
    }
    if (url.pathname === preview) return route.fulfill({ headers: cors, contentType: 'image/png', body: png })
    return route.fulfill({ status: 404, headers: cors, body: '' })
  })
}

async function ask(page: Page, text: string) {
  const input = page.getByRole('textbox', { name: 'Question' })
  await input.fill(text)
  await input.press('Enter')
}

test('a host page that listens late gets the setter and refreshes an expired session in place', async ({ page }) => {
  const revoked = new Set<string>()
  const used: string[] = []
  await stubSourced(page, revoked, used)
  await page.route(host + '/', (route) => route.fulfill({
    contentType: 'text/html', body: `<!doctype html><title>Host</title><body><h1>Brightleaf people</h1>${tag()}</body>`,
  }))
  // An early listener sees the widget's own announcement.
  await page.addInitScript(() => {
    const seen = { ready: 0 }
    Object.assign(window, { seen })
    document.addEventListener('sourced:ready', () => { seen.ready++ })
  })
  await page.goto(host + '/')
  await expect(page.getByRole('button', { name: 'Open chat' })).toBeVisible()
  await expect.poll(() => page.evaluate(() => (window as any).seen.ready)).toBe(1)

  // The host's integration script attaches after the widget started, then asks for ready again.
  await page.evaluate(([siteKey, first, second, third]) => {
    const script = document.querySelector(`script[data-site-key="${siteKey}"]`)!
    const fresh = [first, second, third]
    const log: object[] = []
    Object.assign(window, { log })
    function connect(event: Event) {
      const { detail, bubbles, target } = event as CustomEvent
      if (detail.siteKey !== siteKey) return
      log.push({ type: event.type, bubbles, fromScript: target === script, setter: typeof detail.setSessionToken })
      detail.setSessionToken(fresh.shift() ?? null)
    }
    document.addEventListener('sourced:ready', connect)
    document.addEventListener('sourced:session-expired', connect)
    script.dispatchEvent(new Event('sourced:request-ready'))
  }, [key, token('first'), token('second'), token('third')])
  await expect.poll(() => page.evaluate(() => (window as any).log)).toEqual([
    { type: 'sourced:ready', bubbles: true, fromScript: true, setter: 'function' },
  ])
  expect(await page.evaluate(() => (window as any).seen.ready)).toBe(2)

  await page.getByRole('button', { name: 'Open chat' }).click()
  const log = page.getByRole('log')
  await expect(log).toContainText(greeting)
  await ask(page, 'How many days of leave do I get?')
  await expect(log).toContainText('24 days')

  // The API refuses the token: the widget reports it, and the host's fresh token starts a new conversation.
  revoked.add(token('first'))
  await ask(page, 'And part-time?')
  await expect.poll(() => page.evaluate(() => (window as any).log.length)).toBe(2)
  expect(await page.evaluate(() => (window as any).log[1])).toEqual(
    { type: 'sourced:session-expired', bubbles: true, fromScript: true, setter: 'function' },
  )
  await expect(log).toHaveText(greeting)
  await expect(page.getByRole('textbox', { name: 'Question' })).toHaveValue('')
  await ask(page, 'How many days of leave do I get?')
  await expect(log).toContainText('24 days')
  expect(used).toEqual(['chat ' + token('first'), 'chat ' + token('first'), 'chat ' + token('second')])

  // A refused cited-page preview reports the expiry too.
  revoked.add(token('second'))
  await page.getByRole('button', { name: 'Source 1' }).click()
  await expect.poll(() => page.evaluate(() => (window as any).log.length)).toBe(3)
  expect(await page.evaluate(() => (window as any).log[2].type)).toBe('sourced:session-expired')
  await expect(page.getByRole('dialog', { name: 'Source' })).toHaveCount(0)
  await expect(log).toHaveText(greeting)
  expect(used.at(-1)).toBe('grant.' + 'f'.repeat(64) + '.png ' + token('second'))
})

test('a host without a refresh listener sees the expiry message and keeps the question', async ({ page }) => {
  const revoked = new Set([token('old')])
  await stubSourced(page, revoked, [])
  await page.route(host + '/', (route) => route.fulfill({ contentType: 'text/html', body: `<!doctype html><body>${tag()}</body>` }))
  await page.addInitScript(([siteKey, old]) => {
    document.addEventListener('sourced:ready', (event) => {
      const { detail } = event as CustomEvent
      if (detail.siteKey === siteKey) detail.setSessionToken(old)
    })
  }, [key, token('old')])
  await page.goto(host + '/')
  await page.getByRole('button', { name: 'Open chat' }).click()
  await expect(page.getByRole('log')).toContainText(greeting)
  await ask(page, 'How many days of leave do I get?')
  await expect(page.getByRole('log')).toContainText(expiredText)
  await expect(page.getByRole('textbox', { name: 'Question' })).toHaveValue('How many days of leave do I get?')
  await expect(page.getByRole('button', { name: 'Send' })).toBeEnabled()
})

test('the hosted page reports expiry and takes a fresh token from location.hash without reloading', async ({ page }) => {
  const revoked = new Set<string>()
  const used: string[] = []
  await stubSourced(page, revoked, used)
  await page.addInitScript(() => {
    const events: object[] = []
    Object.assign(window, { events })
    document.addEventListener('sourced:ready', () => events.push({ type: 'ready' }))
    document.addEventListener('sourced:session-expired', (event) => {
      const { detail, bubbles } = event as CustomEvent
      events.push({ type: event.type, bubbles, keys: Object.keys(detail) })
    })
  })
  let documents = 0
  page.on('request', (request) => { if (request.resourceType() === 'document') documents++ })
  const hosted = `${sourced}/c/${key}`
  await page.goto(hosted + '#token=' + token('first'))
  await expect(page).toHaveURL(hosted)
  const log = page.getByRole('log')
  await expect(log).toContainText(greeting)
  await page.evaluate((siteKey) => {
    document.querySelector(`script[data-site-key="${siteKey}"]`)!.dispatchEvent(new Event('sourced:request-ready'))
  }, key)
  await ask(page, 'How many days of leave do I get?')
  await expect(log).toContainText('24 days')

  revoked.add(token('first'))
  await ask(page, 'And part-time?')
  await expect(log).toContainText(expiredText)
  expect(await page.evaluate(() => (window as any).events)).toEqual([
    { type: 'sourced:session-expired', bubbles: true, keys: ['siteKey'] },
  ])

  // What the Android and iOS examples in docs/mobile.md run.
  await page.evaluate((fresh) => { location.hash = 'token=' + fresh }, token('second'))
  await expect(page).toHaveURL(hosted)
  await expect(log).toHaveText(greeting)
  await ask(page, 'How many days of leave do I get?')
  await expect(log).toContainText('24 days')
  expect(used).toEqual(['chat ' + token('first'), 'chat ' + token('first'), 'chat ' + token('second')])
  expect(documents).toBe(1)
})
