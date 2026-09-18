import type { CSSProperties } from 'react'

type IconName = 'file' | 'upload' | 'arrow' | 'check' | 'close' | 'chevron' | 'compare' | 'download' | 'search' | 'external' | 'clock' | 'refresh' | 'warning' | 'book' | 'spark' | 'layers'
const paths: Record<IconName, React.ReactNode> = {
  file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h6"/></>,
  upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5"/></>,
  arrow: <path d="M4 12h16m-6-6 6 6-6 6"/>, check: <path d="m5 12 4 4L19 6"/>, close: <path d="m6 6 12 12M18 6 6 18"/>, chevron: <path d="m9 5 7 7-7 7"/>,
  compare: <><path d="M4 7h16m-4-4 4 4-4 4M20 17H4m4-4-4 4 4 4"/></>,
  download: <path d="M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5"/>,
  search: <><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></>,
  external: <path d="M14 3h7v7m0-7L10 14M10 3H3v18h18v-7"/>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  refresh: <><path d="M20 7a9 9 0 1 0 1 9M20 3v5h-5"/></>,
  warning: <><path d="m12 3 10 18H2zM12 9v5"/><path d="M12 17h.01"/></>,
  book: <><path d="M12 5v16M3 3c4 0 6 0 9 2 3-2 5-2 9-2v16c-4 0-6 0-9 2-3-2-5-2-9-2z"/></>,
  spark: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z"/></>,
  layers: <><path d="m12 3 10 5-10 5L2 8zm-10 9 10 5 10-5M2 16l10 5 10-5"/></>,
}
export function Icon({ name, size = 18, className, style }: { name: IconName; size?: number; className?: string; style?: CSSProperties }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className} style={style}>{paths[name]}</svg>
}
