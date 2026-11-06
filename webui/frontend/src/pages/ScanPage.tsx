import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Button, Card, Col, Empty, Input, Row, Select, Space, Spin, Switch,
  Table, Tabs, Tag, Tooltip, Typography, message,
} from 'antd'
import {
  ScanOutlined, FilterOutlined, ThunderboltOutlined, DownloadOutlined,
  FundOutlined, AimOutlined, AppstoreOutlined, CalendarOutlined,
  LineChartOutlined, PlayCircleOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import MetricCard from '../components/MetricCard'
import { downloadCSV, fmt, getScan, getScanJob, runScan } from '../api'
import type { ScanData, ScanJob, ScanRow } from '../types'

const { Text } = Typography
const GREEN = '#00e676'
const RED = '#ff5252'
const ORANGE = '#FF6B35'
const BLUE = '#42a5f5'
const PURPLE = '#ba68c8'

type LayerKey = 'all' | 'daily' | 'weekly' | 'monthly' | 'dw' | 'wm' | 'entry' | 'watch' | 'phase'

const UNIVERSE_LABEL: Record<string, string> = {
  hs300: '沪深300', all: '全市场', coal: '动力煤',
}
const UNIVERSE_OPTIONS = [
  { value: 'hs300', label: '沪深300' },
  { value: 'all', label: '全市场' },
  { value: 'coal', label: '动力煤' },
]

const universeFromHash = (): string => {
  const m = window.location.hash.match(/universe=(hs300|all|coal)/)
  return m ? m[1] : 'hs300'
}

const LAYERS: Array<{ key: LayerKey; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'daily', label: '日线' },
  { key: 'weekly', label: '周线' },
  { key: 'monthly', label: '月线' },
  { key: 'dw', label: '日周共振' },
  { key: 'wm', label: '周月共振' },
  { key: 'entry', label: '三周期' },
  { key: 'watch', label: '候选池' },
  { key: 'phase', label: '分段' },
]

/** 某场景的「当前状态」是否命中。 */
function stateOf(r: ScanRow, k: LayerKey): boolean {
  switch (k) {
    case 'daily': return !!r.up_daily
    case 'weekly': return !!r.up_weekly
    case 'monthly': return !!r.up_monthly
    case 'dw': return !!r.dw_resonance
    case 'wm': return !!r.wm_resonance
    case 'entry': return !!r.resonance
    case 'watch': return !!r.watch
    case 'phase': return !!(r.phase_wm || r.phase_entry)
    default: return true
  }
}

/** 某场景的「首日」是否命中（仅首日开关用）。 */
function turnOf(r: ScanRow, k: LayerKey): boolean {
  switch (k) {
    case 'daily': return !!r.up_daily_turn
    case 'weekly': return !!r.up_weekly_turn
    case 'monthly': return !!r.up_monthly_turn
    case 'dw': return !!r.dw_entry
    case 'wm': return !!r.wm_entry
    case 'entry': return !!r.entry
    case 'watch': return !!r.watch_entry
    case 'phase': return !!(r.phase_wm || r.phase_entry)
    default: return !!(r.up_daily_turn || r.up_weekly_turn || r.up_monthly_turn
      || r.dw_entry || r.wm_entry || r.entry || r.phase_wm || r.phase_entry)
  }
}

function histText(v: number | null) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return Number(v).toFixed(3)
}

function stateTags(r: ScanRow, layer: LayerKey) {
  const tags: any[] = []
  if (r.watch) tags.push(<Tag key="w" color="gold">候选</Tag>)
  if (r.wm_resonance) tags.push(<Tag key="m" color="blue">周月</Tag>)
  if (r.resonance) tags.push(<Tag key="e" color="green">三周期</Tag>)
  if (r.dw_resonance) tags.push(<Tag key="d" color="orange">日周</Tag>)
  if (r.phase_wm) tags.push(<Tag key="p" color="purple">分段·周月</Tag>)
  if (r.phase_entry) tags.push(<Tag key="q" color="purple">分段·三周期</Tag>)
  // 只标「当前场景」的首日，避免日线 Tab 被周线/日周的首日误标
  if (turnOf(r, layer)) {
    tags.push(<Tag key="f" color="red">首日</Tag>)
  }
  return tags
}

export default function ScanPage({ meta, onOpenStock }: {
  meta: any
  onOpenStock: (code: string) => void
}) {
  const [data, setData] = useState<ScanData | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [layer, setLayer] = useState<LayerKey>('all')
  const [onlyFirst, setOnlyFirst] = useState(false)
  const [q, setQ] = useState('')
  const [universe, setUniverse] = useState(universeFromHash)
  const [job, setJob] = useState<ScanJob | null>(null)
  const [scanning, setScanning] = useState(false)
  const timer = useRef<any>(null)

  const changeUniverse = (u: string) => {
    setUniverse(u)
    if (window.location.hash !== `#/scan?universe=${u}`) {
      window.location.hash = `#/scan?universe=${u}`
    }
  }

  const load = (u: string) => {
    setLoading(true)
    setErr('')
    setData(null)
    getScan(u)
      .then(d => setData(d))
      .catch(e => setErr(String(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load(universe) }, [universe])
  useEffect(() => () => { if (timer.current) clearInterval(timer.current) }, [])

  const startScan = async () => {
    setScanning(true)
    setJob(null)
    try {
      const j = await runScan(universe)
      setJob(j)
      timer.current = setInterval(async () => {
        try {
          const s = await getScanJob(j.id)
          setJob(s)
          if (s.status !== 'running') {
            if (timer.current) clearInterval(timer.current)
            timer.current = null
            setScanning(false)
            if (s.status === 'completed') {
              message.success('扫描完成')
              load(universe)
            } else {
              message.error(`扫描失败：${s.message || ''}`)
            }
          }
        } catch (e) {
          if (timer.current) clearInterval(timer.current)
          timer.current = null
          setScanning(false)
          message.error(String(e))
        }
      }, 2000)
    } catch (e) {
      setScanning(false)
      message.error(String(e))
    }
  }

  const rows = data?.rows ?? []

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const r of rows) {
      for (const l of LAYERS) {
        if (stateOf(r, l.key)) c[l.key] = (c[l.key] || 0) + 1
      }
    }
    return c
  }, [rows])

  const filtered = useMemo(() => {
    const kw = q.trim()
    const rank = (r: ScanRow) =>
      r.resonance ? 5 : r.wm_resonance ? 4 : r.dw_resonance ? 3
        : (r.phase_wm || r.phase_entry) ? 2 : r.watch ? 1 : 0
    return rows.filter(r => {
      if (!stateOf(r, layer)) return false
      if (onlyFirst && !turnOf(r, layer)) return false
      if (kw && !(`${r.stock_code}${r.stock_name}`.toLowerCase().includes(kw.toLowerCase()))) return false
      return true
    }).sort((a, b) => rank(b) - rank(a) || a.stock_code.localeCompare(b.stock_code))
  }, [rows, layer, onlyFirst, q])

  const columns: ColumnsType<ScanRow> = [
    {
      title: '代码', dataIndex: 'stock_code', width: 82, fixed: 'left',
      render: (v: string) => <span className="code-link" onClick={() => onOpenStock(v)}>{v}</span>,
    },
    { title: '名称', dataIndex: 'stock_name', width: 92, fixed: 'left' },
    {
      title: '收盘', dataIndex: 'close_price', width: 80, align: 'right',
      sorter: (a, b) => (a.close_price || 0) - (b.close_price || 0),
      render: (v: number) => <span className="mono">{fmt(v, 2)}</span>,
    },
    {
      title: '日线hist', dataIndex: 'hist', width: 88, align: 'right',
      sorter: (a, b) => (a.hist || 0) - (b.hist || 0),
      render: (v: number) => <span className="mono" style={{ color: (v || 0) >= 0 ? GREEN : RED }}>{histText(v)}</span>,
    },
    {
      title: '周线hist', dataIndex: 'hist_weekly', width: 88, align: 'right',
      render: (v: number) => <span className="mono" style={{ color: (v || 0) >= 0 ? GREEN : RED }}>{histText(v)}</span>,
    },
    {
      title: '月线hist', dataIndex: 'hist_monthly', width: 88, align: 'right',
      render: (v: number) => <span className="mono" style={{ color: (v || 0) >= 0 ? GREEN : RED }}>{histText(v)}</span>,
    },
    {
      title: '日', dataIndex: 'up_daily', width: 46, align: 'center',
      render: (v: boolean) => v ? <Tag color="green">上</Tag> : <Tag>—</Tag>,
    },
    {
      title: '周', dataIndex: 'up_weekly', width: 46, align: 'center',
      render: (v: boolean) => v ? <Tag color="green">上</Tag> : <Tag>—</Tag>,
    },
    {
      title: '月', dataIndex: 'up_monthly', width: 46, align: 'center',
      render: (v: boolean) => v ? <Tag color="green">上</Tag> : <Tag>—</Tag>,
    },
    {
      title: '月线可用', dataIndex: 'monthly_ready', width: 78, align: 'center',
      render: (v: boolean) => v ? <Tag color="blue">是</Tag> : <Tag>否</Tag>,
    },
    {
      title: '周龄', dataIndex: 'week_age_weeks', width: 62, align: 'right',
      render: (v: number) => v == null ? '—' : <span className="mono">{Number(v).toFixed(1)}</span>,
    },
    {
      title: '日线领先', dataIndex: 'd2w_days', width: 76, align: 'right',
      render: (v: number) => v == null ? '—' : <span className="mono">{Number(v).toFixed(0)}天</span>,
    },
    {
      title: '状态', key: 'state', width: 220,
      render: (_: any, r: ScanRow) => <Space size={2} wrap>{stateTags(r, layer)}</Space>,
    },
  ]

  const exportCSV = () => {
    if (!filtered.length) { message.warning('当前筛选没有数据'); return }
    const headers = ['代码', '名称', '交易日', '收盘', '日线DIF', '日线DEA', '日线hist',
      '周线hist', '月线hist', '日线上抛', '周线上抛', '月线上抛', '月线可用',
      '候选池', '周月共振', '三周期共振', '日周共振', '首日']
    const body = filtered.map(r => [
      r.stock_code, r.stock_name, r.trade_date, r.close_price, r.dif, r.dea, r.hist,
      r.hist_weekly, r.hist_monthly, r.up_daily, r.up_weekly, r.up_monthly, r.monthly_ready,
      r.watch, r.wm_resonance, r.resonance, r.dw_resonance,
      turnOf(r, 'all'),
    ])
    downloadCSV(`mlr_scan_${data?.date || 'latest'}_${layer}.csv`, headers, body)
  }

  const scanBar = (
    <Space>
      <Select size="small" value={universe} onChange={changeUniverse}
        style={{ width: 110 }} options={UNIVERSE_OPTIONS} />
      <Button size="small" type="primary" icon={<PlayCircleOutlined />}
        loading={scanning} onClick={startScan}>执行扫描</Button>
    </Space>
  )
  const jobBanner = scanning ? (
    <Alert
      type="info" showIcon style={{ marginBottom: 12 }}
      icon={<Spin size="small" />}
      message={`正在扫描 ${UNIVERSE_LABEL[universe] || universe} ...`}
      description={universe === 'all'
        ? '全市场约 2–5 分钟（分块计算），完成后自动刷新；期间可继续查看旧数据'
        : '约 15–30 秒，完成后自动刷新'}
    />
  ) : null

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>
  if (err) return <Alert type="error" showIcon message="扫描数据不可用" description={err} />
  if (!data?.available) {
    return (
      <Card size="small" className="panel-card"
        title={<Space><ScanOutlined style={{ color: ORANGE }} /><span>扫描结果</span></Space>}
        extra={scanBar}>
        {jobBanner}
        <Alert type="warning" showIcon
          message={`${UNIVERSE_LABEL[universe] || universe} 还没有扫描快照`}
          description={data?.error || '点击右上角「执行扫描」生成'} />
      </Card>
    )
  }

  const cards = [
    { icon: <AppstoreOutlined />, value: String(counts.all ?? rows.length), label: '股票池（当日）', color: BLUE, bg: 'rgba(66,165,245,0.10)' },
    { icon: <LineChartOutlined />, value: String(counts.daily ?? 0), label: '日线上抛', color: GREEN, bg: 'rgba(0,230,118,0.08)' },
    { icon: <CalendarOutlined />, value: String(counts.weekly ?? 0), label: '周线上抛', color: BLUE, bg: 'rgba(66,165,245,0.08)' },
    { icon: <CalendarOutlined />, value: String(counts.monthly ?? 0), label: '月线上抛', color: PURPLE, bg: 'rgba(186,104,200,0.08)' },
    { icon: <ThunderboltOutlined />, value: String(counts.dw ?? 0), label: '日周共振', color: ORANGE, bg: 'rgba(255,107,53,0.10)' },
    { icon: <FundOutlined />, value: String(counts.wm ?? 0), label: '周月共振', color: BLUE, bg: 'rgba(66,165,245,0.08)' },
    { icon: <AimOutlined />, value: String(counts.entry ?? 0), label: '三周期共振', color: GREEN, bg: 'rgba(0,230,118,0.08)' },
    { icon: <FilterOutlined />, value: String(counts.watch ?? 0), label: '候选池（周上抛/月未上抛）', color: '#ffb74d', bg: 'rgba(255,183,77,0.10)' },
  ]

  return (
    <div>
      {jobBanner}
      <Row gutter={[10, 10]} style={{ marginBottom: 14 }}>
        {cards.map((c, i) => <Col xs={12} sm={8} md={6} lg={3} key={i}><MetricCard {...c} /></Col>)}
      </Row>

      <Card
        size="small" className="panel-card"
        title={<Space><ScanOutlined style={{ color: ORANGE }} /><span>扫描结果</span>
          {data?.date && <Tag color="orange">{data.date}</Tag>}
          <Tag>{UNIVERSE_LABEL[data?.universe || universe] || data?.universe || universe}</Tag>
          {data?.mtime && <Text type="secondary" style={{ fontSize: 11 }}>更新 {data.mtime}</Text>}
        </Space>}
        extra={<Space>
          {scanBar}
          <Input size="small" allowClear placeholder="代码 / 名称" value={q}
            onChange={e => setQ(e.target.value)} style={{ width: 150 }} />
          <Space size={4}>
            <Tooltip title="当前场景状态由 False→True 的第一天：日线=up_daily_turn，周线=up_weekly_turn，月线=up_monthly_turn，日周=dw_entry，周月=wm_entry，三周期=entry">
              <Text type="secondary" style={{ fontSize: 12 }}>仅首日</Text>
            </Tooltip>
            <Switch size="small" checked={onlyFirst} onChange={setOnlyFirst} /></Space>
          <Button size="small" icon={<DownloadOutlined />} onClick={exportCSV}>导出 CSV</Button>
        </Space>}
      >
        <Tabs
          size="small" activeKey={layer} onChange={k => setLayer(k as LayerKey)}
          items={LAYERS.map(l => ({
            key: l.key,
            label: `${l.label} ${counts[l.key] ?? 0}`,
          }))}
        />
        {filtered.length ? (
          <Table
            size="small" rowKey="stock_code" dataSource={filtered} columns={columns}
            scroll={{ x: 1250 }} pagination={{ pageSize: 20, size: 'small', showSizeChanger: false }}
          />
        ) : <Empty description={`当前场景（${LAYERS.find(l => l.key === layer)?.label}）没有命中`} />}
      </Card>

      {meta?.scan?.error && <Alert style={{ marginTop: 12 }} type="warning" showIcon message={meta.scan.error} />}
    </div>
  )
}
