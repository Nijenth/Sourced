import { BarChart3 } from 'lucide-react'
import type { UsageRow } from './api'
import { Heading, ResourceError, useResource } from './common'

const columns = [
  { key: 'questions', label: 'Questions' },
  { key: 'answered', label: 'Answered' },
  { key: 'not_found', label: 'Not found' },
  { key: 'blocked', label: 'Blocked' },
] as const
const format = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })

export function Usage() {
  const { data: rows, error, refresh } = useResource<UsageRow[]>('/usage?days=30')
  return <div className="page usage-page">
    <Heading title="Usage" action={<span className="count-pill">Last 30 days · UTC</span>}>
      See how many questions your workspace answers, can’t find, or blocks.
    </Heading>
    <ResourceError error={error} refresh={refresh} />
    {!rows && !error && <p role="status">Loading usage…</p>}
    {rows && <div className="stats">
      {columns.map(({ key, label }) => <div className="card stat" key={key}>
        <span>{label}</span><strong>{rows.reduce((sum, row) => sum + row[key], 0).toLocaleString()}</strong>
      </div>)}
    </div>}
    {rows && rows.length === 0 && <div className="card empty">
      <BarChart3 size={28} aria-hidden="true" /><strong>No usage yet</strong><p>Ask a test question to see activity here.</p>
    </div>}
    {!!rows?.length && <div className="card table-scroll" tabIndex={0} role="region" aria-label="Daily usage by channel">
      <table className="usage-table">
        <thead><tr><th scope="col">Date</th><th scope="col">Channel</th>
          {columns.map(({ key, label }) => <th key={key} scope="col">{label}</th>)}</tr></thead>
        <tbody>{rows.map((row) => <tr key={row.day + row.channel}>
          <th scope="row"><time dateTime={row.day}>{format.format(new Date(row.day))}</time></th>
          <td>{row.channel === 'ask_test' ? 'Ask (test)' : row.channel}</td>
          {columns.map(({ key }) => <td key={key}>{row[key].toLocaleString()}</td>)}
        </tr>)}</tbody>
      </table>
    </div>}
  </div>
}
