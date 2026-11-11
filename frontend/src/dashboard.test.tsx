import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { AuthForm } from './App'
import { AnswerCard, Ask } from './Ask'
import { Documents } from './Documents'
import { Usage } from './Usage'
import type { Answer, Citation } from './api'

const citation: Citation = {
  number: 1, document_id: '76f13c80-b1a5-4250-afd4-95c044f8be35', source: 'Handbook.pdf',
  page: 6, quote: 'Full-time employees get 24 days.', verified: true, preview_url: 'https://example.com/untrusted',
}
const answer: Answer = {
  status: 'answered', text: 'Full-time employees get 24 days [1]. <img src=x>', partial: true,
  searched_for: 'How much annual leave is there?', reason: '', citations: [citation], passages: [],
  judgement: { verdict: 'supported', judge: 'GroqCloud', problems: [] },
}
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })

describe('Sourced dashboard', () => {
  it('submits signup and reports a sign-in error', async () => {
    const onSuccess = vi.fn()
    const member = { workspace_id: 'test', workspace_name: 'Brightleaf Studio', email: 'admin@example.com' }
    const fetch = vi.fn().mockResolvedValueOnce(json(member, 201))
      .mockResolvedValueOnce(json({ detail: 'Email or password is incorrect.' }, 401))
    vi.stubGlobal('fetch', fetch)
    const user = userEvent.setup()
    const view = render(<MemoryRouter><AuthForm signup onSuccess={onSuccess} /></MemoryRouter>)
    await user.type(screen.getByLabelText('Workspace name'), member.workspace_name)
    await user.type(screen.getByLabelText('Email address'), member.email)
    await user.type(screen.getByLabelText('Password'), 'fictional passphrase')
    await user.click(screen.getByRole('button', { name: 'Create workspace' }))
    expect(onSuccess).toHaveBeenCalledWith(member)
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/auth/signup')
    view.rerender(<MemoryRouter><AuthForm key="signin" signup={false} onSuccess={onSuccess} /></MemoryRouter>)
    await user.type(screen.getByLabelText('Email address'), member.email)
    await user.type(screen.getByLabelText('Password'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Email or password is incorrect.')
  })

  it('shows upload disclosure, processing and failure, then confirms deletion', async () => {
    const user = userEvent.setup()
    let rows = [
      { id: 'ready', filename: 'Handbook.pdf', status: 'ready', progress: 100, pages: 16 },
      { id: 'pending', filename: 'Policy.pdf', status: 'processing', progress: 60, pages: 10 },
      { id: 'failed', filename: '<b>Scan.pdf</b>', status: 'failed', error: 'No selectable text.', pages: 1 },
    ].map((row) => ({ ...row, created_at: '2026-11-02T10:00:00Z' }))
    const fetch = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith('/settings')) return json({ max_bytes: 10485760, max_pages: 80, embedding_provider: 'OpenAI' })
      if (init.method === 'DELETE') {
        rows = rows.filter((row) => row.id !== 'ready')
        return new Response(null, { status: 204 })
      }
      return json(rows)
    })
    vi.stubGlobal('fetch', fetch)
    render(<Documents />)
    expect(await screen.findByText(/Up to 10 MB and 80 pages/)).toHaveTextContent('OpenAI')
    expect(screen.getByRole('progressbar', { name: 'Processing Policy.pdf' })).toHaveValue(60)
    expect(screen.getByText('Couldn’t read')).toBeVisible()
    expect(screen.getByText('<b>Scan.pdf</b>')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Delete Handbook.pdf' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus()
    await user.click(within(dialog).getByRole('button', { name: 'Delete document' }))
    expect(await screen.findByText('Handbook.pdf deleted.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Choose files' })).toHaveFocus()
  })

  it('submits a question and opens a safe source URL with keyboard focus', async () => {
    const fetch = vi.fn(async (url: string) => {
      if (url.endsWith('/providers')) return json([{ name: 'offline', label: 'Offline test mode', ready: true }])
      if (url.endsWith('/chat')) return json(answer)
      return new Response(null, { status: 404 })
    })
    vi.stubGlobal('fetch', fetch)
    render(<Ask />)
    const user = userEvent.setup()
    await screen.findByText('Ready')
    const input = screen.getByRole('textbox', { name: 'Question' })
    await user.type(input, 'How much leave?{Enter}')
    expect(await screen.findByText(/every claim matches/)).toBeVisible()
    expect(screen.getByText('The documents answer part of this question.')).toBeVisible()
    expect(screen.getByText(/<img src=x>/)).toBeVisible()
    expect(input).toHaveFocus()
    const source = screen.getByRole('button', { name: 'Source 1' })
    await user.click(source)
    expect(screen.getByRole('heading', { name: 'Handbook.pdf, page 6' })).toHaveFocus()
    expect(screen.getByRole('link', { name: /Open the full PDF/ })).toHaveAttribute('href',
      '/api/v1/documents/' + citation.document_id + '/pdf#page=6')
    await user.keyboard('{Escape}')
    expect(source).toHaveFocus()
  })

  it('renders blocked and not-found answers, including a failed accuracy check', () => {
    const blocked: Answer = {
      ...answer, status: 'unverified', text: 'Private rejected text', partial: false,
      judgement: { verdict: 'unsupported', judge: 'GroqCloud', problems: ['Private rejected text'] },
      passages: [{ document_id: citation.document_id, source: 'Handbook.pdf', page: 6, text: 'Actual passage' }],
    }
    const open = vi.fn()
    const view = render(<AnswerCard answer={blocked} open={open} />)
    expect(screen.getByText('Couldn’t verify this answer')).toBeVisible()
    expect(screen.getByText(/do not support every claim/)).toBeVisible()
    expect(screen.getByText('Actual passage')).toBeVisible()
    expect(screen.queryByText('Private rejected text')).not.toBeInTheDocument()
    view.rerender(<AnswerCard answer={{ ...answer, status: 'not_found', text: 'I couldn’t find that.',
      partial: false, searched_for: '', judgement: null }} open={open} />)
    expect(screen.getByText('The documents don’t cover this question.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Source 1' })).not.toBeInTheDocument()
  })

  it('totals usage and names the channel', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json([
      { day: '2026-11-02', channel: 'ask_test', questions: 5, answered: 3, not_found: 1, blocked: 1 },
      { day: '2026-11-01', channel: 'ask_test', questions: 2, answered: 2, not_found: 0, blocked: 0 },
    ])))
    render(<Usage />)
    expect(await screen.findByText('7')).toBeVisible()
    expect(screen.getAllByText('Ask (test)')).toHaveLength(2)
    expect(screen.getByRole('columnheader', { name: 'Blocked' })).toBeVisible()
  })
})
