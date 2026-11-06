export interface ScanRow {
  stock_code: string
  stock_name: string
  trade_date: string
  close_price: number
  dif: number
  dea: number
  hist: number
  hist_weekly: number
  hist_monthly: number
  up_daily: boolean
  up_weekly: boolean
  up_monthly: boolean
  up_daily_turn: boolean
  up_weekly_turn: boolean
  up_monthly_turn: boolean
  monthly_ready: boolean
  watch: boolean
  wm_resonance: boolean
  resonance: boolean
  dw_resonance: boolean
  dw_watch: boolean
  entry: boolean
  wm_entry: boolean
  dw_entry: boolean
  watch_entry: boolean
  dw_watch_entry: boolean
  phase_wm: boolean
  phase_entry: boolean
  entry_fresh: boolean
  entry_slowest: boolean
  week_age_weeks: number | null
  w2m_weeks: number | null
  d2w_days: number | null
  bars_since_cross: number | null
  [k: string]: any
}

export interface ScanData {
  available: boolean
  date?: string
  universe?: string
  counts?: Record<string, number>
  rows: ScanRow[]
  file?: string
  mtime?: string
  generated_at?: string
  error?: string
}

export interface ScanJob {
  id: string
  status: 'running' | 'completed' | 'failed'
  universe: string
  date?: string | null
  started?: string
  finished?: string | null
  duration?: number | null
  message?: string
}

export interface PerfRow {
  horizon: number
  main?: string | null
  signals: number
  trades: number
  skip: number
  skip_lu: number
  skip_cap: number
  dedup: number
  annual: number
  alpha: number
  alpha_t: number
  vol: number
  sharpe: number
  mdd: number
  calmar: number
  util: number
  avg_net: number
  net_t: number
  win: number
}

export interface TradeRow {
  code: string
  entry: string
  exit: string
  hold: number
  entry_px: number
  exit_px: number
  gross: number
  net: number
  mfe: number
  mae: number
  reason?: string
  [k: string]: any
}

export interface BacktestData {
  available: boolean
  file?: string
  mtime?: string
  perf: PerfRow[]
  benchmark?: any
  annual: Record<string, Record<string, number | null>>
  monthly: Record<string, Record<string, number | null>>
  nav: Array<Record<string, any>>
  trades: Record<string, TradeRow[]>
  signals: Array<Record<string, any>>
  horizons: number[]
  main: number
  error?: string
}

export interface StockData {
  available: boolean
  code: string
  name: string
  first?: string
  last?: string
  daily: Array<Record<string, any>>
  weekly: Array<Record<string, any>>
  monthly: Array<Record<string, any>>
  signals: Array<Record<string, any>>
  error?: string
}

export interface RunRow {
  name: string
  rel: string
  size: number
  mtime: string
  kind: string
}
