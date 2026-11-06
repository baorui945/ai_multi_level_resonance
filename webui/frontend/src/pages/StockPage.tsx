import { useEffect, useState } from 'react'
import {
  Alert, Button, Card, Col, Descriptions, Empty, Input, Row, Space, Spin,
  Table, Tag, Typography,
} from 'antd'
import { SearchOutlined, StockOutlined, LineChartOutlined } from '@ant-design/icons'
import ReactECharts from 'echarts-for-react'
import { stockOption } from '../components/Charts'
import { fmt, getJSON } from '../api'
import type { StockData } from '../types'

const { Text } = Typography
const GREEN = '#00e676'
const RED = '#ff5252'
const ORANGE = '#FF6B35'
const BLUE = '#42a5f5'
const PURPLE = '#ba68c8'

export default function StockPage({ initialCode }: { initialCode: string }) {
  const [input, setInput] = useState(initialCode || '601225')
  const [data, setData] = useState<StockData | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const load = (c: string) => {
    const code = (c || '').trim()
    if (!code) return
    setLoading(true); setErr('')
    getJSON<StockData>(`/api/stock/${code}`)
      .then(d => { setData(d); if (!d.available) setErr(d.error || '未找到') })
      .catch(e => { setErr(String(e)); setData(null) })
      .finally(() => setLoading(false))
  }

  useEffect(() => { load(initialCode || '601225') }, [initialCode])

  const lastW = data?.weekly?.[data.weekly.length - 1]
  const lastM = data?.monthly?.[data.monthly.length - 1]
  const lastD = data?.daily?.[data.daily.length - 1]

  const signalCols = [
    { title: '日期', dataIndex: 'date', width: 100 },
    { title: '收盘', dataIndex: 'close', width: 76, align: 'right' as const, render: (v: number) => fmt(v, 2) },
    { title: 'DIF', dataIndex: 'dif', width: 74, align: 'right' as const, render: (v: number) => fmt(v, 3) },
    { title: 'DEA', dataIndex: 'dea', width: 74, align: 'right' as const, render: (v: number) => fmt(v, 3) },
    { title: 'hist', dataIndex: 'hist', width: 74, align: 'right' as const, render: (v: number) => <span style={{ color: v >= 0 ? GREEN : RED }}>{fmt(v, 3)}</span> },
    { title: '日线领先', dataIndex: 'd2w_days', width: 80, align: 'right' as const, render: (v: number) => v == null ? '—' : `${v}天` },
    { title: '周龄', dataIndex: 'week_age_weeks', width: 64, align: 'right' as const, render: (v: number) => v == null ? '—' : fmt(v, 1) },
    {
      title: '月线可用', dataIndex: 'monthly_ready', width: 78, align: 'center' as const,
      render: (v: boolean) => v ? <Tag color="blue">是</Tag> : <Tag>否</Tag>,
    },
    {
      title: '类型', key: 'type', width: 190,
      render: (_: any, r: any) => (
        <Space size={2} wrap>
          {r.dw_entry && <Tag color="orange">日周</Tag>}
          {r.wm_entry && <Tag color="blue">周月</Tag>}
          {r.entry && <Tag color="green">三周期</Tag>}
          {r.phase_wm && r.monthly_ready && <Tag color="purple">分段·周月</Tag>}
          {r.phase_entry && r.monthly_ready && <Tag color="purple">分段·三周期</Tag>}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Card size="small" className="panel-card" style={{ marginBottom: 12 }}>
        <div className="toolbar">
          <Space>
            <StockOutlined style={{ color: ORANGE }} />
            <Text strong>个股明细</Text>
            <Input size="small" value={input} onChange={e => setInput(e.target.value)}
              onPressEnter={() => load(input)} placeholder="6 位代码" style={{ width: 130 }} />
            <Button size="small" type="primary" icon={<SearchOutlined />} onClick={() => load(input)}>查询</Button>
          </Space>
          <span className="spacer" />
          <Space size={4}>
            {['600188', '601088', '601225'].map(c => (
              <Button key={c} size="small" onClick={() => { setInput(c); load(c) }}>{c}</Button>
            ))}
          </Space>
        </div>
      </Card>

      {loading && <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>}
      {err && <Alert type="error" showIcon message="查询失败" description={err} />}
      {!loading && !err && data?.available && (
        <>
          <Card size="small" className="panel-card" style={{ marginBottom: 12 }}>
            <Descriptions size="small" column={6}
              items={[
                { key: 'name', label: '名称', children: <Text strong>{data.name}</Text> },
                { key: 'code', label: '代码', children: data.code },
                { key: 'range', label: '区间', children: `${data.first} ~ ${data.last}` },
                { key: 'sig', label: '信号数', children: <Tag color="orange">{data.signals.length}</Tag> },
                { key: 'w', label: '周线', children: lastW ? <Tag color={lastW.up ? 'success' : 'default'}>{lastW.up ? '上抛' : '未上抛'} · {lastW.period_end}</Tag> : '—' },
                { key: 'm', label: '月线', children: lastM ? <Tag color={lastM.up ? 'success' : 'default'}>{lastM.up ? '上抛' : '未上抛'} · {lastM.period_end}</Tag> : '—' },
              ]} />
          </Card>

          <Card size="small" className="panel-card" style={{ marginBottom: 12 }}
            title={<Space><LineChartOutlined style={{ color: ORANGE }} />价格 + MACD（日线）</Space>}
            extra={lastD ? <Space size={6}>
              <Text type="secondary" style={{ fontSize: 11 }}>最新 {lastD.date}</Text>
              <Tag color={lastD.up ? 'success' : 'default'}>{lastD.up ? '日线上抛' : '日线未上抛'}</Tag>
              <Tag color={lastD.monthly_ready ? 'blue' : 'default'}>{lastD.monthly_ready ? '月线已暖机' : '月线未暖机'}</Tag>
            </Space> : null}>
            <ReactECharts option={stockOption(data.daily, data.signals)} style={{ height: 520 }} notMerge />
          </Card>

          <Card size="small" className="panel-card" title={<Space><span>信号日</span><Tag>{data.signals.length}</Tag></Space>}>
            {data.signals.length ? (
              <Table size="small" rowKey="date" dataSource={data.signals} columns={signalCols}
                scroll={{ x: 820 }} pagination={{ pageSize: 20, size: 'small', showSizeChanger: false }} />
            ) : <Empty description="该股票没有日周/周月/三周期信号" />}
          </Card>
        </>
      )}
      {!loading && !err && data && !data.available && <Empty description={data.error || '未找到'} />}
    </div>
  )
}
