import { useEffect, useState } from 'react'
import { ConfigProvider, theme, App as AntApp, Layout, Menu, Tag, Button, Space, Tooltip } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import {
  LineChartOutlined, ScanOutlined, StockOutlined, HistoryOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import ScanPage from './pages/ScanPage'
import BacktestPage from './pages/BacktestPage'
import StockPage from './pages/StockPage'
import RunsPage from './pages/RunsPage'
import { getJSON, postJSON } from './api'

const { Header, Content } = Layout

type TabKey = 'scan' | 'backtest' | 'stock' | 'runs'

const TABS: TabKey[] = ['scan', 'backtest', 'stock', 'runs']
const tabFromHash = (): TabKey => {
  const h = window.location.hash.replace(/^#\/?/, '')
  return (TABS as string[]).includes(h) ? (h as TabKey) : 'scan'
}

export default function App() {
  const [tab, setTabState] = useState<TabKey>(tabFromHash)
  const [stockCode, setStockCode] = useState('601225')
  const [meta, setMeta] = useState<any>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [refreshing, setRefreshing] = useState(false)

  const loadMeta = () => getJSON<any>('/api/meta').then(setMeta).catch(() => {})
  useEffect(() => { loadMeta() }, [])
  useEffect(() => {
    const onHash = () => setTabState(tabFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const setTab = (k: TabKey) => {
    setTabState(k)
    if (window.location.hash !== `#/${k}`) window.location.hash = `#/${k}`
  }
  const openStock = (code: string) => { setStockCode(code); setTab('stock') }

  const refresh = async () => {
    setRefreshing(true)
    try { await postJSON('/api/refresh'); await loadMeta(); setRefreshKey(k => k + 1) }
    finally { setRefreshing(false) }
  }

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: '#FF6B35',
          colorBgBase: '#0a0a1a',
          colorBgContainer: '#0f0f24',
          colorBgElevated: '#141430',
          colorBorder: 'rgba(255,255,255,0.08)',
          colorText: '#e0e0e0',
          colorTextSecondary: '#888',
          borderRadius: 8,
          fontFamily: `-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',
            'Hiragino Sans GB', 'Microsoft YaHei', 'Helvetica Neue', Helvetica, Arial, sans-serif`,
        },
        components: {
          Table: {
            headerBg: 'rgba(255,255,255,0.04)',
            rowHoverBg: 'rgba(255,107,53,0.06)',
            borderColor: 'rgba(255,255,255,0.04)',
          },
          Menu: {
            darkItemBg: 'transparent',
            darkItemSelectedBg: 'rgba(255,107,53,0.15)',
            darkItemSelectedColor: '#FF6B35',
          },
          Button: { primaryShadow: '0 2px 8px rgba(255,69,0,0.3)' },
        },
      }}
    >
      <AntApp>
        <Layout style={{ minHeight: '100vh' }}>
          <Header style={{
            display: 'flex', alignItems: 'center', background: '#0f0f24',
            borderBottom: '1px solid rgba(255,255,255,0.08)', padding: '0 24px',
          }}>
            <div style={{ color: '#FF6B35', fontWeight: 700, fontSize: 18, marginRight: 28, whiteSpace: 'nowrap' }}>
              📈 MLR · 多周期共振
            </div>
            <Menu
              theme="dark" mode="horizontal" selectedKeys={[tab]}
              onClick={(e) => setTab(e.key as TabKey)}
              style={{ flex: 1, minWidth: 0, background: 'transparent' }}
              items={[
                { key: 'scan', icon: <ScanOutlined />, label: '扫描（Scan）' },
                { key: 'backtest', icon: <LineChartOutlined />, label: '回测（Backtest）' },
                { key: 'stock', icon: <StockOutlined />, label: '个股（Stock）' },
                { key: 'runs', icon: <HistoryOutlined />, label: '运行记录（Runs）' },
              ]}
            />
            <Space size={8}>
              {meta?.as_of && <Tag color="orange">数据日 {meta.as_of}</Tag>}
              <Tooltip title="重新读取 output/ 下最新的扫描与回测产物">
                <Button size="small" icon={<ReloadOutlined />} loading={refreshing}
                  onClick={refresh}>刷新</Button>
              </Tooltip>
            </Space>
          </Header>
          <Content style={{ background: '#0a0a1a', padding: 20, minHeight: 'calc(100vh - 64px)' }}>
            <div className="fade-in" key={refreshKey}>
              {tab === 'scan' && <ScanPage meta={meta} onOpenStock={openStock} />}
              {tab === 'backtest' && <BacktestPage meta={meta} />}
              {tab === 'stock' && <StockPage initialCode={stockCode} />}
              {tab === 'runs' && <RunsPage />}
            </div>
          </Content>
        </Layout>
      </AntApp>
    </ConfigProvider>
  )
}
