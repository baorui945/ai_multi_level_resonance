import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Empty, Select, Space, Spin, Table, Tag, Typography } from 'antd'
import { DownloadOutlined, HistoryOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { fileURL, fmtSize, getJSON } from '../api'
import type { RunRow } from '../types'

const { Text } = Typography
const KIND_COLOR: Record<string, string> = {
  xlsx: 'green', csv: 'orange', png: 'blue', txt: 'default', json: 'purple',
}

export default function RunsPage() {
  const [rows, setRows] = useState<RunRow[]>([])
  const [loading, setLoading] = useState(true)
  const [kind, setKind] = useState<string>('all')

  useEffect(() => {
    getJSON<{ rows: RunRow[] }>('/api/runs')
      .then(d => setRows(d.rows || []))
      .finally(() => setLoading(false))
  }, [])

  const filtered = useMemo(
    () => kind === 'all' ? rows : rows.filter(r => r.kind === kind),
    [rows, kind])

  const cols: ColumnsType<RunRow> = [
    { title: '文件', dataIndex: 'name', width: 360, ellipsis: true,
      render: (v: string, r: RunRow) => <Space><Tag color={KIND_COLOR[r.kind] || 'default'}>{r.kind}</Tag><Text>{v}</Text></Space> },
    { title: '目录', dataIndex: 'rel', ellipsis: true, render: (v: string) => <Text type="secondary" style={{ fontSize: 11 }}>{v}</Text> },
    { title: '大小', dataIndex: 'size', width: 100, align: 'right', render: (v: number) => fmtSize(v) },
    { title: '更新时间', dataIndex: 'mtime', width: 160 },
    { title: '', key: 'dl', width: 70, align: 'center',
      render: (_: any, r: RunRow) => <Button size="small" type="link" icon={<DownloadOutlined />} href={fileURL(r.rel)} /> },
  ]

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>

  return (
    <Card size="small" className="panel-card"
      title={<Space><HistoryOutlined style={{ color: '#FF6B35' }} /><span>运行记录</span><Tag>{rows.length}</Tag></Space>}
      extra={<Select size="small" value={kind} onChange={setKind} style={{ width: 120 }}
        options={[
          { value: 'all', label: '全部类型' },
          { value: 'xlsx', label: 'Excel' },
          { value: 'csv', label: 'CSV' },
          { value: 'png', label: '图片' },
          { value: 'txt', label: '文本' },
        ]} />}>
      {filtered.length ? (
        <Table size="small" rowKey="rel" dataSource={filtered} columns={cols}
          pagination={{ pageSize: 20, size: 'small', showSizeChanger: false }} />
      ) : <Empty description="output/ 下暂无产物" />}
    </Card>
  )
}
