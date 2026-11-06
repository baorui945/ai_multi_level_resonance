const C = {
  orange: '#FF6B35', green: '#00e676', red: '#ff5252',
  blue: '#42a5f5', purple: '#ba68c8', gray: '#888',
  grid: 'rgba(255,255,255,0.06)', text: '#888',
}

const base = {
  backgroundColor: 'transparent',
  textStyle: { color: C.text, fontSize: 11 },
  tooltip: {
    trigger: 'axis',
    backgroundColor: '#141430',
    borderColor: 'rgba(255,255,255,0.1)',
    textStyle: { color: '#e0e0e0', fontSize: 11 },
  },
}

export function equityOption(nav: any[], h: number) {
  const dates = nav.map(r => r.date)
  const s = nav.map(r => r[`H${h}`] ?? null)
  const b = nav.map(r => r['沪深300'] ?? null)
  return {
    ...base,
    legend: { data: [`H${h}`, '沪深300'], textStyle: { color: C.text }, top: 0, right: 0 },
    grid: { left: 55, right: 16, top: 30, bottom: 42 },
    xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: C.grid } }, axisLabel: { color: C.text } },
    yAxis: { type: 'log', scale: true, axisLabel: { color: C.text }, splitLine: { lineStyle: { color: C.grid } } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 14, bottom: 8, borderColor: 'transparent', textStyle: { color: C.text } }],
    series: [
      { name: `H${h}`, type: 'line', data: s, showSymbol: false, lineStyle: { width: 1.6, color: C.orange }, itemStyle: { color: C.orange } },
      { name: '沪深300', type: 'line', data: b, showSymbol: false, lineStyle: { width: 1.2, color: C.gray }, itemStyle: { color: C.gray } },
    ],
  }
}

export function drawdownOption(nav: any[], h: number) {
  const dates = nav.map(r => r.date)
  let peak = -Infinity
  const dd = nav.map(r => {
    const v = Number(r[`H${h}`] ?? 0)
    peak = Math.max(peak, v)
    return peak > 0 ? ((v / peak - 1) * 100) : 0
  })
  return {
    ...base,
    grid: { left: 48, right: 12, top: 16, bottom: 28 },
    xAxis: { type: 'category', data: dates, axisLabel: { color: C.text }, axisLine: { lineStyle: { color: C.grid } } },
    yAxis: { type: 'value', max: 0, axisLabel: { color: C.text, formatter: '{value}%' }, splitLine: { lineStyle: { color: C.grid } } },
    series: [{
      name: '回撤', type: 'line', data: dd, showSymbol: false,
      lineStyle: { width: 1, color: C.red },
      areaStyle: { color: 'rgba(255,82,82,0.22)' },
    }],
  }
}

export function annualOption(annual: Record<string, any>, h: number) {
  const mine = annual[String(h)] ?? {}
  const bench = annual['基准'] ?? {}
  const years = Object.keys(mine)
  return {
    ...base,
    legend: { data: [`H${h}`, '沪深300'], textStyle: { color: C.text }, top: 0, right: 0 },
    grid: { left: 48, right: 12, top: 30, bottom: 26 },
    xAxis: { type: 'category', data: years, axisLabel: { color: C.text }, axisLine: { lineStyle: { color: C.grid } } },
    yAxis: { type: 'value', axisLabel: { color: C.text, formatter: '{value}%' }, splitLine: { lineStyle: { color: C.grid } } },
    series: [
      { name: `H${h}`, type: 'bar', data: years.map(y => mine[y] ?? null), itemStyle: { color: C.orange, borderRadius: [3, 3, 0, 0] }, barMaxWidth: 22 },
      { name: '沪深300', type: 'bar', data: years.map(y => bench[y] ?? null), itemStyle: { color: 'rgba(136,136,136,0.5)', borderRadius: [3, 3, 0, 0] }, barMaxWidth: 22 },
    ],
  }
}

export function stockOption(daily: any[], signals: any[]) {
  const dates = daily.map(r => r.date)
  const k = daily.map(r => [r.open, r.close, r.low, r.high])
  const hist = daily.map(r => r.hist)
  const dif = daily.map(r => r.dif)
  const dea = daily.map(r => r.dea)
  const idx: Record<string, number> = {}
  dates.forEach((d, i) => { idx[d] = i })
  const marks = signals
    .filter(s => idx[s.date] != null)
    .map(s => ({
      name: s.dw_entry ? '日周' : s.phase_wm ? '分段' : s.wm_entry ? '周月' : '三周期',
      coord: [idx[s.date], s.close],
      value: s.dw_entry ? 'DW' : s.phase_wm ? 'PH' : s.wm_entry ? 'WM' : 'EN',
      itemStyle: { color: s.dw_entry ? C.orange : s.wm_entry ? C.blue : C.purple },
    }))
  return {
    ...base,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    legend: { data: ['DIF', 'DEA'], textStyle: { color: C.text }, top: 0, right: 0 },
    grid: [
      { left: 58, right: 18, top: 26, height: '52%' },
      { left: 58, right: 18, top: '66%', height: '18%' },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { color: C.text }, axisLine: { lineStyle: { color: C.grid } } },
      { type: 'category', data: dates, gridIndex: 1, axisLabel: { show: false }, axisLine: { lineStyle: { color: C.grid } } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, axisLabel: { color: C.text }, splitLine: { lineStyle: { color: C.grid } } },
      { scale: true, gridIndex: 1, axisLabel: { color: C.text }, splitLine: { lineStyle: { color: C.grid } } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 70, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1], height: 14, bottom: 4, borderColor: 'transparent', textStyle: { color: C.text } },
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', data: k, xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: { color: C.red, color0: C.green, borderColor: C.red, borderColor0: C.green },
        markPoint: { symbolSize: 34, label: { fontSize: 9, color: '#0a0a1a' }, data: marks },
      },
      { name: 'DIF', type: 'line', data: dif, xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, lineStyle: { width: 1, color: C.orange } },
      { name: 'DEA', type: 'line', data: dea, xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, lineStyle: { width: 1, color: C.blue } },
      {
        name: 'hist', type: 'bar', data: hist, xAxisIndex: 1, yAxisIndex: 1,
        itemStyle: { color: (p: any) => (p.value >= 0 ? C.red : C.green) },
      },
    ],
  }
}
