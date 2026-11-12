import { useEffect, useState, type FormEvent } from 'react'
import { BarChart3, CodeXml, FileText, LogOut, MessageCircle } from 'lucide-react'
import { Link, NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { api, ApiError, errorMessage, post, type Member } from './api'
import { Brand, ErrorMessage, Heading } from './common'
import { Documents } from './Documents'
import { Ask } from './Ask'
import { Usage } from './Usage'
import { WidgetSettings } from './WidgetSettings'

export function AuthForm({ signup, onSuccess }: { signup: boolean; onSuccess: (member: Member) => void }) {
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending) return
    const fields = Object.fromEntries(new FormData(event.currentTarget))
    setPending(true)
    setError('')
    try {
      onSuccess(await post<Member>(signup ? '/auth/signup' : '/auth/login', fields))
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setPending(false)
    }
  }

  return <main className="auth-shell">
    <section className="auth-card card" aria-label={signup ? 'Create your workspace' : 'Sign in to Sourced'}>
      <Brand />
      <Heading title={signup ? 'Create your workspace' : 'Sign in to Sourced'}>
        {signup ? 'Set up a private workspace for your documents.' : 'Access your workspace to manage documents and test answers.'}
      </Heading>
      <form onSubmit={submit} className="auth-form">
        {signup && <label>Workspace name<input name="workspace_name" autoComplete="organization" maxLength={120} required /></label>}
        <label>Email address<input name="email" type="email" autoComplete="username" required /></label>
        <label>Password<input name="password" type="password" autoComplete={signup ? 'new-password' : 'current-password'}
          minLength={signup ? 12 : 1} maxLength={128} aria-describedby={signup ? 'password-hint' : undefined} required /></label>
        {signup && <p className="field-hint" id="password-hint">Use at least 12 characters.</p>}
        <ErrorMessage message={error} />
        <button className="primary" type="submit" disabled={pending}>{pending ? 'Please wait…' : signup ? 'Create workspace' : 'Sign in'}</button>
      </form>
      <p className="auth-switch">{signup ? 'Already have an account?' : 'New to Sourced?'}{' '}
        <Link to={signup ? '/sign-in' : '/sign-up'}>{signup ? 'Sign in' : 'Create a workspace'}</Link>
      </p>
    </section>
  </main>
}

const navigation = [
  { href: '/documents', label: 'Documents', Icon: FileText },
  { href: '/ask', label: 'Ask (test)', Icon: MessageCircle },
  { href: '/widgets', label: 'Website widget', Icon: CodeXml },
  { href: '/usage', label: 'Usage', Icon: BarChart3 },
]

export function App() {
  const [member, setMember] = useState<Member | null | undefined>(undefined)
  const [error, setError] = useState('')
  const [checking, setChecking] = useState(0)
  const [signingOut, setSigningOut] = useState(false)
  const navigate = useNavigate()

  useEffect(() => {
    const controller = new AbortController()
    api<Member>('/auth/me', { signal: controller.signal }).then((session) => {
      if (!controller.signal.aborted) setMember(session)
    }).catch((err) => {
      if (controller.signal.aborted) return
      if (err instanceof ApiError && err.status === 401) setMember(null)
      else setError(errorMessage(err))
    })
    const expire = () => setMember(null)
    window.addEventListener('sourced:session-expired', expire)
    return () => {
      controller.abort()
      window.removeEventListener('sourced:session-expired', expire)
    }
  }, [checking])

  function signedIn(session: Member) {
    setMember(session)
    navigate('/documents', { replace: true })
  }

  async function signOut() {
    setSigningOut(true)
    setError('')
    try {
      await post('/auth/logout')
      setMember(null)
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 401)) setError(errorMessage(err))
    } finally {
      setSigningOut(false)
    }
  }

  if (member === undefined) return <main className="auth-shell">
    {error ? <div className="card loading"><ErrorMessage message={error} /><button className="secondary" onClick={() => {
      setError('')
      setChecking((value) => value + 1)
    }}>Try again</button></div> : <p role="status">Loading Sourced…</p>}
  </main>

  if (!member) return <Routes>
    <Route path="/sign-in" element={<AuthForm key="login" signup={false} onSuccess={signedIn} />} />
    <Route path="/sign-up" element={<AuthForm key="signup" signup onSuccess={signedIn} />} />
    <Route path="*" element={<Navigate to="/sign-in" replace />} />
  </Routes>

  return <div className="layout">
    <a href="#main" className="skip-link">Skip to content</a>
    <aside className="sidebar">
      <Link to="/documents" className="brand-link" aria-label="Sourced home"><Brand /></Link>
      <p className="workspace"><span>Workspace</span><strong>{member.workspace_name}</strong></p>
      <nav aria-label="Admin">
        {navigation.map(({ href, label, Icon }) => <NavLink key={href} to={href}>
          <Icon size={18} aria-hidden="true" />{label}
        </NavLink>)}
      </nav>
      <div className="account">
        <span className="account-email" title={member.email}>{member.email}</span>
        <button className="quiet sign-out" type="button" onClick={signOut} disabled={signingOut}>
          <LogOut size={18} aria-hidden="true" /> Sign out
        </button>
        <ErrorMessage message={error} />
      </div>
    </aside>
    <main id="main" tabIndex={-1}>
      <Routes>
        <Route path="/documents" element={<Documents />} />
        <Route path="/ask" element={<Ask />} />
        <Route path="/widgets" element={<WidgetSettings />} />
        <Route path="/usage" element={<Usage />} />
        <Route path="*" element={<Navigate to="/documents" replace />} />
      </Routes>
    </main>
  </div>
}
