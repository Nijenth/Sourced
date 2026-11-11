import { resolve } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'

async function tabTo(page: Page, locator: Locator) {
  await expect(locator).toBeVisible()
  for (let i = 0; i < 32; i++) {
    if (await locator.evaluate((node) => document.activeElement === node)) return
    await page.keyboard.press('Tab')
  }
  await expect(locator).toBeFocused()
}

async function activate(page: Page, locator: Locator) {
  await tabTo(page, locator)
  await page.keyboard.press('Enter')
}

async function type(page: Page, name: string, value: string) {
  await tabTo(page, page.getByLabel(name, { exact: true }))
  await page.keyboard.type(value)
}

test('keyboard-only workspace walkthrough with the API', async ({ page }) => {
  test.skip(!process.env.SOURCED_E2E_LIVE, 'Start the disposable API with python -m frontend.e2e.run_live.')
  const email = 'keyboard@example.com'
  const password = 'synthetic workspace passphrase'
  const pdf = resolve('../data/sample/Brightleaf_Studio_Employee_Handbook.pdf')
  await page.goto('/sign-in')
  await activate(page, page.getByRole('link', { name: 'Create a workspace' }))
  await type(page, 'Workspace name', 'Brightleaf Studio')
  await type(page, 'Email address', email)
  await type(page, 'Password', password)
  await activate(page, page.getByRole('button', { name: 'Create workspace' }))
  await expect(page.getByRole('heading', { name: 'Documents' })).toBeFocused()

  const chooser = page.waitForEvent('filechooser')
  await activate(page, page.getByRole('button', { name: 'Choose files' }))
  await (await chooser).setFiles(pdf)
  await expect(page.getByText('Ready', { exact: true })).toBeVisible({ timeout: 30_000 })
  await activate(page, page.getByRole('link', { name: 'Ask (test)' }))
  const question = page.getByRole('textbox', { name: 'Question' })
  await type(page, 'Question', 'How many days of annual leave do I get?')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('article', { name: 'Answer' })).toContainText('24 days')
  await expect(question).toBeFocused()
  const citation = page.getByRole('button', { name: 'Source 1' })
  await activate(page, citation)
  await expect(page.getByRole('heading', { name: /Handbook.pdf, page 6/ })).toBeFocused()
  await expect(page.getByRole('img', { name: 'Page 6 preview' })).toBeVisible()
  await expect(page.getByRole('img', { name: 'Page 6 preview' })).toHaveJSProperty('complete', true)
  await page.keyboard.press('Escape')
  await expect(citation).toBeFocused()
  await activate(page, page.getByRole('link', { name: 'Usage' }))
  await expect(page.getByRole('row', { name: /Ask \(test\)/ })).toContainText('1')

  await activate(page, page.getByRole('link', { name: 'Documents' }))
  const remove = page.getByRole('button', { name: /Delete Brightleaf_Studio/ })
  await activate(page, remove)
  await expect(page.getByRole('button', { name: 'Cancel' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(remove).toBeFocused()
  await activate(page, remove)
  await activate(page, page.getByRole('button', { name: 'Delete document' }))
  await expect(page.getByText('No documents yet')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Choose files' })).toBeFocused()

  await activate(page, page.getByRole('button', { name: 'Sign out' }))
  await type(page, 'Email address', email)
  await type(page, 'Password', password)
  await activate(page, page.getByRole('button', { name: 'Sign in', exact: true }))
  await expect(page.getByRole('heading', { name: 'Documents' })).toBeFocused()

  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390)
})
