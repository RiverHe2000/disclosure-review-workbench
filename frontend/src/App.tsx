import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api, ApiError, errorMessage, formatValue, humanize, post } from './api'
import { Icon } from './Icons'
import { PdfViewer } from './PdfViewer'
import { BANKS, METRICS } from './types'
import type { Bank, Candidate, Comparison, DocumentRecord, Fact, Health, Job, Method, MetricId, Observation, Stats } from './types'

const numberFormat = new Intl.NumberFormat('en-AU')
const documentLabel = (doc: DocumentRecord) => `${doc.bank} · ${doc.year}`
const isActive = (job: Job) => job.status === 'queued' || job.status === 'running'

function StatusBadge({ status }: { status: string }) {
  return <span className={`status-badge status-${status}`}><span/>{humanize(status)}</span>
}

function UploadDialog({ onClose, onUploaded }: { onClose: () => void; onUploaded: (doc: DocumentRecord) => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [bank, setBank] = useState<Bank>('CBA')
  const [year, setYear] = useState(String(new Date().getFullYear() - 1))
  const [source, setSource] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { dialog.current?.showModal(); return () => dialog.current?.close() }, [])
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!file || busy) return
    setBusy(true); setError('')
    const body = new FormData(); body.set('file', file); body.set('bank', bank); body.set('year', year); body.set('source_url', source.trim())
    try { onUploaded(await api<DocumentRecord>('/documents', { method: 'POST', body })) } catch (err) { setError(errorMessage(err)); setBusy(false) }
  }
  return <dialog className="upload-dialog" ref={dialog} onCancel={event => { event.preventDefault(); if (!busy) onClose() }}>
    <form onSubmit={event => void submit(event)}>
      <div className="dialog-heading"><div><span className="eyebrow">ADD TO YOUR LIBRARY</span><h2>Bring a report to the desk.</h2></div><button type="button" className="icon-button" onClick={onClose} disabled={busy} aria-label="Close upload dialog"><Icon name="close"/></button></div>
      <p className="muted">Upload an annual report. The original PDF is retained alongside its source details.</p>
      <input ref={input} type="file" accept=".pdf,application/pdf" className="file-input" aria-label="Choose annual report PDF" onChange={e => setFile(e.target.files?.[0] ?? null)}/>
      <button type="button" className={`dropzone ${file ? 'has-file' : ''}`} onClick={() => input.current?.click()} disabled={busy}><span className="upload-symbol"><Icon name={file ? 'file' : 'upload'} size={25}/></span><strong>{file ? file.name : 'Choose a PDF report'}</strong><span>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB · Click to change` : 'Select the original annual report from your computer'}</span></button>
      <div className="form-two"><label>Bank<select value={bank} onChange={e => setBank(e.target.value as Bank)}>{BANKS.map(item => <option key={item}>{item}</option>)}</select></label><label>Report year<input type="number" min={1900} max={2100} required value={year} onChange={e => setYear(e.target.value)}/></label></div>
      <label>Official source URL <span className="optional">optional</span><input type="url" placeholder="https://…" value={source} onChange={e => setSource(e.target.value)}/></label>
      {error && <div className="inline-error" role="alert">{error}</div>}
      <div className="dialog-footer"><button type="button" className="button secondary" disabled={busy} onClick={onClose}>Cancel</button><button className="button primary" disabled={!file || busy}>{busy ? <span className="spinner"/> : <Icon name="upload"/>}{busy ? 'Uploading report…' : 'Add report'}</button></div>
    </form>
  </dialog>
}

function ReviewForm({ fact, candidates, onPreview, onSaved }: { fact: Fact; candidates: Candidate[]; onPreview: (candidate: Candidate | null) => void; onSaved: (fact: Fact) => void }) {
  const [draft, setDraft] = useState({ value: '', period: '', entity_scope: '', basis: '', observation: 'unknown' as Observation, reason: '', candidate_id: '', restatement_resolved: false })
  const [showAllCandidates, setShowAllCandidates] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [conflict, setConflict] = useState(false)
  const [latest, setLatest] = useState<Fact | null>(null)
  const metric = METRICS.find(item => item.id === fact.metric_id)!
  const loadDraft = (item: Fact) => setDraft({ value: item.value?.toString() ?? '', period: item.period ?? '', entity_scope: item.entity_scope ?? '', basis: item.basis ?? '', observation: item.observation ?? 'unknown', reason: '', candidate_id: item.candidate_id ?? item.evidence?.id ?? '', restatement_resolved: item.restatement_resolved ?? false })
  useEffect(() => { loadDraft(fact); setError(''); setConflict(false); setLatest(null); setShowAllCandidates(false) }, [fact.id, fact.version])
  useEffect(() => { setSuccess('') }, [fact.id])
  const update = (field: Exclude<keyof typeof draft, 'restatement_resolved'>, value: string) => {
    setDraft(current => {
      // A confirmation belongs to the evidence and meaning the reviewer checked.
      // Numeric formatting alone (20 vs 20.0) does not change that meaning.
      const sameNumericValue = field === 'value' && current.value.trim() !== '' && value.trim() !== ''
        && Number.isFinite(Number(current.value)) && Number.isFinite(Number(value))
        && Number(current.value) === Number(value)
      const contextChanged = field !== 'reason' && current[field] !== value && !sameNumericValue
      return { ...current, [field]: value, ...(contextChanged ? { restatement_resolved: false } : {}) }
    })
    setSuccess('')
  }
  const evidence = candidates.find(candidate => candidate.id === draft.candidate_id) ?? (fact.evidence?.id === draft.candidate_id ? fact.evidence : null)
  const availableCandidates = candidates.filter(candidate => showAllCandidates || candidate.metric_ids?.includes(fact.metric_id) || candidate.id === draft.candidate_id)
  if (evidence && !availableCandidates.some(candidate => candidate.id === evidence.id)) availableCandidates.unshift(evidence)
  const canReview = Boolean(evidence?.id)
  const save = async (action: 'accept' | 'edit' | 'reject') => {
    if (busy || conflict) return
    if (!draft.reason.trim()) { setError('Add a review note explaining the evidence checked and your decision.'); return }
    if (action === 'edit' && (!draft.value.trim() || !Number.isFinite(Number(draft.value)))) { setError('Enter a valid numeric value before saving.'); return }
    setBusy(true); setError(''); setSuccess('')
    const body = { action, reason: draft.reason.trim(), expected_version: fact.version ?? 0, ...(action === 'edit' ? { value: Number(draft.value), period: draft.period || null, entity_scope: draft.entity_scope.trim(), basis: draft.basis.trim(), observation: draft.observation, candidate_id: draft.candidate_id || null, ...(fact.restated ? { restatement_resolved: draft.restatement_resolved } : {}) } : {}) }
    try {
      const saved = await api<Fact>(`/facts/${encodeURIComponent(fact.id)}/review`, post(body))
      onSaved(saved)
      setSuccess(action === 'reject' ? 'Rejected. Your reason is saved in the review history.' : 'Review saved with its source evidence.')
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConflict(true); setError('This record was updated elsewhere. Your draft is preserved. Load the latest record before reviewing again.')
        try { const facts = await api<Fact[]>(`/documents/${encodeURIComponent(fact.document_id)}/facts`); setLatest(facts.find(item => item.id === fact.id) ?? null) } catch { /* The explicit reload remains available if the refresh fails. */ }
      } else setError(errorMessage(err))
    } finally { setBusy(false) }
  }
  const reloadLatest = async () => {
    if (latest) { onSaved(latest); loadDraft(latest); setConflict(false); setError(''); return }
    try { const facts = await api<Fact[]>(`/documents/${encodeURIComponent(fact.document_id)}/facts`); const current = facts.find(item => item.id === fact.id); if (current) { onSaved(current); loadDraft(current); setConflict(false); setError('') } else setError('This fact is no longer in the latest run. Refresh the document to review its current results.') } catch (err) { setError(errorMessage(err)) }
  }
  const edited = draft.value !== (fact.value?.toString() ?? '') || draft.period !== (fact.period ?? '') || draft.entity_scope !== (fact.entity_scope ?? '') || draft.basis !== (fact.basis ?? '') || draft.observation !== (fact.observation ?? 'unknown') || draft.candidate_id !== (fact.candidate_id ?? fact.evidence?.id ?? '') || draft.restatement_resolved !== (fact.restatement_resolved ?? false)

  return <div className="review-detail">
    <div className="detail-title"><div><span className="eyebrow">REVIEW SELECTED METRIC</span><h3>{metric.label}</h3></div><StatusBadge status={fact.status}/></div>
    {typeof fact.review?.reason === 'string' && <div className="saved-review"><Icon name={fact.status === 'accepted' ? 'check' : 'book'} size={15}/><div><strong>Saved review · Version {fact.version}</strong><p>{fact.review.reason}</p></div></div>}
    {(fact.issues?.length || fact.restated) ? fact.status === 'accepted' ? <details className="reviewed-notes" key={fact.id}><summary>Original extraction notes (reviewed)</summary><ul>{(fact.issues ?? []).map((issue, index) => <li key={`${issue}-${index}`}>{humanize(issue)}</li>)}{fact.restated && <li>The original source identified a restated figure.</li>}</ul></details> : <div className="context-warning"><Icon name="warning" size={16}/><div><strong>Context needs attention</strong><ul>{(fact.issues ?? []).map((issue, index) => <li key={`${issue}-${index}`}>{humanize(issue)}</li>)}{fact.restated && <li>Source identifies a restated figure. Check the comparison basis.</li>}</ul></div></div> : null}
    {fact.status === 'accepted' && fact.restated && !fact.restatement_resolved && <div className="context-warning"><Icon name="warning" size={16}/><p>This source is restated. Resolve the restatement basis before comparing this figure.</p></div>}
    <div className="evidence-picker"><label>Source evidence<select aria-label="Source evidence" value={draft.candidate_id} disabled={busy || conflict} onChange={event => { const id = event.target.value; update('candidate_id', id); onPreview(candidates.find(candidate => candidate.id === id) ?? null) }}><option value="">Select a source candidate</option>{availableCandidates.map(candidate => <option key={candidate.id} value={candidate.id}>Page {candidate.page} · {candidate.text.replace(/\s+/g, ' ').slice(0, 120)}</option>)}</select></label><label className="checkbox-label"><input type="checkbox" checked={showAllCandidates} onChange={event => setShowAllCandidates(event.target.checked)}/> Show candidates for all metrics</label></div>
    {evidence ? <div className="source-quote"><div><span><Icon name="book" size={14}/> SOURCE · PAGE {evidence.page}</span><span>{fact.method ? humanize(fact.method) : 'extracted'}</span></div><blockquote>{evidence.text || 'Source text unavailable.'}</blockquote>{evidence.context && evidence.context !== evidence.text && <details><summary>Surrounding context</summary><p>{evidence.context}</p></details>}</div> : <div className="context-warning"><Icon name="search" size={17}/><p>No source evidence is attached. Select a source candidate to locate the correct figure before accepting it.</p></div>}
    <div className="form-two"><label>Value <span className="optional">{metric.unit}</span><input value={draft.value} inputMode="decimal" placeholder="Not found" onChange={e => update('value', e.target.value)} disabled={busy || conflict}/></label><label>Reporting date<input type="date" value={draft.period} onChange={e => update('period', e.target.value)} disabled={busy || conflict}/></label></div>
    <label>Entity scope<input value={draft.entity_scope} placeholder="Confirm entity from the source" onChange={e => update('entity_scope', e.target.value)} disabled={busy || conflict}/></label>
    <label>Reporting basis<input value={draft.basis} placeholder="Confirm basis from the source" onChange={e => update('basis', e.target.value)} disabled={busy || conflict}/></label>
    <label>Observation<select value={draft.observation} onChange={e => update('observation', e.target.value)} disabled={busy || conflict}><option value="unknown">Unknown — requires review</option><option value="point_in_time">Point in time</option><option value="quarter_average">Quarter average</option><option value="year_average">Year average</option></select></label>
    {fact.restated && <label className="checkbox-label restatement-confirmation"><input type="checkbox" checked={draft.restatement_resolved} onChange={event => setDraft(current => ({ ...current, restatement_resolved: event.target.checked }))} disabled={busy || conflict}/> I checked and resolved the restatement basis.</label>}
    <label>Review note <span className="optional">required for every decision</span><textarea rows={2} value={draft.reason} placeholder="Explain the decision and any context resolved…" onChange={e => update('reason', e.target.value)} disabled={busy || conflict}/></label>
    {error && <div className="inline-error" role="alert">{error}{conflict && <button className="text-button" type="button" onClick={() => void reloadLatest()}>Load latest record</button>}</div>}
    {success && <div className="inline-success" role="status"><Icon name="check" size={15}/>{success}</div>}
    <div className="review-actions"><button className="button primary" disabled={busy || conflict || !canReview} onClick={() => void save(edited ? 'edit' : 'accept')}>{busy ? <span className="spinner"/> : <Icon name="check" size={16}/>} {edited ? 'Save & accept' : 'Accept figure'}</button><button className="button reject" disabled={busy || conflict} onClick={() => void save('reject')}>Reject</button></div>
    <p className="review-footnote"><Icon name="layers" size={13}/> Version {fact.version ?? 0} · Reviews are retained separately from extraction.</p>
  </div>
}

function ReviewPanel({ document, facts, candidates, loading, selectedMetric, onSelect, onPreview, onSaved, error }: { document: DocumentRecord | null; facts: Fact[]; candidates: Candidate[]; loading: boolean; selectedMetric: MetricId; onSelect: (id: MetricId) => void; onPreview: (candidate: Candidate | null) => void; onSaved: (fact: Fact) => void; error: string }) {
  const selectedFact = facts.find(fact => fact.metric_id === selectedMetric)
  const accepted = facts.filter(fact => fact.status === 'accepted').length
  return <section className="review-panel panel" aria-label="Metric review">
    <div className="panel-heading"><div><span className="eyebrow">EXTRACT · VERIFY · DECIDE</span><h2>Review queue</h2></div><span className="count-label">{accepted}/{METRICS.length}<span> accepted</span></span></div>
    <div className="metric-list" aria-label="Metrics">{METRICS.map(metric => { const fact = facts.find(item => item.metric_id === metric.id); return <button key={metric.id} className={`metric-row ${selectedMetric === metric.id ? 'selected' : ''}`} onClick={() => onSelect(metric.id)} aria-pressed={selectedMetric === metric.id}><span className={`metric-marker ${fact?.status ?? ''}`}>{fact?.status === 'accepted' ? <Icon name="check" size={11}/> : null}</span><span className="metric-name">{metric.short}</span><span className="metric-value">{formatValue(fact?.value, fact?.unit)}</span><Icon name="chevron" size={13}/></button> })}</div>
    {loading ? <div className="small-empty"><span className="spinner"/> Loading extraction results…</div> : error ? <div className="inline-error" role="alert">{error}</div> : selectedFact ? <ReviewForm fact={selectedFact} candidates={candidates} onPreview={onPreview} onSaved={onSaved}/> : <div className="empty-state review-empty"><span className="empty-icon"><Icon name="check" size={25}/></span><h3>{document ? 'Ready for extraction.' : 'A considered second look.'}</h3><p>{document ? 'Run an extraction to collect the five disclosure metrics. Review the value, period and reporting context against the PDF.' : 'Extracted figures appear here for you to verify. Nothing is accepted automatically.'}</p><div className="review-steps"><span><b>01</b> Read the evidence</span><span><b>02</b> Confirm the context</span><span><b>03</b> Accept or explain</span></div></div>}
  </section>
}

function ComparisonView({ documents, selectedId }: { documents: DocumentRecord[]; selectedId: string }) {
  const selectedDocument = documents.find(doc => doc.id === selectedId) ?? documents[0]
  const prior = documents.filter(doc => doc.bank === selectedDocument?.bank && doc.year < selectedDocument.year).sort((a, b) => b.year - a.year)[0]
  const following = documents.filter(doc => doc.bank === selectedDocument?.bank && doc.year > selectedDocument.year).sort((a, b) => a.year - b.year)[0]
  const [left, setLeft] = useState(prior?.id ?? selectedDocument?.id ?? '')
  const [right, setRight] = useState(prior ? selectedDocument?.id ?? '' : following?.id ?? '')
  const [comparison, setComparison] = useState<Comparison | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    setComparison(null); setError('')
    if (!left || !right || left === right) return
    let active = true; setLoading(true)
    void api<Comparison>(`/compare?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`).then(result => { if (active) setComparison(result) }).catch(err => { if (active) setError(errorMessage(err)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [left, right])
  const exportUrl = (format: string) => `/api/export?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}&format=${format}`
  return <section className="comparison-panel panel">
    <div className="comparison-heading"><div><span className="eyebrow">CHANGE, WITH CONTEXT</span><h2>Across reporting periods.</h2><p>Compare reviewed figures only when their reporting context matches.</p></div><div className="export-actions">{comparison && <><a className="button secondary" href={exportUrl('csv')} download><Icon name="download" size={15}/> CSV</a><a className="button secondary" href={exportUrl('markdown')} download><Icon name="download" size={15}/> Markdown</a></>}</div></div>
    <div className="comparison-selectors"><label>Baseline report<select value={left} onChange={e => setLeft(e.target.value)}><option value="">Select a report</option>{documents.map(doc => <option key={doc.id} value={doc.id}>{documentLabel(doc)} · {doc.filename}</option>)}</select></label><button className="icon-button swap-button" aria-label="Swap baseline and comparison report" onClick={() => { setLeft(right); setRight(left) }}><Icon name="compare" size={21}/></button><label>Comparison report<select value={right} onChange={e => setRight(e.target.value)}><option value="">Select a report</option>{documents.map(doc => <option key={doc.id} value={doc.id}>{documentLabel(doc)} · {doc.filename}</option>)}</select></label></div>
    {left && right && left === right && <div className="context-warning"><Icon name="warning"/>Choose two different reports to compare.</div>}
    {error && <div className="inline-error" role="alert">{error}</div>}
    {loading && left !== right && <div className="small-empty"><span className="spinner"/> Checking comparability…</div>}
    {!comparison && !loading && !error && <div className="empty-state comparison-empty"><span className="empty-icon"><Icon name="compare" size={30}/></span><h3>Two periods. One consistent basis.</h3><p>Select two reports to compare accepted figures. Differences in entity, period or measurement basis are surfaced before any change is calculated.</p></div>}
    {comparison && <div className="comparison-table-wrap"><table className="comparison-table"><thead><tr><th>Disclosure metric</th><th>{documentLabel(comparison.left)}<small>BASELINE</small></th><th>{documentLabel(comparison.right)}<small>COMPARISON</small></th><th>Change<small>COMPARABLE FIGURES ONLY</small></th></tr></thead><tbody>{(comparison.rows ?? []).map(row => <tr key={row.metric_id}><td><strong>{row.label || METRICS.find(item => item.id === row.metric_id)?.label}</strong><small>{row.unit === 'AUD_million' ? 'AUD millions' : 'Percentage'}</small></td><td><strong className="table-value">{formatValue(row.left?.value, row.unit)}</strong><StatusBadge status={row.left?.status ?? 'missing'}/><small>{row.left?.period || 'Period not confirmed'}</small></td><td><strong className="table-value">{formatValue(row.right?.value, row.unit)}</strong><StatusBadge status={row.right?.status ?? 'missing'}/><small>{row.right?.period || 'Period not confirmed'}</small></td><td>{row.comparable && row.delta !== null ? <><strong className="delta-value">{row.delta > 0 ? '+' : ''}{numberFormat.format(row.delta)}{row.unit === 'percent' ? ' pp' : ' A$m'}</strong>{row.relative_change !== null && <small>{row.relative_change > 0 ? '+' : ''}{(row.relative_change * 100).toFixed(2)}% relative change</small>}<span className="comparable-label"><Icon name="check" size={13}/> Context matched</span></> : <><span className="not-comparable"><Icon name="warning" size={14}/> Not comparable</span><ul className="comparison-reasons">{(row.reasons?.length ? row.reasons : ['Review both figures and confirm their reporting context.']).map((reason, index) => <li key={index}>{humanize(reason)}</li>)}</ul></>}</td></tr>)}</tbody></table></div>}
    <div className="comparison-note"><Icon name="book" size={17}/><p>Percentage ratios are compared in percentage points. Relative change applies only to risk-weighted assets. Exports retain source details, evidence pages and review decisions.</p></div>
  </section>
}

export default function App() {
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(true)
  const [connectionError, setConnectionError] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [bankFilter, setBankFilter] = useState('all')
  const [facts, setFacts] = useState<Fact[]>([])
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [preview, setPreview] = useState<{ factId: string; candidate: Candidate | null } | null>(null)
  const [factsLoading, setFactsLoading] = useState(false)
  const [factsError, setFactsError] = useState('')
  const [selectedMetric, setSelectedMetric] = useState<MetricId>('cet1_ratio')
  const [method, setMethod] = useState<Method>('rules')
  const [jobBusy, setJobBusy] = useState(false)
  const [jobError, setJobError] = useState('')
  const [uploadOpen, setUploadOpen] = useState(false)
  const [view, setView] = useState<'review' | 'compare'>('review')
  const [factsRevision, setFactsRevision] = useState(0)
  const mounted = useRef(true)

  const refresh = useCallback(async () => {
    const results = await Promise.allSettled([api<DocumentRecord[]>('/documents'), api<Job[]>('/jobs'), api<Health>('/health'), api<Stats>('/stats')])
    if (!mounted.current) return
    const [docsResult, jobsResult, healthResult, statsResult] = results
    if (docsResult.status === 'fulfilled') { setDocuments(docsResult.value); setSelectedId(current => docsResult.value.some(doc => doc.id === current) ? current : docsResult.value[0]?.id ?? ''); setConnectionError('') } else setConnectionError(errorMessage(docsResult.reason))
    if (jobsResult.status === 'fulfilled') setJobs(jobsResult.value)
    if (healthResult.status === 'fulfilled') setHealth(healthResult.value); else setHealth(null)
    if (statsResult.status === 'fulfilled') setStats(statsResult.value)
    setLoading(false)
  }, [])
  useEffect(() => { mounted.current = true; void refresh(); const timer = window.setInterval(() => void refresh(), 4000); return () => { mounted.current = false; window.clearInterval(timer) } }, [refresh])
  const completedKey = jobs.filter(job => job.document_id === selectedId && job.status === 'completed').map(job => `${job.id}:${job.updated_at}`).sort().join('|')
  useEffect(() => {
    setJobError(''); setFacts([]); setCandidates([]); setPreview(null); setFactsError('')
    if (!selectedId) { setFactsLoading(false); return }
    let active = true; setFactsLoading(true)
    void api<Fact[]>(`/documents/${encodeURIComponent(selectedId)}/facts`).then(result => { if (active) setFacts(result) }).catch(err => { if (active) setFactsError(errorMessage(err)) }).finally(() => { if (active) setFactsLoading(false) })
    void api<Candidate[]>(`/documents/${encodeURIComponent(selectedId)}/candidates`).then(result => { if (active) setCandidates(result) }).catch(err => { if (active) setFactsError(`Unable to load source candidates: ${errorMessage(err)}`) })
    return () => { active = false }
  }, [selectedId, completedKey, factsRevision])

  const currentDocument = documents.find(doc => doc.id === selectedId) ?? null
  const visibleDocuments = documents.filter(doc => bankFilter === 'all' || doc.bank === bankFilter).sort((a, b) => b.year - a.year || a.bank.localeCompare(b.bank))
  const selectedFact = facts.find(fact => fact.metric_id === selectedMetric)
  const currentJobs = jobs.filter(job => job.document_id === selectedId).sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))
  const activeJob = currentJobs.find(isActive)
  const latestJob = currentJobs[0]
  const activeJobs = jobs.filter(isActive)
  const failedJobs = jobs.filter(job => job.status === 'failed')
  const runExtraction = async (retry = false, retryMethod?: Method) => {
    if (!selectedId || jobBusy) return
    setJobBusy(true); setJobError('')
    try { await api<Job>(`/documents/${encodeURIComponent(selectedId)}/jobs`, post({ method: retryMethod ?? method, ...(retry ? { retry: true } : {}) })); await refresh() } catch (err) { setJobError(errorMessage(err)) } finally { setJobBusy(false) }
  }
  const onSaved = (saved: Fact) => { setFacts(items => items.map(item => item.id === saved.id ? saved : item)); setPreview(null); void refresh() }
  const onUploaded = (doc: DocumentRecord) => { setDocuments(items => items.some(item => item.id === doc.id) ? items : [doc, ...items]); setSelectedId(doc.id); setBankFilter('all'); setView('review'); setUploadOpen(false); void refresh() }

  return <div className="app-shell">
    <header className="app-header"><a className="brand" href="#" onClick={e => { e.preventDefault(); setView('review') }} aria-label="Disclosure Desk home"><span className="brand-mark"><span/><span/><span/></span><span>Disclosure<span className="brand-light"> Desk</span><small>BANK DISCLOSURE REVIEW</small></span></a><nav aria-label="Workspace views"><button className={view === 'review' ? 'active' : ''} onClick={() => setView('review')}><Icon name="book" size={16}/> Review workspace</button><button className={view === 'compare' ? 'active' : ''} onClick={() => setView('compare')}><Icon name="compare" size={16}/> Period comparison</button></nav><div className="header-local"><span className={`connection-dot ${health ? 'online' : ''}`}/><span>Local workspace<small>{health ? 'Connected' : loading ? 'Connecting…' : 'Connection unavailable'}</small></span><span className="local-avatar">DD</span></div></header>
    <main>
      <div className="workspace-intro"><div><span className="eyebrow">THE DISCLOSURE WORKBENCH</span><h1>Every figure. In context.</h1><p>A clear view of the evidence, from annual report to reviewed comparison.</p></div><button className="button primary" onClick={() => setUploadOpen(true)}><Icon name="upload" size={17}/> Add report</button></div>
      {connectionError && <div className="connection-banner" role="alert"><Icon name="warning" size={17}/><span><strong>The local service is unavailable.</strong> {connectionError}</span><button className="text-button" onClick={() => void refresh()}>Reconnect</button></div>}
      <div className="workspace-summary"><div><Icon name="file" size={19}/><strong>{stats ? numberFormat.format(stats.documents) : documents.length || '—'}</strong><span>reports in library</span></div><div><span className="summary-dot amber"/><strong>{stats ? numberFormat.format(stats.facts_pending) : '—'}</strong><span>figures awaiting review</span></div><div><span className="summary-dot green"/><strong>{stats ? numberFormat.format(stats.facts_accepted) : '—'}</strong><span>figures accepted</span></div><span className="summary-note"><Icon name="layers" size={14}/> Source-linked. Reviewer-controlled.</span></div>
      {view === 'compare' ? <ComparisonView documents={documents} selectedId={selectedId}/> : <>
        <div className="workspace-bar"><div className="breadcrumb"><span>Workspace</span><Icon name="chevron" size={12}/><strong>{currentDocument ? `${documentLabel(currentDocument)} annual report` : 'Report library'}</strong></div><span className="workspace-meta">{currentDocument?.page_count ? `${numberFormat.format(currentDocument.page_count)} pages` : 'Original sources, retained'}{currentDocument?.source_url && <a href={currentDocument.source_url} target="_blank" rel="noreferrer">Official source <Icon name="external" size={12}/></a>}</span></div>
        <div className="workbench-grid">
          <aside className="library-panel panel" aria-label="Report library"><div className="panel-heading"><div><span className="eyebrow">YOUR DOCUMENTS</span><h2>Report library</h2></div><span className="library-count">{documents.length}</span></div><div className="library-filter"><Icon name="search" size={15}/><select aria-label="Filter reports by bank" value={bankFilter} onChange={e => setBankFilter(e.target.value)}><option value="all">All banks</option>{BANKS.map(bank => <option key={bank}>{bank}</option>)}</select></div><div className="document-list">{loading ? <div className="small-empty"><span className="spinner"/> Loading reports…</div> : visibleDocuments.length ? visibleDocuments.map(doc => <button className={`document-card ${doc.id === selectedId ? 'selected' : ''}`} key={doc.id} onClick={() => setSelectedId(doc.id)}><span className="document-top"><span className={`bank-monogram bank-${doc.bank.toLowerCase()}`}>{doc.bank === 'Westpac' ? 'W' : doc.bank === 'CBA' ? 'C' : doc.bank === 'ANZ' ? 'A' : 'N'}</span><strong>{doc.bank}</strong><span className="document-year">{doc.year}</span></span><span className="document-title">Annual report</span><span className="document-filename" title={doc.filename}>{doc.filename}</span><span className="document-bottom"><Icon name="file" size={12}/>{doc.page_count ? `${doc.page_count} pages` : 'PDF report'}{doc.id === selectedId && <span>In review <span className="tiny-dot"/></span>}</span></button>) : <div className="library-empty"><Icon name="file" size={26}/><strong>{bankFilter === 'all' ? 'Your library starts here' : 'No reports for this bank'}</strong><p>Add an annual report to begin reviewing its disclosures.</p><button className="text-button" onClick={() => setUploadOpen(true)}>Add your first report <Icon name="arrow" size={14}/></button></div>}</div>
            <div className="extraction-controls"><div className="section-label"><Icon name="spark" size={15}/><span>Extraction</span></div><label className="sr-only" htmlFor="extraction-method">Extraction method</label><select id="extraction-method" value={method} onChange={e => setMethod(e.target.value as Method)}><option value="rules">Rules · deterministic</option><option value="qwen" disabled={!health?.model_available}>Qwen · local model{!health?.model_available ? ' (unavailable)' : ''}</option></select><p>{method === 'rules' ? 'Find candidate figures using disclosure patterns.' : 'Read candidate evidence with the local Qwen model.'}</p><button className="button primary extraction-button" disabled={!selectedId || jobBusy || Boolean(activeJob) || (method === 'qwen' && !health?.model_available)} onClick={() => void runExtraction()}>{jobBusy || activeJob ? <span className="spinner"/> : <Icon name="arrow" size={15}/>} {activeJob ? 'Extraction in progress' : jobBusy ? 'Queuing…' : 'Run extraction'}</button>{jobError && <div className="inline-error" role="alert">{jobError}</div>}
              {activeJob && <div className="job-progress" role="status"><div><StatusBadge status={activeJob.status}/><span>{humanize(activeJob.method)}</span></div><p>{activeJob.stage ? humanize(activeJob.stage) : activeJob.status === 'queued' ? 'Waiting for the local worker' : 'Processing source evidence'}</p><div className="indeterminate-track"><span/></div>{health?.worker_active === false && <p className="worker-notice">Worker is not active. This job will start when the worker connects.</p>}</div>}
              {!activeJob && latestJob?.status === 'failed' && <div className="failed-job"><span><Icon name="warning" size={14}/> Extraction failed</span><p>{latestJob.error || 'The worker could not complete this job.'}</p><button className="text-button" disabled={jobBusy} onClick={() => void runExtraction(true, latestJob.method)}><Icon name="refresh" size={13}/> Retry {humanize(latestJob.method)}</button></div>}
              {!activeJob && latestJob?.status === 'completed' && <div className="last-run"><Icon name="check" size={13}/> {humanize(latestJob.method)} extraction complete<button className="icon-button" aria-label="Refresh extracted facts" onClick={() => setFactsRevision(value => value + 1)}><Icon name="refresh" size={12}/></button></div>}
            </div>
            <div className="queue-summary"><Icon name="clock" size={14}/><span>{activeJobs.length} active jobs{failedJobs.length ? ` · ${failedJobs.length} failed` : ''}</span><span className={`worker-dot ${health?.worker_active ? 'online' : ''}`} title={health?.worker_active ? 'Worker active' : 'Worker offline'}/></div>
          </aside>
          <PdfViewer document={currentDocument} evidence={preview && preview.factId === selectedFact?.id ? preview.candidate : selectedFact?.evidence}/>
          <ReviewPanel document={currentDocument} facts={facts} candidates={candidates} loading={factsLoading} selectedMetric={selectedMetric} onSelect={id => { setSelectedMetric(id); setPreview(null) }} onPreview={candidate => setPreview({ factId: selectedFact?.id ?? '', candidate })} onSaved={onSaved} error={factsError}/>
        </div>
      </>}
      <footer className="app-footer"><span><span className="footer-mark">D</span> Disclosure Desk</span><p>Read the source. Keep the context. Record the decision.</p><span>Single-user · Local storage</span></footer>
    </main>
    {uploadOpen && <UploadDialog onClose={() => setUploadOpen(false)} onUploaded={onUploaded}/>}
  </div>
}
