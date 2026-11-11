import { useEffect, useRef, useState } from 'react'
import { AlertCircle, Check, FileText, LoaderCircle, Trash2, Upload, X } from 'lucide-react'
import { api, documentPath, errorMessage, type DocumentRow, type UploadSettings } from './api'
import { ErrorMessage, Heading, ResourceError, useResource } from './common'

const dateFormat = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
const timeFormat = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit' })

function addedAt(value: string) {
  const date = new Date(value)
  return date.toDateString() === new Date().toDateString() ? 'Today, ' + timeFormat.format(date) : dateFormat.format(date)
}

export function DocumentStatus({ row }: { row: DocumentRow }) {
  if (row.status === 'ready') return <span className="badge success"><Check size={13} aria-hidden="true" />Ready</span>
  if (row.status === 'failed') return <span className="badge warning"><AlertCircle size={13} aria-hidden="true" />Couldn’t read</span>
  return <span className="badge pending"><LoaderCircle size={13} aria-hidden="true" />{row.status === 'queued' ? 'Queued' : 'Processing'}</span>
}

export function Documents() {
  const { data: documents, error, refresh } = useResource<DocumentRow[]>('/documents')
  const settings = useResource<UploadSettings>('/documents/settings')
  const [uploading, setUploading] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [status, setStatus] = useState('')
  const [selection, setSelection] = useState<DocumentRow | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const chooser = useRef<HTMLInputElement>(null)
  const chooseButton = useRef<HTMLButtonElement>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const cancelButton = useRef<HTMLButtonElement>(null)
  const opener = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!documents?.some((row) => row.status === 'queued' || row.status === 'processing')) return
    const timer = window.setTimeout(refresh, 2000)
    return () => window.clearTimeout(timer)
  }, [documents, error, refresh])

  useEffect(() => {
    if (selection) {
      dialog.current?.showModal()
      cancelButton.current?.focus()
    }
  }, [selection])

  async function upload(files: File[]) {
    if (!files.length || uploading) return
    setUploading(true)
    setUploadError('')
    setStatus('')
    const errors: string[] = []
    let queued = 0
    for (const file of files) {
      try {
        if (!file.name.toLowerCase().endsWith('.pdf')) {
          errors.push(file.name + ' isn’t a PDF.')
          continue
        }
        if (settings.data && file.size > settings.data.max_bytes) {
          errors.push(file.name + ' exceeds the upload limit.')
          continue
        }
        const form = new FormData()
        form.append('file', file)
        await api('/documents', { method: 'POST', body: form })
        queued++
      } catch (err) {
        errors.push(file.name + ' — ' + errorMessage(err))
      }
    }
    setUploading(false)
    setUploadError(errors.join(' '))
    if (queued) {
      setStatus(queued === 1 ? '1 PDF queued for processing.' : queued + ' PDFs queued for processing.')
      refresh()
    }
  }

  function closeDialog(removed = false) {
    dialog.current?.close()
    setSelection(null)
    if (removed) chooseButton.current?.focus()
    else opener.current?.focus()
  }

  async function remove() {
    if (!selection || deleting) return
    setDeleting(true)
    setDeleteError('')
    try {
      await api(documentPath(selection.id), { method: 'DELETE' })
      setStatus(selection.filename + ' deleted.')
      closeDialog(true)
      refresh()
    } catch (err) {
      setDeleteError(errorMessage(err))
    } finally {
      setDeleting(false)
    }
  }

  const pages = documents?.reduce((total, row) => total + row.pages, 0)
  const limits = settings.data
  return <div className="page">
    <Heading title="Documents" action={documents && <span className="count-pill">
      {documents.length} {documents.length === 1 ? 'document' : 'documents'} · {pages} {pages === 1 ? 'page' : 'pages'}
    </span>}>Answers come only from the documents listed here. Each answer cites its page.</Heading>

    <section aria-label="Upload documents" className={'dropzone' + (dragging ? ' drag-active' : '')}
      onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
      onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false) }}
      onDrop={(event) => { event.preventDefault(); setDragging(false); void upload(Array.from(event.dataTransfer.files)) }}>
      <span className="upload-icon"><Upload size={26} aria-hidden="true" /></span>
      <div className="upload-copy">
        <strong>Drop PDFs here, or choose files</strong>
        {limits && <p>Up to {limits.max_bytes / 1024 ** 2} MB and {limits.max_pages} pages each.{' '}
          {limits.embedding_provider ? 'Text is indexed by your search provider (' + limits.embedding_provider + '); ' : 'A search provider must be configured to index text. '}
          Only the passages relevant to a question go to the answer model.</p>}
        {!limits && !settings.error && <p>Loading upload limits…</p>}
        <ResourceError error={settings.error} refresh={settings.refresh} />
      </div>
      <input hidden ref={chooser} type="file" accept="application/pdf,.pdf" multiple onChange={(event) => {
        const files = Array.from(event.currentTarget.files ?? [])
        event.currentTarget.value = ''
        void upload(files)
      }} />
      <button ref={chooseButton} type="button" className="primary" aria-disabled={uploading}
        onClick={() => { if (!uploading) chooser.current?.click() }}>{uploading ? 'Uploading…' : 'Choose files'}</button>
    </section>
    <ErrorMessage message={uploadError} />
    <div role="status" className="sr-only">{status}</div>
    <ResourceError error={error} refresh={refresh} />

    {!documents && !error && <p role="status">Loading documents…</p>}
    {documents && documents.length === 0 && <div className="card empty">
      <FileText size={28} aria-hidden="true" /><strong>No documents yet</strong><p>Upload a PDF to start answering questions.</p>
    </div>}
    {!!documents?.length && <div className="card table-scroll" tabIndex={0} role="region" aria-label="Workspace documents">
      <table className="documents-table">
        <thead><tr><th scope="col">Document</th><th scope="col">Pages</th><th scope="col">Status</th>
          <th scope="col">Added</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>{documents.map((row) => {
          const inProgress = row.status === 'queued' || row.status === 'processing'
          return <tr key={row.id}>
            <th scope="row"><span className="filename">{row.filename}</span>
              {row.status === 'processing' && <span className="row-detail">Processing… {row.progress}%
                <progress aria-label={'Processing ' + row.filename} value={row.progress} max={100} /></span>}
              {row.status === 'queued' && <span className="row-detail">Waiting to be processed.</span>}
              {row.status === 'failed' && <span className="row-detail warning-text">{row.error || 'The PDF could not be read.'}</span>}
            </th>
            <td>{row.pages}</td><td><DocumentStatus row={row} /></td>
            <td><time dateTime={row.created_at}>{addedAt(row.created_at)}</time></td>
            <td className="row-action"><button type="button" className="icon-button quiet"
              aria-label={(inProgress ? 'Cancel ' : 'Delete ') + row.filename} onClick={(event) => {
                opener.current = event.currentTarget
                setDeleteError('')
                setSelection(row)
              }}>{inProgress ? <X size={18} aria-hidden="true" /> : <Trash2 size={18} aria-hidden="true" />}</button></td>
          </tr>
        })}</tbody>
      </table>
    </div>}
    {!!documents?.length && <p className="muted footer-note">Deleting a document removes the file, its passages and its search index. It can’t be undone.</p>}

    <dialog ref={dialog} className="dialog card" aria-labelledby="delete-title" aria-describedby="delete-description"
      onCancel={(event) => { event.preventDefault(); if (!deleting) closeDialog() }}>
      <h2 id="delete-title">Delete document?</h2>
      <p id="delete-description"><strong>{selection?.filename}</strong> and its search index will be removed. This can’t be undone.</p>
      <ErrorMessage message={deleteError} />
      <div className="dialog-actions">
        <button ref={cancelButton} className="secondary" type="button" onClick={() => closeDialog()} disabled={deleting}>Cancel</button>
        <button className="danger" type="button" onClick={() => void remove()} disabled={deleting}>{deleting ? 'Deleting…' : 'Delete document'}</button>
      </div>
    </dialog>
  </div>
}
