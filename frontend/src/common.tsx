import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, errorMessage } from './api'

export function Brand() {
  return <div className="brand"><span className="brand-mark" aria-hidden="true"><i /></span>Sourced</div>
}

export function Heading({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus() }, [title])
  return <header className="page-heading">
    <div><h1 ref={heading} tabIndex={-1}>{title}</h1>{children && <p>{children}</p>}</div>
    {action}
  </header>
}

export function ErrorMessage({ message }: { message: string }) {
  return message ? <p role="alert" className="notice warning">{message}</p> : null
}

export function useResource<T>(path: string) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    api<T>(path, { signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) {
        setData(result)
        setError('')
      }
    }).catch((err) => {
      if (!controller.signal.aborted) setError(errorMessage(err))
    })
    return () => controller.abort()
  }, [path, revision])
  const refresh = useCallback(() => setRevision((value) => value + 1), [])
  return { data, error, refresh }
}

export function ResourceError({ error, refresh }: { error: string; refresh: () => void }) {
  if (!error) return null
  return <div className="retry"><ErrorMessage message={error} /><button type="button" className="secondary" onClick={refresh}>Try again</button></div>
}
