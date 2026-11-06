import { useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Card, Col, Empty, Input, Row, Segmented, Space, Spin,
  Table, Tabs, Tag, Typography,
} from 'antd'
import {
  LineChartOutlined, TrophyOutlined, RiseOutlined, FallOutlined,
  ThunderboltOutlined, BarChartOutlined, DownloadOutlined, ClockCircleOutlined,
  FundOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import ReactECharts from 'echarts-for-react'
import MetricCard from '../components/MetricCard'
import { annualOption, drawdownOption, equityOption } from '../components/Charts'
import { fileURL, fmt, fmtPct, getJSON } from '../api'
import type { BacktestData, PerfRow, TradeRow } from '../types'

const { Text } = Typography
const GREEN = '#00e676'
const RED = '#ff5252'
const ORANGE = '#FF6B35'
const BLUE = '#42a5f5'
const PURPLE = '#ba68c8'

export default function BacktestPage({ meta }: { meta: any }) {
  const [data, setData] = useState<BacktestData | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [h, setH] = useState<number>(40)
  const [tradeQ, setTradeQ] = useState('')

  useEffect(() => {
    setLoading(true)
    getJSON<BacktestData>('/api/backtest')
      .then(d => {
        setData(d)
        setErr(d.available ? '' : (d.error || '回测数据不可用'))
        if (d.available && d.main != null) setH(Number(d.main))
      })
      .catch(e => setErr(String(e)))
      .finally(() => setLoading(false))
  }, [])

  const perf = data?.perf ?? []
  const current = perf.find(p => Number(p.horizon) === Number(h))
  const trades = data?.trades?.[String(h)] ?? []
  const signals = data?.signals ?? []
  const nav = data?.nav ?? []

  const tradeRows = useMemo(() => {
    const kw = tradeQ.trim()
    if (!kw) return trades
    return trades.filter(t => `${t.code}${t.entry}${t.exit}`.includes(kw))
  }, [trades, tradeQ])

  const perfCols: ColumnsType<PerfRow> = [
    { title: '持有期', dataIndex: 'horizon', width: 64, render: (v: number, r: PerfRow) => r.main === '★' ? <Tag color="orange">H{v}</Tag> : `H${v}` },
    { title: '信号', dataIndex: 'signals', width: 64 },
    { title: '交易', dataIndex: 'trades', width: 64 },
    { title: '年化', dataIndex: 'annual', width: 78, align: 'right', render: (v: number) => <span style={{ color: v >= 0 ? GREEN : RED }}>{fmtPct(v)}</span> },
    { title: 'α', dataIndex: 'alpha', width: 72, align: 'right', render: (v: number) => fmtPct(v) },
    { title: 'α-t', dataIndex: 'alpha_t', width: 60, align: 'right', render: (v: number) => fmt(v, 2) },
    { title: '夏普', dataIndex: 'sharpe', width: 64, align: 'right', render: (v: number) => fmt(v, 2) },
    { title: '回撤', dataIndex: 'mdd', width: 78, align: 'right', render: (v: number) => <span style={{ color: RED }}>{fmtPct(v)}</span> },
    { title: 'Calmar', dataIndex: 'calmar', width: 68, align: 'right', render: (v: number) => fmt(v, 2) },
    { title: '利用率', dataIndex: 'util', width: 68, align: 'right', render: (v: number) => fmt(v, 1, '%') },
    { title: '逐笔净均', dataIndex: 'avg_net', width: 82, align: 'right', render: (v: number) => <span style={{ color: v >= 0 ? GREEN : RED }}>{fmtPct(v)}</span> },
    { title: '胜率', dataIndex: 'win', width: 64, align: 'right', render: (v: number) => fmt(v, 1, '%') },
  ]

  const tradeCols: ColumnsType<TradeRow> = [
    { title: '代码', dataIndex: 'code', width: 76, fixed: 'left' },
    { title: '买入日', dataIndex: 'entry', width: 96 },
    { title: '卖出日', dataIndex: 'exit', width: 96 },
    { title: '持有', dataIndex: 'hold', width: 56, align: 'right' },
    { title: '买入价', dataIndex: 'entry_px', width: 72, align: 'right', render: (v: number) => fmt(v, 3) },
    { title: '卖出价', dataIndex: 'exit_px', width: 72, align: 'right', render: (v: number) => fmt(v, 3) },
    {
      title: '净收益', dataIndex: 'net', width: 82, align: 'right',
      sorter: (a, b) => (a.net || 0) - (b.net || 0),
      render: (v: number) => <Tag color={v >= 0 ? 'success' : 'error'} style={{ minWidth: 66, textAlign: 'center' }}>{fmtPct(v)}</Tag>,
    },
    { title: '最大浮盈', dataIndex: 'mfe', width: 78, align: 'right', render: (v: number) => <span style={{ color: GREEN }}>{fmtPct(v)}</span> },
    { title: '最大浮亏', dataIndex: 'mae', width: 78, align: 'right', render: (v: number) => <span style={{ color: RED }}>{fmtPct(v)}</span> },
  ]

  const signalCols: ColumnsType<any> = [
    { title: '代码', dataIndex: 'code', width: 80 },
    { title: '名称', dataIndex: 'name', width: 90 },
    { title: '信号日', dataIndex: 'date', width: 100 },
    { title: '收盘', dataIndex: 'close', width: 76, align: 'right', render: (v: number) => fmt(v, 2) },
    { title: 'DIF', dataIndex: 'dif', width: 74, align: 'right', render: (v: number) => fmt(v, 3) },
    { title: 'DEA', dataIndex: 'dea', width: 74, align: 'right', render: (v: number) => fmt(v, 3) },
    { title: 'hist', dataIndex: 'hist', width: 74, align: 'right', render: (v: number) => <span style={{ color: v >= 0 ? GREEN : RED }}>{fmt(v, 3)}</span> },
    { title: '金叉后天数', dataIndex: 'bars_since_cross', width: 96, align: 'right' },
  ]

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>
  if (err) return <Alert type="error" showIcon message="回测数据不可用" description={err} />
  if (!data?.available) return <Empty description={data?.error || '暂无回测产物'} />

  const years = Object.keys(data.annual?.[String(h)] ?? {})
  const annualRows = years.map(y => ({
    year: y,
    value: data.annual?.[String(h)]?.[y] ?? null,
    bench: data.annual?.['基准']?.[y] ?? null,
  }))

  const cards = current ? [
    { icon: <RiseOutlined />, value: fmtPct(current.annual, 2), label: '年化收益', color: GREEN, bg: 'rgba(0,230,118,0.08)' },
    { icon: <BarChartOutlined />, value: fmt(current.sharpe, 2), label: '夏普比率', color: PURPLE, bg: 'rgba(186,104,200,0.08)' },
    { icon: <FallOutlined />, value: fmtPct(current.mdd, 2), label: '最大回撤', color: RED, bg: 'rgba(255,82,82,0.08)' },
    { icon: <TrophyOutlined />, value: fmtPct(current.alpha, 2), label: 'α（年化）', color: BLUE, bg: 'rgba(66,165,245,0.08)', subtext: `t=${fmt(current.alpha_t, 2)}` },
    { icon: <ThunderboltOutlined />, value: fmt(current.win, 1, '%'), label: '逐笔胜率', color: ORANGE, bg: 'rgba(255,152,0,0.08)' },
    { icon: <FundOutlined />, value: fmt(current.util, 1, '%'), label: '资金利用率', color: PURPLE, bg: 'rgba(186,104,200,0.06)' },
    { icon: <ClockCircleOutlined />, value: String(current.trades), label: '交易笔数', color: BLUE, bg: 'rgba(66,165,245,0.06)', subtext: `信号 ${current.signals}` },
    { icon: <LineChartOutlined />, value: fmtPct(current.avg_net, 2), label: '逐笔净均', color: ORANGE, bg: 'rgba(255,107,53,0.08)', subtext: `t=${fmt(current.net_t, 2)}` },
  ] : []

  const overview = (
    <div>
      <Row gutter={[10, 10]} style={{ marginBottom: 14 }}>
        {cards.map((c, i) => <Col xs={12} sm={8} md={6} lg={3} key={i}><MetricCard {...c} /></Col>)}
      </Row>
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col xs={24} lg={16}>
          <Card size="small" className="panel-card" title={<Space><LineChartOutlined style={{ color: ORANGE }} />净值曲线（对数轴）</Space>}>
            <ReactECharts option={equityOption(nav, h)} style={{ height: 330 }} notMerge />
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card size="small" className="panel-card" title={<Space><FallOutlined style={{ color: RED }} />回撤（%）</Space>}>
            <ReactECharts option={drawdownOption(nav, h)} style={{ height: 330 }} notMerge />
          </Card>
        </Col>
      </Row>
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col xs={24} lg={12}>
          <Card size="small" className="panel-card" title="年度收益（%）">
            <ReactECharts option={annualOption(data.annual, h)} style={{ height: 260 }} notMerge />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" className="panel-card" title="年度收益明细">
            <Table size="small" rowKey="year" pagination={false} dataSource={annualRows}
              columns={[
                { title: '年份', dataIndex: 'year', width: 70 },
                { title: `H${h}`, dataIndex: 'value', align: 'right', render: (v: number) => v == null ? '—' : <span style={{ color: v >= 0 ? GREEN : RED }}>{fmtPct(v)}</span> },
                { title: '沪深300', dataIndex: 'bench', align: 'right', render: (v: number) => v == null ? '—' : <span style={{ color: v >= 0 ? GREEN : RED }}>{fmtPct(v)}</span> },
              ]} />
          </Card>
        </Col>
      </Row>
      <Card size="small" className="panel-card" title="持有期对比">
        <Table size="small" rowKey="horizon" pagination={false} dataSource={perf} columns={perfCols} />
      </Card>
    </div>
  )

  const tradesTab = (
    <Card size="small" className="panel-card"
      title={<Space><span>逐笔明细 H{h}</span><Tag>{tradeRows.length} 笔</Tag></Space>}
      extra={<Input size="small" allowClear placeholder="代码 / 日期" value={tradeQ}
        onChange={e => setTradeQ(e.target.value)} style={{ width: 160 }} />}>
      {tradeRows.length ? <Table size="small" rowKey={(r) => `${r.code}-${r.entry}-${r.exit}`}
        dataSource={tradeRows} columns={tradeCols} scroll={{ x: 900 }}
        pagination={{ pageSize: 20, size: 'small', showSizeChanger: false }} />
        : <Empty description="没有逐笔数据" />}
    </Card>
  )

  const signalsTab = (
    <Card size="small" className="panel-card"
      title={<Space><span>共振信号</span><Tag>{signals.length} 条</Tag></Space>}>
      <Table size="small" rowKey={(r) => `${r.code}-${r.date}`} dataSource={signals}
        columns={signalCols} scroll={{ x: 760 }}
        pagination={{ pageSize: 20, size: 'small', showSizeChanger: false }} />
    </Card>
  )

  return (
    <div>
      <Card size="small" className="panel-card" style={{ marginBottom: 12 }}>
        <div className="toolbar">
          <Space>
            <Text strong>回测产物</Text>
            <Tag color="orange">{data.file}</Tag>
            <Text type="secondary" style={{ fontSize: 11 }}>更新 {data.mtime}</Text>
          </Space>
          <span className="spacer" />
          <Segmented size="small" value={h}
            options={(data.horizons ?? []).map(x => ({ label: `H${x}`, value: Number(x) }))}
            onChange={v => setH(Number(v))} />
          {data.file && <Button size="small" icon={<DownloadOutlined />} href={fileURL(data.file)}>下载 Excel</Button>}
        </div>
      </Card>
      <Tabs
        size="small" defaultActiveKey="overview"
        items={[
          { key: 'overview', label: '概览', children: overview },
          { key: 'trades', label: `逐笔（${trades.length}）`, children: tradesTab },
          { key: 'signals', label: `信号（${signals.length}）`, children: signalsTab },
        ]}
      />
      {meta?.backtest?.error && <Alert style={{ marginTop: 12 }} type="warning" showIcon message={meta.backtest.error} />}
    </div>
  )
}
