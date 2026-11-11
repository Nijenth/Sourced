import { useState, type CSSProperties, type FormEvent } from 'react'
import { Copy, MessageCircle, Plus } from 'lucide-react'
import { api, errorMessage, post, type DocumentRow, type Provider } from './api'
import { ErrorMessage, Heading, ResourceError, useResource } from './common'
import { DocumentStatus } from './Documents'

export interface WidgetFields {
  name: string
  access_mode: 'public'
  allowed_origins: string[]
  document_ids: string[]
  provider: string
  judge_enabled: boolean
  title: string
  greeting: string
  brand_color: string
  fallback_text: string
}

export interface WidgetRecord extends WidgetFields {
  id: string
  site_key: string
  script_url: string | null
  integrity: string | null
  hosted_url: string | null
}

const palette = [
  ['Forest green', '#1F5C46'], ['Blue', '#2445A8'], ['Plum', '#7A2E8E'], ['Rust', '#B4410F'],
]

function defaults(providers: Provider[]): WidgetFields {
  return {
    name: 'Website widget', title: 'Ask the handbook', greeting: 'Hi! What would you like to know?',
    access_mode: 'public', brand_color: '#1F5C46', fallback_text: 'Please contact our team for more help.',
    allowed_origins: [], document_ids: [], provider: providers.find((provider) => provider.ready)?.name ?? '',
    judge_enabled: false,
  }
}

function fields(widget: WidgetRecord): WidgetFields {
  const { name, access_mode, allowed_origins, document_ids, provider, judge_enabled, title, greeting, brand_color, fallback_text } = widget
  return { name, access_mode, allowed_origins, document_ids, provider, judge_enabled, title, greeting, brand_color, fallback_text }
}

function embedCode(widget: WidgetRecord | null) {
  if (!widget?.script_url || !widget.integrity) return ''
  return '<script src="' + widget.script_url + '"\n  data-site-key="' + widget.site_key + '"\n  integrity="' +
    widget.integrity + '"\n  crossorigin="anonymous" async></script>'
}

function WidgetEditor({ widget, documents, providers, saved, removed }: {
  widget: WidgetRecord | null; documents: DocumentRow[]; providers: Provider[];
  saved: (widget: WidgetRecord) => void; removed: (id: string) => void;
}) {
  const [draft, setDraft] = useState<WidgetFields>(() => widget ? fields(widget) : defaults(providers))
  const [website, setWebsite] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const snippet = embedCode(widget)
  const path = widget ? '/widgets/' + encodeURIComponent(widget.id) : '/widgets'
  const validProvider = providers.some((provider) => provider.name === draft.provider && provider.ready)

  function change<K extends keyof WidgetFields>(key: K, value: WidgetFields[K]) {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    if (!validProvider) {
      setError('Choose an answer model that is ready.')
      return
    }
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await api<WidgetRecord>(path, { method: widget ? 'PATCH' : 'POST', body: JSON.stringify(draft) })
      setDraft(fields(result))
      saved(result)
      setNotice('Widget saved.')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  function addWebsite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    const input = website.trim()
    if (!input) return
    try {
      const url = new URL(input.includes('://') ? input : 'https://' + input)
      if (!['http:', 'https:'].includes(url.protocol) || url.pathname !== '/' || url.search || url.hash || url.username ||
          url.password || /[\\%*?#]/.test(input)) throw new Error()
      if (!draft.allowed_origins.includes(url.origin)) {
        if (draft.allowed_origins.length >= 25) throw new Error()
        change('allowed_origins', [...draft.allowed_origins, url.origin])
      }
      setWebsite('')
    } catch {
      setError('Add up to 25 website origins without paths, such as https://help.example.com.')
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(snippet)
      setNotice('Embed code copied.')
    } catch {
      setError('Select and copy the embed code manually.')
    }
  }

  async function manage(action: 'rotate' | 'delete') {
    if (!widget || busy) return
    const confirmed = window.confirm(action === 'rotate'
      ? 'Rotate the public site key? Existing snippets and hosted links will stop working.'
      : 'Delete this widget? Its embeds and hosted link will stop working.')
    if (!confirmed) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      if (action === 'rotate') {
        await post(path + '/rotate-keys', { site_key: true, secret_key: false })
        saved(await api<WidgetRecord>(path))
        setNotice('Site key rotated. Replace the embed code on your websites.')
      } else {
        await api(path, { method: 'DELETE' })
        removed(widget.id)
      }
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return <>
    <ErrorMessage message={error} />
    {notice && <p role="status" className="notice success">{notice}</p>}
    <div className="widget-settings-grid">
      <div className="widget-settings-stack">
        <section className="card widget-settings-card" aria-labelledby="install-title">
          <h2 id="install-title">Add it to your site</h2>
          <p className="muted">Paste this just before <code>&lt;/body&gt;</code> on every page that should show the chat.</p>
          {snippet ? <div className="widget-snippet">
            <pre tabIndex={0} aria-label="Widget embed code">{snippet}</pre>
            <button type="button" onClick={() => void copy()}><Copy size={15} aria-hidden="true" />Copy code</button>
          </div> : <p className="notice pending">{widget ? 'Set the public origin and preview signing secret on the server to generate an embed code.' : 'Save your widget to generate an embed code.'}</p>}
          <p className="muted">The site key is public. The website list controls browser embeds. Anyone with the key can call the public API directly.</p>
          {widget?.hosted_url && <a className="widget-hosted-link" href={widget.hosted_url} target="_blank" rel="noopener noreferrer">Open hosted chat</a>}
        </section>

        <section className="card widget-settings-card" aria-labelledby="access-title">
          <h2 id="access-title">Who can ask, and where</h2>
          <fieldset className="widget-fieldset">
            <legend>Access</legend>
            <label className="widget-option active">
              <input type="radio" name="access" checked readOnly />
              <span><strong>Anyone on these websites</strong><small>For public help pages. Treat the chosen documents as public.</small></span>
            </label>
            <label className="widget-option">
              <input type="radio" name="access" disabled />
              <span><strong>Only people signed in to your site or app</strong><small>For internal documents and signed-in users.</small></span>
              <span className="badge pending">Coming soon</span>
            </label>
          </fieldset>
          <div className="widget-settings-stack compact">
            <strong className="widget-label">Websites allowed to show it</strong>
            {draft.allowed_origins.length > 0 && <ul className="widget-origins">
              {draft.allowed_origins.map((origin) => <li key={origin}>
                <code>{origin}</code><button className="quiet" type="button" aria-label={'Remove ' + origin}
                  onClick={() => change('allowed_origins', draft.allowed_origins.filter((item) => item !== origin))}>Remove</button>
              </li>)}
            </ul>}
            <form className="widget-website-form" onSubmit={addWebsite}>
              <label className="sr-only" htmlFor="website-origin">Add a website</label>
              <input id="website-origin" placeholder="e.g. help.brightleaf.example" value={website} maxLength={300}
                onChange={(event) => setWebsite(event.target.value)} />
              <button className="secondary" type="submit">Add website</button>
            </form>
          </div>
        </section>

        <section className="card widget-settings-card" aria-labelledby="widget-documents">
          <h2 id="widget-documents">Documents it can answer from</h2>
          <p className="muted">Visitors can see quotes and cited pages from these documents. Leave out anything private.</p>
          {!documents.length && <p className="muted">Upload a PDF on the Documents page to add it here.</p>}
          <div className="widget-choices">
            {documents.map((document) => <label key={document.id} className="widget-document">
              <input type="checkbox" checked={draft.document_ids.includes(document.id)} disabled={document.status !== 'ready'}
                onChange={(event) => change('document_ids', event.target.checked
                  ? [...draft.document_ids, document.id] : draft.document_ids.filter((id) => id !== document.id))} />
              <span>{document.filename}</span>{document.status !== 'ready' && <DocumentStatus row={document} />}
            </label>)}
          </div>
        </section>

        <section className="card widget-settings-card" aria-labelledby="answer-settings">
          <h2 id="answer-settings">How it answers</h2>
          <fieldset className="widget-fieldset">
            <legend>Answer model</legend>
            {providers.map((provider) => <label className={'widget-option model-option' + (draft.provider === provider.name ? ' active' : '')} key={provider.name}>
              <input type="radio" name="widget-provider" disabled={!provider.ready} checked={draft.provider === provider.name}
                onChange={() => setDraft((current) => ({
                  ...current, provider: provider.name, judge_enabled: provider.name === 'offline' ? false : current.judge_enabled,
                }))} />
              <span>{provider.label}</span><span className={'badge ' + (provider.ready ? 'success' : 'pending')}>
                {provider.ready ? 'Ready' : 'Unavailable'}
              </span>
            </label>)}
          </fieldset>
          <label className="widget-option widget-accuracy">
            <input type="checkbox" role="switch" checked={draft.judge_enabled} disabled={draft.provider === 'offline'}
              onChange={(event) => change('judge_enabled', event.target.checked)} />
            <span><strong>Accuracy check</strong><small>{draft.provider === 'offline' ? 'Accuracy checking needs an AI answer model.' :
              'A second model re-reads every answer against the sources. Answers it can’t confirm are held back.'}</small></span>
          </label>
          <label className="widget-field">When the documents don’t cover a question
            <input form="save-widget" value={draft.fallback_text} maxLength={500} onChange={(event) => change('fallback_text', event.target.value)} />
          </label>
        </section>
      </div>

      <aside className="widget-settings-stack" aria-label="Look and preview">
        <section className="card widget-settings-card" aria-labelledby="widget-look">
          <h2 id="widget-look">Look</h2>
          <label className="widget-field">Title
            <input form="save-widget" required value={draft.title} maxLength={100} onChange={(event) => change('title', event.target.value)} />
          </label>
          <label className="widget-field">Greeting
            <input form="save-widget" value={draft.greeting} maxLength={500} onChange={(event) => change('greeting', event.target.value)} />
          </label>
          <label className="widget-field">Widget name
            <input form="save-widget" required value={draft.name} maxLength={100} onChange={(event) => change('name', event.target.value)} />
          </label>
          <fieldset className="widget-fieldset">
            <legend>Brand color</legend>
            <div className="widget-palette">
              {palette.map(([label, color]) => <button key={color} type="button" aria-label={label}
                aria-pressed={draft.brand_color.toUpperCase() === color} style={{ backgroundColor: color }}
                onClick={() => change('brand_color', color)} />)}
            </div>
          </fieldset>
        </section>
        <section className="widget-preview" aria-label="Preview" style={{ '--preview-brand': draft.brand_color } as CSSProperties}>
          <h2>Preview</h2>
          <div className="widget-preview-panel">
            <header><strong>{draft.title || 'Chat'}</strong><small>Answers from the documents, with sources</small></header>
            <div className="widget-preview-messages">
              {draft.greeting && <p>{draft.greeting}</p>}
              <p className="widget-preview-question">How many sick days do I get?</p>
              <p>You get 10 paid sick days a year <span>1</span></p>
            </div>
            <footer>Powered by Sourced</footer>
          </div>
          <div className="widget-preview-launcher" aria-hidden="true"><MessageCircle size={22} /></div>
        </section>
        {widget && <section className="card widget-settings-card" aria-labelledby="widget-keys">
          <h2 id="widget-keys">Manage widget</h2>
          <p className="muted">Rotation replaces the public key used by existing embeds and hosted links. Server secret keys belong only on your server.</p>
          <button type="button" className="secondary" disabled={busy} onClick={() => void manage('rotate')}>Rotate site key</button>
          <button type="button" className="quiet widget-delete" disabled={busy} onClick={() => void manage('delete')}>Delete widget</button>
        </section>}
      </aside>
    </div>
    <form id="save-widget" onSubmit={save} />
  </>
}

export function WidgetSettings() {
  const widgets = useResource<WidgetRecord[]>('/widgets')
  const documents = useResource<DocumentRow[]>('/documents')
  const providers = useResource<Provider[]>('/providers')
  const [changes, setChanges] = useState<Record<string, WidgetRecord | null>>({})
  const [selected, setSelected] = useState<string | null>(null)
  const combined = { ...Object.fromEntries((widgets.data ?? []).map((widget) => [widget.id, widget])), ...changes }
  const items = Object.values(combined).filter((widget): widget is WidgetRecord => !!widget)
  const active = selected === 'new' ? null : (selected && combined[selected]) || items[0] || null
  const ready = widgets.data && documents.data && providers.data

  return <div className="page widget-settings-page">
    <Heading title="Website widget" action={ready && <button className="primary" form="save-widget" type="submit">Save changes</button>}>
      A chat bubble for your own site. It answers only from the documents you pick below.
    </Heading>
    {[widgets, documents, providers].map((resource, index) => <ResourceError key={index} error={resource.error} refresh={resource.refresh} />)}
    {!ready && !widgets.error && !documents.error && !providers.error && <p role="status">Loading widget settings…</p>}
    {ready && <>
      {items.length > 0 && <div className="widget-switch">
        <label htmlFor="widget-select">Widget</label>
        <select id="widget-select" value={active?.id ?? 'new'} onChange={(event) => setSelected(event.target.value)}>
          {items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          {!active && <option value="new">New widget</option>}
        </select>
        <button className="secondary" type="button" onClick={() => setSelected('new')}><Plus size={16} aria-hidden="true" />New widget</button>
      </div>}
      <WidgetEditor key={active?.id ?? 'new'} widget={active} documents={documents.data!} providers={providers.data!}
        saved={(widget) => { setChanges((current) => ({ ...current, [widget.id]: widget })); setSelected(widget.id) }}
        removed={(id) => { setChanges((current) => ({ ...current, [id]: null })); setSelected(null) }} />
    </>}
  </div>
}
