// Security reviews S03, N03 and N08: the Streamlit demo shows document and model text exactly as written,
// with no links, images or formatting, in a real browser. Run with: npx playwright test -c playwright.streamlit.config.ts
import { expect, test } from '@playwright/test'

const HOSTILE =
  'Visit https://evil.example/x, www.evil.example, _https://under.example and _www.under.example, ' +
  'mail boss@evil.example or @handle, [click](https://evil.example/login) ![pixel](https://evil.example/p.png) ' +
  '**bold** _em_ `code` <b>tag</b> <img src=x onerror=alert(1)> $5 & co'

const FIELDS = ['ANSWER', 'PROBLEM', 'SOURCE', 'QUOTE', 'BLOCKED', 'PASSAGE-SOURCE', 'PASSAGE', 'WARNING', 'PROMPT']

test('untrusted text renders literally, with nothing clickable', async ({ page }) => {
  await page.goto('/')
  const main = page.locator('[data-testid="stMain"]')
  await expect(main.getByText('END OF PAGE')).toBeVisible({ timeout: 30_000 })
  for (const summary of await main.locator('summary').all()) await summary.click()
  await expect(main.getByText('QUOTE Visit', { exact: false })).toBeVisible()
  await expect(main.getByText('PASSAGE Visit', { exact: false })).toBeVisible()

  expect(await main.locator('a').evaluateAll((links) => links.map((link) => link.getAttribute('href')))).toEqual([])
  expect(await main.locator('.sourced-text img, .sourced-text b, .sourced-text strong, .sourced-text em, .sourced-text code').count()).toBe(0)

  // Exactly the original characters: nothing added (such as invisible joiners) and nothing lost.
  const shown = await main.locator('.sourced-text').evaluateAll((blocks) => blocks.map((block) => block.textContent))
  for (const field of FIELDS) expect(shown).toContain(`${field} ${HOSTILE}`)
  expect(shown).toContain(`Why: REASON ${HOSTILE}`)
  expect(shown).toContain(`Searched for: “SEARCHED ${HOSTILE}”`)
  // Spaces and line breaks survive, both in the page and in what a reader selects and copies.
  const spacing = 'SPACING  two spaces,   three spaces,\n  an indented second line\n\nand a blank line before this one'
  expect(shown).toContain(spacing)
  const block = main.locator('.sourced-text', { hasText: 'SPACING' })
  expect(await block.innerText()).toBe(spacing)
  await block.selectText()
  expect(await page.evaluate(() => window.getSelection()?.toString())).toBe(spacing)
  // Labels that Streamlit parses as Markdown are fixed text.
  await expect(main.locator('summary', { hasText: 'Source 1 · page 6' })).toHaveCount(1)
  await expect(main.locator('summary', { hasText: 'Passage 1 · page 2' })).toHaveCount(1)
})
