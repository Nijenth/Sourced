import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { WidgetSettings, type WidgetRecord } from './WidgetSettings'

const record: WidgetRecord = {
  id: 'sample-id', name: 'People help', site_key: 'pk_sample', access_mode: 'public',
  allowed_origins: ['https://people.brightleaf.example'], document_ids: ['handbook'],
  provider: 'groq', judge_enabled: false, title: 'Ask the handbook', greeting: 'Hello!',
  brand_color: '#1F5C46', fallback_text: 'Ask the People team.',
  script_url: 'https://chat.example.com/widget-assets/1.0.0/widget.js',
  integrity: 'sha384-fixture', hosted_url: 'https://chat.example.com/c/pk_sample',
}

it('edits public settings, keeps signed-in mode disabled, and saves only editable fields', async () => {
  const fetch = vi.fn(async (path: string, options?: RequestInit) => {
    if (options?.method === 'PATCH') return new Response(JSON.stringify({ ...record, ...JSON.parse(String(options.body)) }))
    const fixtures: Record<string, unknown> = {
      '/api/v1/widgets': [record],
      '/api/v1/documents': [
        { id: 'handbook', filename: 'Handbook.pdf', status: 'ready' },
        { id: 'draft', filename: 'Draft.pdf', status: 'processing' },
      ],
      '/api/v1/providers': [{ name: 'groq', label: 'GroqCloud', ready: true }],
    }
    return new Response(JSON.stringify(fixtures[path]))
  })
  vi.stubGlobal('fetch', fetch)
  const user = userEvent.setup()
  render(<WidgetSettings />)
  expect(await screen.findByRole('radio', { name: /Only people signed in/ })).toBeDisabled()
  expect(screen.getByText('Coming soon')).toBeVisible()
  expect(screen.getByRole('checkbox', { name: /Draft.pdf/ })).toBeDisabled()
  expect(screen.getByLabelText('Widget embed code')).toHaveTextContent('integrity="sha384-fixture"')
  await user.type(screen.getByLabelText('Add a website'), 'help.brightleaf.example')
  await user.click(screen.getByRole('button', { name: 'Add website' }))
  const title = screen.getByLabelText('Title')
  await user.clear(title)
  await user.type(title, 'Ask Brightleaf')
  expect(within(screen.getByRole('region', { name: 'Preview' })).getByText('Ask Brightleaf')).toBeVisible()
  await user.click(screen.getByRole('switch', { name: /Accuracy check/ }))
  await user.click(screen.getByRole('button', { name: 'Save changes' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Widget saved.')
  const call = fetch.mock.calls.find(([, options]) => options?.method === 'PATCH')
  expect(call?.[0]).toBe('/api/v1/widgets/sample-id')
  const saved = JSON.parse(String(call?.[1]?.body))
  expect(saved).toMatchObject({ title: 'Ask Brightleaf', access_mode: 'public', judge_enabled: true,
    allowed_origins: ['https://people.brightleaf.example', 'https://help.brightleaf.example'], document_ids: ['handbook'] })
  expect(saved).not.toHaveProperty('site_key')
})
