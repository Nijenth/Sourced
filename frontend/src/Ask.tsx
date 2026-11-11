import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { ArrowRight, ExternalLink, MessageCircle, ShieldCheck, X } from 'lucide-react'
import { citationPaths, errorMessage, highlightedPage, post, type Answer, type Citation, type Provider } from './api'
import { ErrorMessage, Heading, ResourceError, useResource } from './common'

type OpenSource = (citation: Citation, trigger: HTMLButtonElement) => void

function inlineCitations(text: string, citations: Citation[], open: OpenSource): ReactNode[] {
  return text.split(/(\[\d{1,2}(?:\s*,\s*\d{1,2})*\])/g).flatMap((part, index) => {
    if (index % 2 === 0) return [part]
    return part.slice(1, -1).split(',').map((number, position) => {
      const citation = citations.find((item) => item.number === Number(number))
      return citation ? <button key={index + '-' + position} className="inline-citation" type="button"
        aria-label={'Source ' + citation.number} onClick={(event) => open(citation, event.currentTarget)}>
        <span>{citation.number}</span>
      </button> : '[' + number.trim() + ']'
    })
  })
}

const judgementLabel = {
  supported: 'every claim matches the passages',
  partial: 'some claims need review',
  unsupported: 'the passages do not support every claim',
  error: 'could not complete',
}

export function AnswerCard({ answer, open, selected }: { answer: Answer; open: OpenSource; selected?: Citation }) {
  const blocked = answer.status === 'unverified' || answer.status === 'invalid_output'
  const judgement = answer.judgement
  return <article className="card answer" aria-label="Answer">
    {blocked ? <>
      <strong className="warning-text">Couldn’t verify this answer</strong>
      <p>I couldn’t verify an answer against the documents, so I’m not showing one.</p>
      {answer.reason && <p className="muted">{answer.reason}</p>}
    </> : <p className="answer-text">{inlineCitations(answer.text, answer.status === 'answered' ? answer.citations : [], open)}</p>}
    {answer.status === 'not_found' && <p className="muted">The documents don’t cover this question.</p>}
    {answer.partial && !blocked && <p className="notice pending">The documents answer part of this question.</p>}
    {answer.searched_for && <p className="muted"><strong>Searched for</strong> {answer.searched_for}</p>}
    {judgement && <div>
      <span className={'badge ' + (judgement.verdict === 'supported' ? 'success' : 'warning')}>
        <ShieldCheck size={16} aria-hidden="true" />Accuracy check ({judgement.judge}){': '}{judgementLabel[judgement.verdict]}
      </span>
      {!blocked && judgement.problems.length > 0 && <ul className="judge-problems">{judgement.problems.map((problem, index) => <li key={index}>{problem}</li>)}</ul>}
    </div>}
    {answer.status === 'answered' && answer.citations.length > 0 && <div className="citations">
      {answer.citations.map((citation) => <button key={citation.number} type="button"
        className={'citation-chip' + (selected === citation ? ' selected' : '')}
        aria-pressed={selected === citation} onClick={(event) => open(citation, event.currentTarget)}>
        [{citation.number}] {citation.source} · p. {citation.page}
      </button>)}
    </div>}
    {blocked && answer.passages.length > 0 && <section className="passages" aria-label="Closest passages">
      <h2>Closest passages</h2>
      {answer.passages.map((passage, index) => <div key={index} className="passage">
        <strong>{passage.source} · p. {passage.page}</strong><p>{passage.text}</p>
      </div>)}
    </section>}
  </article>
}

function SourcePanel({ citation, close }: { citation: Citation; close: () => void }) {
  const heading = useRef<HTMLHeadingElement>(null)
  const [image, setImage] = useState('')
  const [broken, setBroken] = useState(false)
  const paths = citationPaths(citation)
  const highlight = paths?.highlight

  useEffect(() => { heading.current?.focus() }, [])
  useEffect(() => {
    if (!highlight || !citation.verified || !citation.quote || citation.quote.length > 2000) return
    const controller = new AbortController()
    let url = ''
    highlightedPage(highlight, citation.quote, controller.signal).then((blob) => {
      if (!controller.signal.aborted) {
        url = URL.createObjectURL(blob)
        setImage(url)
      }
    }).catch(() => { /* The original page preview remains available. */ })
    return () => {
      controller.abort()
      if (url) URL.revokeObjectURL(url)
    }
  }, [highlight, citation.quote, citation.verified])

  return <aside className="source-panel" aria-labelledby="source-title" onKeyDown={(event) => {
    if (event.key === 'Escape') close()
  }}>
    <header>
      <div><p className="eyebrow">Source {citation.number}{citation.verified ? ' · quote verified' : ' · source passage'}</p>
        <h2 id="source-title" ref={heading} tabIndex={-1}>{citation.source}, page {citation.page}</h2></div>
      <button className="icon-button quiet" type="button" aria-label="Close source" onClick={close}><X size={18} aria-hidden="true" /></button>
    </header>
    <blockquote>{citation.quote}</blockquote>
    {paths && !broken ? <figure className="preview">
      <img src={image || paths.preview} alt={'Page ' + citation.page + ' preview'} onError={() => {
        if (image) setImage('')
        else setBroken(true)
      }} />
    </figure> : <p className="muted">Page preview unavailable.</p>}
    {paths && <a className="pdf-link" href={paths.pdf} target="_blank" rel="noopener noreferrer">
      Open the full PDF at page {citation.page}<ExternalLink size={16} aria-hidden="true" />
    </a>}
  </aside>
}

interface Turn { question: string; answer: Answer }

export function Ask() {
  const providers = useResource<Provider[]>('/providers')
  const [provider, setProvider] = useState('')
  const [question, setQuestion] = useState('')
  const [pending, setPending] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [error, setError] = useState('')
  const [announcement, setAnnouncement] = useState('')
  const [source, setSource] = useState<{ citation: Citation; revision: number } | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const sourceTrigger = useRef<HTMLButtonElement>(null)
  const selectedProvider = providers.data?.find((item) => item.name === provider && item.ready)?.name
    ?? providers.data?.find((item) => item.ready)?.name ?? ''

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const text = question.trim()
    if (!text || pending || !selectedProvider) return
    setPending(text)
    setQuestion('')
    setError('')
    setAnnouncement('Searching your documents…')
    input.current?.focus()
    const history = turns.slice(-3).flatMap((turn) => [
      { role: 'user', content: turn.question.slice(0, 600) },
      { role: 'assistant', content: (turn.answer.status === 'answered' ? turn.answer.text : 'No answer was shown.').slice(0, 600) },
    ])
    try {
      const answer = await post<Answer>('/chat', { question: text, provider: selectedProvider, history })
      setTurns((previous) => [...previous, { question: text, answer }])
      setAnnouncement(answer.status === 'answered' ? 'Answer received.' :
        answer.status === 'not_found' ? 'No answer found in the documents.' : 'The answer could not be verified.')
    } catch (err) {
      setQuestion((current) => current || text)
      setError(errorMessage(err))
      setAnnouncement('')
    } finally {
      setPending('')
    }
  }

  function open(citation: Citation, trigger: HTMLButtonElement) {
    sourceTrigger.current = trigger
    setSource((current) => ({ citation, revision: (current?.revision ?? 0) + 1 }))
  }

  function close() {
    setSource(null)
    sourceTrigger.current?.focus()
  }

  return <div className={'ask-layout' + (source ? ' has-source' : '')}>
    <section className="conversation" aria-label="Conversation">
      <Heading title="Ask" action={<div className="model-picker">
        <label htmlFor="model">Answer model</label>
        <select id="model" value={selectedProvider} onChange={(event) => setProvider(event.target.value)} disabled={!providers.data || !!pending}>
          {!selectedProvider && <option value="">{providers.data ? 'No models ready' : 'Loading models…'}</option>}
          {providers.data?.map((item) => <option key={item.name} value={item.name} disabled={!item.ready}>
            {item.label}{item.ready ? '' : ' · unavailable'}
          </option>)}
        </select>
        {selectedProvider && <span className="badge success"><span className="status-dot" />Ready</span>}
      </div>} />
      <ResourceError error={providers.error} refresh={providers.refresh} />
      {providers.data && !selectedProvider && <ErrorMessage message="Configure an answer model to start asking questions." />}
      {!turns.length && !pending && <div className="card empty ask-empty">
        <MessageCircle size={28} aria-hidden="true" /><strong>Ask about your documents</strong>
        <p>Test an answer and open its citations to check the sources.</p>
      </div>}
      {turns.map((turn, index) => <div className="turn" key={index}>
        <p className="question">{turn.question}</p>
        <AnswerCard answer={turn.answer} open={open} selected={source?.citation} />
      </div>)}
      {pending && <div className="turn"><p className="question">{pending}</p><p className="card answer muted">Searching your documents…</p></div>}
      <ErrorMessage message={error} />
      <div role="status" className="sr-only">{announcement}</div>
      <form className="composer" aria-label="Ask a question" onSubmit={submit}>
        <label htmlFor="question" className="sr-only">Question</label>
        <input ref={input} id="question" type="text" placeholder="Ask about your documents…" value={question} maxLength={2000}
          onChange={(event) => setQuestion(event.target.value)} />
        <button type="submit" className="primary icon-button" aria-label="Send" disabled={!!pending || !question.trim() || !selectedProvider}>
          <ArrowRight size={21} aria-hidden="true" />
        </button>
      </form>
    </section>
    {source && <SourcePanel key={source.revision} citation={source.citation} close={close} />}
  </div>
}
