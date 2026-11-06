/* MA7 上抛扫描 —— 前端逻辑 */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const state = {
    mode: 'daily', scope: 'hs300', data: null, meta: null,
    sort: { up: ['days', -1], watch: ['day', -1] },
    chart: null, chart2: null, curRow: null, todayOnly: false, cycleEarly: false, lastMs: 0,
  };

  const HINT = {
    daily: { up: 's5>0 且段未结束（允许 1 天回踩）', watch: '候选 1/3、2/3，等 D3 确认' },
    weekly: { up: '周线 s5w>0 且段未结束', watch: '周线候选 1/3、2/3' },
    res: { up: '日线段内 ∩ 周线段内', watch: '日线候选 ∩ 周线段内' },
    cycle: { up: '周期分 ≥ 76 且 R² ≥ 0.55：像正弦波、振幅大、净趋势小', watch: '70–76：有周期特征，拟合稍弱' },
    start: { up: '强周期股触发启动：上穿 MA21 + MACD 柱翻红', watch: '其他周期股触发同一信号（供参考）' },
  };

  const fmt = (v, n = 2) => (v === null || v === undefined || Number.isNaN(v)) ? '—' : Number(v).toFixed(n);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const devCls = (v) => v > 0 ? 'up' : v < 0 ? 'down' : 'muted';
  const devTxt = (v) => (v === null || v === undefined) ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%';

  function el(tag, cls, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html !== undefined) e.innerHTML = html;
    return e;
  }

  async function boot() {
    try {
      state.meta = await fetch('/api/meta').then(r => r.json());
    } catch (e) {
      $('#meta').textContent = '无法连接后端';
      return;
    }
    const sel = $('#scope');
    state.meta.scopes.forEach(s => {
      const o = document.createElement('option');
      o.value = s.k; o.textContent = s.label;
      sel.appendChild(o);
    });
    sel.value = state.scope;
    $('#meta').textContent = '数据 ' + state.meta.asof +
      ' · 市值快照 ' + (state.meta.mv_snapshot || '—') +
      ' · 全市场 ' + state.meta.n_codes + ' 只';

    $('#tabs').addEventListener('click', e => {
      const b = e.target.closest('.tab');
      if (!b || b.dataset.mode === state.mode) return;
      document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t === b));
      state.mode = b.dataset.mode;
      state.todayOnly = false;
      state.cycleEarly = false;
      closeDrawer();
      load();
    });
    sel.addEventListener('change', () => { state.scope = sel.value; state.todayOnly = false; state.cycleEarly = false; closeDrawer(); load(); });
    $('#refresh').addEventListener('click', async () => {
      const btn = $('#refresh');
      btn.disabled = true; btn.textContent = '重载数据…';
      try { await fetch('/api/reload', { cache: 'no-store' }); } catch (e) { /* ignore */ }
      btn.textContent = '重新扫描';
      await load(true);
    });
    $('#exp-up').addEventListener('click', () => exportCodes('up'));
    $('#exp-watch').addEventListener('click', () => exportCodes('watch'));
    $('#dk-close').addEventListener('click', closeDrawer);
    $('#mask').addEventListener('click', closeDrawer);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });
    window.addEventListener('resize', () => {
      if (state.chart) state.chart.resize();
      if (state.chart2) state.chart2.resize();
    });

    const params = new URLSearchParams(location.search);
    const m = params.get('mode');
    if (m && ['daily', 'weekly', 'res', 'cycle', 'start'].includes(m)) {
      state.mode = m;
      document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.mode === m));
    }
    await load();
    const code = params.get('code');
    if (code) { setTimeout(() => openDrawer(code, ''), 200); }
  }

  async function load(force) {
    const btn = $('#refresh');
    btn.disabled = true;
    $('#c-up').textContent = $('#c-watch').textContent = '…';
    $('#tbl-up').innerHTML = '<tbody><tr><td class="empty">扫描中…（首次扫某个范围约 10 秒～1 分钟，之后走缓存）</td></tr></tbody>';
    $('#tbl-watch').innerHTML = '';
    const t0 = performance.now();
    try {
      const q = '/api/scan?mode=' + state.mode + '&scope=' + state.scope + (force ? '&r=' + Date.now() : '');
      state.data = await fetch(q).then(r => r.json());
      if (state.data.error) throw new Error(state.data.error);
    } catch (e) {
      $('#tbl-up').innerHTML = '<tbody><tr><td class="empty">扫描失败：' + esc(e.message) + '</td></tr></tbody>';
      btn.disabled = false;
      return;
    }
    renderStats(Math.round(performance.now() - t0));
    renderTables();
    btn.disabled = false;
  }

  function renderStats(ms) {
    const d = state.data;
    const stats = $('#stats');
    stats.innerHTML = '';
    state.lastMs = ms;
    const add = (label, val) => stats.appendChild(el('span', 'chip', label + ' <b>' + val + '</b>'));
    if (d.mode === 'start') {
      add('强周期启动', d.counts.up);
      add('其他启动', d.counts.watch);
      add('样本', d.counts.total);
      add('耗时', (d.elapsed ?? 0).toFixed(1) + 's' + (d.cached ? ' · 缓存' : ''));
      $('#meta').textContent = '周线 ' + d.asof + ' · ' + d.scope_label +
        ' · 市值快照 ' + (d.mv_snapshot || '—') + ' · 当前页 ' + ms + 'ms';
      return;
    }
    if (d.mode === 'cycle') {
      const chipA = el('span', 'chip clickable' + (state.cycleEarly ? '' : ' active'),
        '强周期 <b>' + d.counts.up + '</b>');
      chipA.title = '显示全部强周期股';
      chipA.addEventListener('click', () => { state.cycleEarly = false; renderStats(state.lastMs); renderTables(); });
      const nStart = d.up.filter(r => r.early || r.pre_start).length;
      const chipE = el('span', 'chip clickable' + (state.cycleEarly ? ' active' : ''),
        '周线启动 <b>' + nStart + '</b>' + (state.cycleEarly ? ' · 已筛选' : ''));
      chipE.title = '启动（上穿 MA21 + MACD 翻红）+ 初上穿（刚站上 MA21）';
      chipE.addEventListener('click', () => { state.cycleEarly = !state.cycleEarly; renderStats(state.lastMs); renderTables(); });
      const chipW = el('span', 'chip', '次级 <b>' + d.counts.watch + '</b>');
      stats.appendChild(chipA);
      stats.appendChild(chipW);
      stats.appendChild(chipE);
      add('样本', d.counts.total);
      add('耗时', (d.elapsed ?? 0).toFixed(1) + 's' + (d.cached ? ' · 缓存' : ''));
      $('#meta').textContent = '周线 ' + d.asof + ' · ' + d.scope_label +
        ' · 市值快照 ' + (d.mv_snapshot || '—') + ' · 当前页 ' + ms + 'ms';
      return;
    }

    // 三个计数 chip 都可点击：上抛中=全部 / 今日确认=只看今日 / 观察名单=跳到观察表
    const chipUp = el('span', 'chip clickable' + (state.todayOnly ? '' : ' active'),
      '上抛中 <b>' + d.counts.up + '</b>');
    chipUp.title = '显示全部上抛中的个股';
    chipUp.addEventListener('click', () => {
      state.todayOnly = false;
      renderStats(state.lastMs);
      renderTables();
    });

    const chipToday = el('span', 'chip clickable' + (state.todayOnly ? ' active' : ''),
      '今日确认 <b>' + d.counts.today + '</b>' + (state.todayOnly ? ' · 已筛选' : ''));
    chipToday.title = '只看今日确认的个股，再点恢复全部';
    chipToday.addEventListener('click', () => {
      state.todayOnly = !state.todayOnly;
      renderStats(state.lastMs);
      renderTables();
    });

    const chipWatch = el('span', 'chip clickable', '观察名单 <b>' + d.counts.watch + '</b>');
    chipWatch.title = '跳到下方的上抛观察名单';
    chipWatch.addEventListener('click', () => {
      const card = document.querySelectorAll('.grid .card')[1];
      if (!card) return;
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      card.classList.add('flash');
      setTimeout(() => card.classList.remove('flash'), 1000);
    });

    stats.appendChild(chipUp);
    stats.appendChild(chipWatch);
    stats.appendChild(chipToday);
    add('范围内', d.counts.total);
    add('耗时', (d.elapsed ?? 0).toFixed(1) + 's' + (d.cached ? ' · 缓存' : ''));
    $('#meta').textContent = '数据 ' + d.asof + ' · ' + d.scope_label +
      ' · 市值快照 ' + (d.mv_snapshot || '—') + ' · 当前页 ' + ms + 'ms';
  }

  function colsCycle() {
    return [
      { k: 'name', t: '名称/代码', cls: 'left' },
      { k: 'mv', t: '市值(亿)' },
      { k: 'score', t: '周期分' },
      { k: 'r2', t: '拟合R²' },
      { k: 'period', t: '周期(周)' },
      { k: 'pos', t: '波谷位' },
      { k: 'wk_cross', t: '上穿MA21' },
      { k: 'wk_red', t: 'MACD翻红' },
      { k: 'phase', t: '阶段' },
      { k: 'dev_ma', t: '距MA21' },
    ];
  }

  function colsStart() {
    return [
      { k: 'name', t: '名称/代码', cls: 'left' },
      { k: 'mv', t: '市值(亿)' },
      { k: 'score', t: '周期分' },
      { k: 'r2', t: '拟合R²' },
      { k: 'pos', t: '波谷位' },
      { k: 'wk_cross', t: '上穿MA21' },
      { k: 'wk_red', t: 'MACD翻红' },
      { k: 'dev_ma', t: '距MA21' },
      { k: 'phase', t: '阶段' },
    ];
  }

  function colsUp() {
    if (state.mode === 'cycle') return colsCycle();
    if (state.mode === 'start') return colsStart();
    const u = state.data.unit === '周' ? '周' : '天';
    const c = [
      { k: 'name', t: '名称/代码', cls: 'left' },
      { k: 'mv', t: '市值(亿)' },
      { k: 'seg_start', t: '段起点' },
      { k: 'days', t: '段内(' + u + ')' },
      { k: 'close', t: '收盘' },
      { k: 'ma7', t: 'MA7' },
      { k: 'dev', t: '距MA7' },
      { k: 's5', t: 's5' },
      { k: 'sig', t: '信号' },
    ];
    if (state.mode === 'res') c.splice(2, 0, { k: 'wk_start', t: '周线段' });
    return c;
  }

  function colsWatch() {
    if (state.mode === 'cycle') return colsCycle();
    if (state.mode === 'start') return colsStart();
    const c = [
      { k: 'name', t: '名称/代码', cls: 'left' },
      { k: 'mv', t: '市值(亿)' },
      { k: 'start', t: '窗口起点' },
      { k: 'day', t: '进度' },
      { k: 'mode', t: '模式' },
      { k: 'close', t: '收盘' },
      { k: 'ma7', t: 'MA7' },
      { k: 'dev', t: '距MA7' },
      { k: 's5', t: 's5' },
    ];
    if (state.mode === 'res') c.push({ k: 'wk_start', t: '周线段' });
    return c;
  }

  function cellHtml(col, r) {
    switch (col.k) {
      case 'name':
        return '<span class="name">' + esc(r.name) + '</span><span class="code">' + r.code + '</span>' +
          (r.today ? '<span class="tag hot">今确认</span>' : '') +
          (r.early ? '<span class="tag green">启动</span>' : '') +
          (!r.early && r.pre_start ? '<span class="tag blue">初上穿</span>' : '');
      case 'dev':
        return '<span class="' + devCls(r.dev) + '">' + devTxt(r.dev) + '</span>';
      case 'sig':
        return r.sig ? esc(r.sig) : '<span class="muted">—</span>';
      case 'day':
        return '<span class="tag ' + (r.day === 2 ? 'green' : '') + '">' + r.day + '/3</span>';
      case 'mode':
        return r.mode ? '<span class="tag blue">' + r.mode + '</span>' : '<span class="muted">—</span>';
      case 'wk_start':
        return r.wk_start ? esc(r.wk_start) + (r.wk_end === '进行中' ? '<span class="tag hot">进行</span>' : '') : '<span class="muted">段外</span>';
      case 'mv':
        return r.mv === null || r.mv === undefined ? '<span class="muted">—</span>' : Number(r.mv).toLocaleString();
      case 's5':
        return fmt(r.s5, 3);
      case 'score':
        return '<span class="' + (r.score >= 85 ? 'up' : '') + '" style="font-weight:600">' + fmt(r.score, 1) + '</span>';
      case 'phase':
        return '<span class="tag ' + (r.phase === '上行' ? 'green' : 'blue') + '">' + (r.phase || '—') + '</span>';
      case 'dev_ma':
        return '<span class="' + devCls(r.dev_ma) + '">' + devTxt(r.dev_ma) + '</span>';
      case 'r2':
        return fmt(r.r2, 3);
      case 'wk_cross':
        return r.wk_cross >= 999 ? '<span class="muted">—</span>' : r.wk_cross + '周前';
      case 'wk_red':
        return r.wk_red >= 999 ? '<span class="muted">—</span>' : r.wk_red + '周前';
      case 'period':
        return fmt(r.period, 0) + '周';
      case 'amp':
        return fmt(r.amp, 1) + '%';
      case 'drift':
        return fmt(r.drift, 2);
      case 'pos':
        return fmt(r.pos, 2);
      default: {
        const v = r[col.k];
        if (typeof v === 'string') return esc(v);
        return fmt(v);
      }
    }
  }

  function renderTable(tblId, rows, cols, sortKey) {
    const tbl = $(tblId);
    const [key, dir] = state.sort[sortKey];
    const data = rows.slice().sort((a, b) => {
      const va = a[key], vb = b[key];
      if (va === vb) return a.code.localeCompare(b.code);
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      return (va > vb ? 1 : -1) * dir;
    });
    const thead = '<thead><tr>' + cols.map(c =>
      '<th class="' + (c.cls || '') + '" data-k="' + c.k + '">' + c.t +
      (key === c.k ? (dir > 0 ? ' ▲' : ' ▼') : '') + '</th>').join('') + '</tr></thead>';
    const tbody = data.length ? data.map(r =>
      '<tr data-code="' + r.code + '" data-name="' + esc(r.name) + '">' +
      cols.map(c => '<td class="' + (c.cls || '') + '">' + cellHtml(c, r) + '</td>').join('') +
      '</tr>').join('')
      : '<tr><td class="empty" colspan="' + cols.length + '">暂无</td></tr>';
    tbl.innerHTML = thead + '<tbody>' + tbody + '</tbody>';
    tbl.querySelectorAll('th').forEach(th => {
      th.addEventListener('click', () => {
        const k = th.dataset.k;
        const s = state.sort[sortKey];
        if (s[0] === k) s[1] = -s[1]; else { s[0] = k; s[1] = -1; }
        renderTable(tblId, rows, cols, sortKey);
      });
    });
    tbl.querySelectorAll('tbody tr[data-code]').forEach(tr => {
      tr.addEventListener('click', () => openDrawer(tr.dataset.code, tr.dataset.name));
    });
    return data;
  }

  // 把某一张表的代码导出成 txt（只导代码，一行一个）
  function exportCodes(which) {
    const ex = state.exportRows || { up: [], watch: [] };
    const seen = new Set();
    const codes = [];
    (ex[which] || []).forEach(r => {
      if (r && r.code && !seen.has(r.code)) { seen.add(r.code); codes.push(r.code); }
    });
    if (!codes.length) { alert('这张表当前没有可导出的代码'); return; }
    const name = 'ma7_' + state.mode + '_' + state.scope + '_' +
      ((state.data && state.data.asof) || '').replace(/-/g, '') + '_' + which + '.txt';
    const blob = new Blob([codes.join('\n') + '\n'], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  function renderTables() {
    const d = state.data;
    const cyc = state.mode === 'cycle';
    $('#h-up').textContent = cyc ? '强周期股' : '上抛过程中';
    $('#h-watch').textContent = cyc ? '次级周期（观察）' : '上抛观察名单';
    const CYC_KEYS = ['name', 'mv', 'score', 'r2', 'period', 'amp', 'drift', 'pos', 'wk_cross', 'wk_red', 'phase', 'dev_ma'];
    if (state.mode === 'start') {
      if (!CYC_KEYS.includes(state.sort.up[0])) state.sort.up = ['score', -1];
      if (!CYC_KEYS.includes(state.sort.watch[0])) state.sort.watch = ['score', -1];
      $('#h-up').textContent = '强周期 · 启动';
      $('#h-watch').textContent = '其他 · 启动（参考）';
      $('#c-up').textContent = d.counts.up;
      $('#c-watch').textContent = d.counts.watch;
      $('#hint-up').textContent = HINT.start.up + ' · 点行看日 K + 周 K';
      $('#hint-watch').textContent = HINT.start.watch + ' · 点行看日 K + 周 K';
      state.exportRows = {
        up: renderTable('#tbl-up', d.up, colsStart(), 'up'),
        watch: renderTable('#tbl-watch', d.watch, colsStart(), 'watch'),
      };
      return;
    }
    if (cyc) {
      if (!CYC_KEYS.includes(state.sort.up[0])) state.sort.up = ['score', -1];
      if (!CYC_KEYS.includes(state.sort.watch[0])) state.sort.watch = ['score', -1];
      const rows = state.cycleEarly
        ? d.up.filter(r => r.early || r.pre_start)
            .sort((a, b) => (b.early - a.early) || (b.score - a.score))
        : d.up;
      $('#c-up').textContent = rows.length;
      $('#c-watch').textContent = d.counts.watch;
      $('#h-up').textContent = state.cycleEarly ? '周线启动（启动 + 初上穿）' : '强周期股';
      $('#hint-up').textContent = state.cycleEarly
        ? '绿「启动」= 上穿 MA21 + MACD 翻红；蓝「初上穿」= 刚站上 MA21、柱未翻红 · 点行看日 K + 周 K'
        : HINT.cycle.up + ' · 点上方「周线启动」筛选 · 点行看日 K + 周 K';
      $('#hint-watch').textContent = HINT.cycle.watch + ' · 点行看日 K + 周 K';
      state.exportRows = {
        up: renderTable('#tbl-up', rows, colsCycle(), 'up'),
        watch: renderTable('#tbl-watch', d.watch, colsCycle(), 'watch'),
      };
      return;
    }
    if (CYC_KEYS.includes(state.sort.up[0])) {
      state.sort.up = ['days', -1];
      state.sort.watch = ['day', -1];
    }
    const rows = state.todayOnly ? d.up.filter(r => r.today) : d.up;
    $('#c-up').textContent = rows.length;
    $('#c-watch').textContent = d.counts.watch;
    $('#hint-up').textContent = state.todayOnly
      ? '只看今日确认（' + rows.length + ' 只）· 点上方「今日确认」恢复全部 · 点行看 K 线'
      : HINT[state.mode].up + ' · 点行看 K 线';
    $('#hint-watch').textContent = HINT[state.mode].watch + ' · 点行看 K 线';
    state.exportRows = {
      up: renderTable('#tbl-up', rows, colsUp(), 'up'),
      watch: renderTable('#tbl-watch', d.watch, colsWatch(), 'watch'),
    };
  }

  function openDrawer(code, name) {
    $('#dk-name').textContent = name || code;
    $('#dk-code').textContent = code;
    $('#mask').classList.add('show');
    $('#drawer').classList.add('show');
    state.curRow = code;
    loadChart(code);
  }

  function closeDrawer() {
    $('#mask').classList.remove('show');
    $('#drawer').classList.remove('show');
  }

  async function fetchKline(code, mark, full) {
    const q = '/api/kline?code=' + code + '&mark=' + mark + (full ? '&full=1' : '');
    const d = await fetch(q).then(r => r.json());
    if (d.error) throw new Error(d.error);
    if (!d.dates || !d.dates.length) throw new Error('该股无 ' + mark + ' K 线数据');
    return d;
  }

  function drawPane(key, boxId, data, mark, showSlider, fitAll) {
    const box = $(boxId);
    box.innerHTML = '';
    const ch = echarts.init(box, null, { renderer: 'canvas' });
    ch.setOption(buildOption(data, mark, showSlider !== false, !!fitAll), true);
    bindPan(ch);
    state[key] = ch;
    return ch;
  }

  async function loadChart(code) {
    // 关键：换股/重开必须先销毁旧实例，否则 canvas 被清掉后旧实例还在画（白屏）
    if (state.chart) { state.chart.dispose(); state.chart = null; }
    if (state.chart2) { state.chart2.dispose(); state.chart2 = null; }

    const isRes = state.mode === 'res';
    const isDual = isRes || state.mode === 'cycle' || state.mode === 'start';
    const mark1 = state.mode === 'weekly' ? 'weekly' : 'daily';
    $('#pane2').hidden = !isDual;
    $('#pane-title-1').textContent = '';
    $('#pane-title-2').textContent = '';
    $('#chart').innerHTML = '<div class="loading">加载 K 线…</div>';
    $('#chart2').innerHTML = isDual ? '<div class="loading">加载周 K…</div>' : '';

    let d1, d2 = null;
    try {
      d1 = await fetchKline(code, mark1);
      // 周期页：周线取全部历史，缩到最小铺满
      if (isDual) d2 = await fetchKline(code, 'weekly', state.mode === 'cycle' || state.mode === 'start');
    } catch (e) {
      $('#chart').innerHTML = '<div class="loading">加载失败：' + esc(e.message) + '</div>';
      return;
    }
    if (d1.name) { $('#dk-name').textContent = d1.name; $('#dk-code').textContent = d1.code; }

    requestAnimationFrame(() => {
      drawPane('chart', '#chart', d1, mark1, !isDual);
      if (isDual && d2) drawPane('chart2', '#chart2', d2, 'weekly', false, state.mode === 'cycle' || state.mode === 'start');
      setTimeout(() => {
        if (state.chart) state.chart.resize();
        if (state.chart2) state.chart2.resize();
      }, 320);

      $('#pane-title-1').textContent = (d1.tf === 'weekly')
        ? '周 K · ' + d1.n_bars + ' 根 · 橙虚线 = 周线段起点 · 三角 = 周线信号周'
        : '日 K · ' + d1.n_bars + ' 根 · 橙虚线 = 日线上抛段起点 · 三角 = 信号日（D3）';
      $('#pane-title-2').textContent = d2
        ? '周 K · ' + d2.n_bars + ' 根 · 橙虚线 = 周线上抛段起点 · 三角 = 周线信号周'
        : '';

      const dTxt = '日 K：MA7（7 日）、MACD(30,90,9)';
      const wTxt = '周 K：MA7（7 周）、MACD(30,90,9) 按周';
      $('#legend').innerHTML = isDual
        ? '上 ' + dTxt + ' ｜ 下 ' + wTxt + ' · 拖动平移 / 滚轮缩放 · 只显示交易日/周'
        : (d1.tf === 'weekly'
            ? '周 K（W-FRI）· 白线 MA7（7 周）· 橙虚线 = 周线段起点 · 三角 = 周线信号周 · ' +
              'MACD(30,90,9) 按周计算 · 拖动平移 / 滚轮缩放 · 只显示交易周'
            : '日 K · 白线 MA7（7 日）· 橙虚线 = 上抛段起点 · 三角 = 信号日 · ' +
              'MACD(30,90,9) · 拖动平移 / 滚轮缩放 · 只显示交易日');
    });
  }

  // 拖拽平移：ECharts 的 inside 平移在开启 tooltip 时会失灵，这里自己接管
  function bindPan(chart) {
    const zr = chart.getZr();
    let dragging = false, lastX = 0;
    const plotW = () => Math.max(80, chart.getWidth() - 56 - 24);
    const isSlider = (e) => e.offsetY > chart.getHeight() - 32;
    zr.on('mousedown', (e) => {
      if (isSlider(e)) return;
      dragging = true; lastX = e.offsetX;
      chart.dispatchAction({ type: 'hideTip' });
    });
    zr.on('mousemove', (e) => {
      if (!dragging) return;
      const dx = e.offsetX - lastX;
      if (!dx) return;
      lastX = e.offsetX;
      const dz = (chart.getOption().dataZoom || [])[0];
      if (!dz || dz.start === undefined) return;
      const s0 = Number(dz.start), e0 = Number(dz.end);
      const span = e0 - s0;
      if (!(span > 0) || span >= 99.9) return;
      const shift = (dx / plotW()) * span;
      const s = Math.max(0, Math.min(100 - span, s0 - shift));
      requestAnimationFrame(() => {
        try { chart.dispatchAction({ type: 'dataZoom', dataZoomIndex: 0, start: s, end: s + span }); } catch (err) { /* ignore */ }
      });
    });
    const stop = () => { dragging = false; };
    zr.on('mouseup', stop);
    zr.on('globalout', stop);
    zr.setCursorStyle('grab');
  }

  function buildOption(d, mark, showSlider = true, fitAll = false) {
    const up = '#ef4444', down = '#22c55e', text = '#8a8a9a', grid = 'rgba(255,255,255,0.05)';
    const axis = {
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      axisLabel: { color: text, fontSize: 11 },
      splitLine: { show: false },
    };
    const dates = d.dates, L = dates.length;

    // 类别轴：只放交易日 → 周末/节假日不占位，K 线之间没有空格。
    // 末尾补 PAD 个隐形占位：最新一根能落在视窗正中，也给向右拖动留出空间。
    const isWeekly = d.tf === 'weekly';
    // fitAll：全部 K 线缩到最小，且**最新一根落在正中** → 右侧补与数据等长的隐形槽
    const PAD = fitAll ? L : (isWeekly ? 70 : 140);
    const padChar = '\u200b';
    const labels = dates.concat(Array.from({ length: PAD }, (_, i) => padChar.repeat(i + 1)));
    const isReal = (v) => typeof v === 'string' && v.length > 0 && v.charAt(0) !== padChar;

    const candles = d.ohlc.map(o => [o[0], o[1], o[2], o[3]]);
    const ma = d.ma7.map(v => (v === null || v === undefined ? '-' : v));
    const vols = d.vol.map((v, i) => ({ value: v, up: d.ohlc[i][1] >= d.ohlc[i][0] }));
    const hist = d.hist.map(v => ({ value: v, up: v >= 0 }));

    // 初始视窗：fitAll 时全量显示；否则以「当下这根」为中心，左右各留 half 根
    const half = isWeekly ? 34 : 70;
    const startIdx = fitAll ? 0 : Math.max(0, L - 1 - half);
    const endIdx = fitAll ? labels.length - 1 : Math.min(labels.length - 1, L - 1 + half);

    // fitAll（看全历史）时把段起点线淡化、去掉标签，避免几十根虚线刷屏
    const markLines = (d.marks || []).map(m => ({
      xAxis: m.date,
      lineStyle: { color: fitAll ? 'rgba(255,107,53,0.25)' : 'rgba(255,107,53,0.6)', type: 'dashed', width: 1 },
      label: { show: !fitAll, color: '#FF6B35', fontSize: 10, position: 'insideEndTop', formatter: '起' },
    }));
    const sigPoints = (d.signals || []).map(s => {
      const i = dates.indexOf(s);
      return i >= 0 ? { coord: [s, d.ohlc[i][2] * 0.985] } : null;
    }).filter(Boolean);

    return {
      backgroundColor: 'transparent',
      animation: false,
      textStyle: { color: text },
      axisPointer: {
        link: [{ xAxisIndex: 'all' }],
        label: { backgroundColor: '#1b1b38', formatter: (p) => (isReal(p && p.value) ? p.value : '') },
      },
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'cross' },
        backgroundColor: 'rgba(15,15,36,0.96)', borderColor: 'rgba(255,107,53,0.4)',
        textStyle: { color: '#ddd', fontSize: 12 },
        formatter: (ps) => {
          const c = ps.find(p => p.seriesName === 'K线');
          const i = c ? c.dataIndex : ps[0].dataIndex;
          if (i === null || i === undefined || i >= L) return '';
          const o = d.ohlc[i];
          return [
            '<b>' + dates[i] + '</b>',
            '开 ' + o[0].toFixed(2) + '　收 ' + o[1].toFixed(2),
            '低 ' + o[2].toFixed(2) + '　高 ' + o[3].toFixed(2),
            'MA7 ' + fmt(d.ma7[i]) + '　DIF ' + fmt(d.dif[i], 3) +
            '　DEA ' + fmt(d.dea[i], 3) + '　MACD ' + fmt(d.hist[i], 3),
          ].join('<br/>');
        },
      },
      grid: showSlider ? [
        { left: 56, right: 24, top: 14, height: '47%' },
        { left: 56, right: 24, top: '56%', height: '11%' },
        { left: 56, right: 24, top: '71%', height: '16%' },
      ] : [
        // 双屏模式没有底部滑块，给时间轴标签留出空间，避免压到下一屏标题
        { left: 56, right: 24, top: 8, height: '46%' },
        { left: 56, right: 24, top: '56%', height: '10%' },
        { left: 56, right: 24, top: '68%', height: '13%' },
      ],
      xAxis: [0, 1, 2].map(i => ({
        type: 'category', gridIndex: i, data: labels, boundaryGap: true,
        axisLine: axis.axisLine, axisTick: { show: false }, splitLine: { show: false },
        axisLabel: i === 2
          ? { ...axis.axisLabel, formatter: (v) => (isReal(v) ? String(v).slice(fitAll ? 2 : 5) : '') }
          : { show: false },
      })),
      yAxis: [
        { scale: true, gridIndex: 0, ...axis, splitLine: { show: true, lineStyle: { color: grid } } },
        { scale: true, gridIndex: 1, splitNumber: 2, ...axis, axisLabel: { show: false } },
        { scale: true, gridIndex: 2, splitNumber: 2, ...axis, axisLabel: { show: false } },
      ],
      dataZoom: (showSlider ? [
        { type: 'inside', xAxisIndex: [0, 1, 2], startValue: startIdx, endValue: endIdx,
          moveOnMouseMove: false, moveOnMouseWheel: false, zoomOnMouseWheel: true },
        { type: 'slider', xAxisIndex: [0, 1, 2], bottom: 6, height: 16,
          startValue: startIdx, endValue: endIdx,
          labelFormatter: (v) => {
            const s = labels[Number(v)];
            return (typeof s === 'string' && s.charAt(0) !== padChar) ? s : '';
          },
          backgroundColor: 'rgba(255,255,255,0.03)', borderColor: 'rgba(255,255,255,0.08)',
          fillerColor: 'rgba(255,107,53,0.12)',
          handleStyle: { color: '#FF6B35' }, textStyle: { color: '#666', fontSize: 10 } },
      ] : [
        { type: 'inside', xAxisIndex: [0, 1, 2], startValue: startIdx, endValue: endIdx,
          moveOnMouseMove: false, moveOnMouseWheel: false, zoomOnMouseWheel: true },
      ]),
      series: [
        {
          name: 'K线', type: 'candlestick', data: candles,
          itemStyle: { color: up, color0: down, borderColor: up, borderColor0: down },
          markLine: { silent: true, symbol: 'none', data: markLines },
          markPoint: {
            symbol: 'triangle', symbolSize: 7, silent: true,
            itemStyle: { color: '#FF6B35' }, label: { show: false }, data: sigPoints,
          },
        },
        { name: 'MA7', type: 'line', data: ma, smooth: true, symbol: 'none',
          lineStyle: { width: 1.2, color: '#ffffff' }, itemStyle: { color: '#fff' }, connectNulls: true },
        ...(d.ma21 && d.ma21.length ? [{
          name: 'MA21', type: 'line',
          data: d.dates.map((dt, i) => (d.ma21[i] === null || d.ma21[i] === undefined ? '-' : d.ma21[i])),
          smooth: true, symbol: 'none', connectNulls: true,
          lineStyle: { width: 1.1, color: '#facc15' }, itemStyle: { color: '#facc15' },
        }] : []),
        {
          name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
          data: vols.map(v => v.value),
          itemStyle: { color: (p) => (vols[p.dataIndex] && vols[p.dataIndex].up ? 'rgba(239,68,68,.55)' : 'rgba(34,197,94,.55)') },
        },
        {
          name: 'MACD', type: 'bar', xAxisIndex: 2, yAxisIndex: 2,
          data: hist.map(v => v.value),
          itemStyle: { color: (p) => (hist[p.dataIndex] && hist[p.dataIndex].up ? 'rgba(239,68,68,.8)' : 'rgba(34,197,94,.8)') },
        },
        { name: 'DIF', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: d.dif,
          symbol: 'none', lineStyle: { width: 1, color: '#fbbf24' } },
        { name: 'DEA', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: d.dea,
          symbol: 'none', lineStyle: { width: 1, color: '#60a5fa' } },
      ],
    };
  }

  boot();
})();
