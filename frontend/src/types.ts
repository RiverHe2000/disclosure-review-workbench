export const BANKS = ['CBA', 'Westpac', 'ANZ', 'NAB'] as const
export type Bank = typeof BANKS[number]
export type Method = 'rules' | 'qwen'
export type Observation = 'point_in_time' | 'quarter_average' | 'year_average' | 'unknown'
export type MetricId = 'cet1_ratio' | 'total_capital_ratio' | 'rwa' | 'lcr' | 'nsfr'
export const METRICS: { id: MetricId; label: string; short: string; unit: string }[] = [
  { id: 'cet1_ratio', label: 'Common equity tier 1', short: 'CET1 ratio', unit: '%' },
  { id: 'total_capital_ratio', label: 'Total capital ratio', short: 'Total capital', unit: '%' },
  { id: 'rwa', label: 'Risk-weighted assets', short: 'RWA', unit: 'A$m' },
  { id: 'lcr', label: 'Liquidity coverage ratio', short: 'LCR', unit: '%' },
  { id: 'nsfr', label: 'Net stable funding ratio', short: 'NSFR', unit: '%' },
]
export interface DocumentRecord { id: string; bank: Bank; year: number; filename: string; sha256?: string; source_url?: string; created_at?: string; page_count?: number }
export interface Candidate { id: string; document_id: string; page: number; bbox: [number, number, number, number]; page_width: number; page_height: number; text: string; context?: string; metric_ids?: MetricId[]; numbers?: string[] }
export interface Fact { id: string; document_id: string; run_id: string; metric_id: MetricId; value: number | null; unit: string; period?: string | null; entity_scope?: string; basis?: string; observation?: Observation; restated?: boolean; restatement_resolved?: boolean; candidate_id?: string | null; evidence?: Candidate | null; status: 'pending' | 'accepted' | 'rejected' | 'missing'; issues?: string[]; method?: Method | 'replay'; review?: Record<string, unknown> | null; version: number }
export interface Job { id: string; document_id: string; method: Method; status: 'queued' | 'running' | 'completed' | 'failed'; stage?: string; error?: string | null; attempts?: number; created_at?: string; updated_at?: string }
export interface Health { status: string; model_available?: boolean; model_path?: string; worker_active?: boolean }
export interface Stats { documents: number; jobs_completed: number; jobs_failed: number; facts_pending: number; facts_accepted: number }
export interface ComparisonRow { metric_id: MetricId; label: string; left: Fact | null; right: Fact | null; comparable: boolean; delta: number | null; relative_change: number | null; unit: string; reasons?: string[] }
export interface Comparison { left: DocumentRecord; right: DocumentRecord; rows: ComparisonRow[] }
