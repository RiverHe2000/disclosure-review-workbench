import { useEffect, useRef, useState } from 'react'
import { GlobalWorkerOptions, getDocument } from 'pdfjs-dist'
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { errorMessage } from './api'
import { Icon } from './Icons'
import type { Candidate, DocumentRecord } from './types'

GlobalWorkerOptions.workerSrc = workerUrl

export function PdfViewer({ document, evidence }: { document: DocumentRecord | null; evidence?: Candidate | null }) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null)
  const [pageNumber, setPageNumber] = useState(1)
  const [pageInput, setPageInput] = useState('1')
  const [pageCount, setPageCount] = useState(0)
  const [width, setWidth] = useState(600)
  const [pageSize, setPageSize] = useState({ width: 600, height: 850 })
  const [zoom, setZoom] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const container = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const renderTask = useRef<RenderTask | null>(null)
  const documentId = document?.id

  useEffect(() => {
    setPdf(null); setError(''); setPageCount(0); setPageNumber(1); setPageInput('1'); setZoom(1)
    if (!documentId) return
    setLoading(true)
    let active = true
    const task = getDocument(`/api/documents/${encodeURIComponent(documentId)}/pdf`)
    void task.promise.then(result => { if (active) { setPdf(result); setPageCount(result.numPages) } }).catch(err => { if (active) { setError(errorMessage(err)); setLoading(false) } })
    return () => { active = false; void task.destroy() }
  }, [documentId])

  useEffect(() => {
    if (!evidence?.page) return
    setPageNumber(evidence.page); setPageInput(String(evidence.page))
  }, [evidence?.id, evidence?.page])

  useEffect(() => {
    if (!container.current) return
    const observer = new ResizeObserver(entries => setWidth(Math.max(200, entries[0].contentRect.width)))
    observer.observe(container.current)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!pdf || !canvas.current) return
    let active = true
    setLoading(true); setError('')
    const render = async () => {
      const previous = renderTask.current
      previous?.cancel()
      if (previous) { try { await previous.promise } catch { /* Cancelled render releases the canvas. */ } }
      if (!active) return
      const page = await pdf.getPage(Math.max(1, Math.min(pageNumber, pdf.numPages)))
      if (!active || !canvas.current) return
      const natural = page.getViewport({ scale: 1 })
      const viewport = page.getViewport({ scale: width * zoom / natural.width })
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2)
      const target = canvas.current
      target.width = Math.floor(viewport.width * pixelRatio)
      target.height = Math.floor(viewport.height * pixelRatio)
      target.style.width = `${viewport.width}px`
      target.style.height = `${viewport.height}px`
      setPageSize({ width: viewport.width, height: viewport.height })
      const task = page.render({ canvas: target, viewport, transform: [pixelRatio, 0, 0, pixelRatio, 0, 0] })
      renderTask.current = task
      await task.promise
      if (active) setLoading(false)
    }
    void render().catch(err => { if (active && !(err instanceof Error && err.name === 'RenderingCancelledException')) { setError(errorMessage(err)); setLoading(false) } })
    return () => { active = false; renderTask.current?.cancel() }
  }, [pdf, pageNumber, width, zoom])

  useEffect(() => {
    if (loading || !container.current || !evidence || evidence.page !== pageNumber || !evidence.page_height || !evidence.page_width) return
    const viewport = container.current
    viewport.scrollTop = Math.max(0, 25 + evidence.bbox[1] / evidence.page_height * pageSize.height - viewport.clientHeight * 0.35)
    const centreX = (evidence.bbox[0] + evidence.bbox[2]) / 2
    viewport.scrollLeft = Math.max(0, 24 + centreX / evidence.page_width * pageSize.width - viewport.clientWidth / 2)
  }, [loading, pageNumber, pageSize.width, pageSize.height, evidence])

  const goToPage = (page: number) => { const next = Math.max(1, Math.min(page, pageCount || 1)); setPageNumber(next); setPageInput(String(next)) }
  const highlight = evidence && evidence.page === pageNumber && evidence.bbox?.length === 4 && evidence.page_width > 0 && evidence.page_height > 0 ? {
    left: `${100 * evidence.bbox[0] / evidence.page_width}%`, top: `${100 * evidence.bbox[1] / evidence.page_height}%`,
    width: `${100 * (evidence.bbox[2] - evidence.bbox[0]) / evidence.page_width}%`, height: `${100 * (evidence.bbox[3] - evidence.bbox[1]) / evidence.page_height}%`,
  } : null

  return <section className="evidence-panel panel" aria-label="PDF source evidence">
    <div className="panel-heading"><div><span className="eyebrow">SOURCE DOCUMENT</span><h2>{document ? `${document.bank} · ${document.year}` : 'Source evidence'}</h2></div>{document && <a className="icon-button" href={`/api/documents/${encodeURIComponent(document.id)}/pdf`} target="_blank" rel="noreferrer" title="Open original PDF"><Icon name="external"/></a>}</div>
    <div className="pdf-toolbar"><div className="page-controls"><button className="icon-button" disabled={!pdf || pageNumber <= 1} onClick={() => goToPage(pageNumber - 1)} aria-label="Previous page"><Icon name="chevron" style={{ transform: 'rotate(180deg)' }}/></button><span>Page</span><input aria-label="PDF page number" inputMode="numeric" value={pageInput} disabled={!pdf} onChange={e => setPageInput(e.target.value)} onBlur={() => goToPage(Number.parseInt(pageInput, 10) || pageNumber)} onKeyDown={e => { if (e.key === 'Enter') goToPage(Number.parseInt(pageInput, 10) || pageNumber) }}/><span className="muted">of {pageCount || '—'}</span><button className="icon-button" disabled={!pdf || pageNumber >= pageCount} onClick={() => goToPage(pageNumber + 1)} aria-label="Next page"><Icon name="chevron"/></button></div><select aria-label="PDF zoom" value={zoom} onChange={e => setZoom(Number(e.target.value))}><option value={1}>Fit width</option><option value={1.25}>125%</option><option value={1.5}>150%</option><option value={2}>200%</option></select></div>
    <div className="pdf-stage" ref={container}>
      {!document && <div className="empty-state source-empty"><div className="document-illustration"><span/><span/><span/><Icon name="search" size={26}/></div><h3>Start with the source.</h3><p>Select a report to read the original PDF and inspect the evidence behind each extracted figure.</p><span className="empty-caption">PDF evidence · Page coordinates · Original context</span></div>}
      {document && error && <div className="empty-state"><Icon name="warning" size={28}/><h3>Unable to display this PDF</h3><p>{error}</p><a href={`/api/documents/${encodeURIComponent(document.id)}/pdf`} target="_blank" rel="noreferrer" className="button secondary">Open original PDF</a></div>}
      {document && !error && <><div className={`pdf-page ${loading ? 'is-loading' : ''}`} style={{ width: pageSize.width, height: pageSize.height }}><canvas ref={canvas} aria-label={`Original PDF, physical page ${pageNumber}`}/>{highlight && !loading && <div className="evidence-highlight" style={highlight} role="img" aria-label={`Highlighted source evidence on page ${pageNumber}: ${evidence?.text}`} title={evidence?.text}/>}</div>{loading && <div className="pdf-loading"><span className="spinner"/> Rendering source document</div>}</>}
    </div>
    <div className="evidence-footer"><span className={`legend-dot ${highlight ? 'highlighted' : ''}`}/>{highlight ? `Evidence highlighted · Physical PDF page ${pageNumber}` : document ? 'Select a metric with evidence to locate its source.' : 'Every reviewed figure stays linked to its source.'}</div>
  </section>
}
