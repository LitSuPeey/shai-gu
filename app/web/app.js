// ========= 通用工具 =========
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));


// 表单数值读取（缺省兜底）
function num(sel, dflt) {
  const el = $(sel);
  if (!el) return dflt;
  const v = parseFloat(el.value);
  return Number.isFinite(v) ? v : dflt;
}

// CSV 下载：直接从结果表格提取（BOM 头保证 Excel 中文不乱码）
function downloadCsv(tableId, filename) {
  const t = $('#' + tableId);
  if (!t) return;
  const head = [...t.querySelectorAll('thead th')].map(th => th.textContent);
  const body = [...t.querySelectorAll('tbody tr')]
    .filter(tr => tr.children.length === head.length || head.length === 0)
    .map(tr => [...tr.querySelectorAll('td')].map(td => {
      const txt = td.textContent.replace(/\s+/g, ' ').trim();
      return /[",\n]/.test(txt) ? '"' + txt.replace(/"/g, '""') + '"' : txt;
    }).join(','));
  if (!body.length) { alert('当前没有结果可下载'); return; }
  const csv = '\ufeff' + [head.join(','), ...body].join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

// 波动幅度开关：similar 模式才显示阈值输入
function bindSimilarVolToggle() {
  const sel = $('#similarVolMode'), wrap = $('#similarVolThreshWrap');
  if (sel && wrap) {
    const sync = () => { wrap.hidden = sel.value !== 'similar'; };
    sel.addEventListener('change', sync); sync();
  }
}

// 带超时的 fetch：避免后端卡住时前端无限等待导致 loading 界面卡死
// run 类长任务挂到全局 _activeFetchCtrl，取消按钮可中断
let _activeFetchCtrl = null;   // 当前运行中的可取消请求
let _userCancelled = false;    // 区分「用户取消」和「超时」
let _bgTask = null;            // 后台运行中的任务 {label, failed, settled}

function fetchJSON(url, options = {}, timeoutMs = 600000, cancellable = false) {
  // 已有后台任务在跑时不允许再叠一个长任务（后端进度状态是全局单槽，并发会互踩）
  if (cancellable && _bgTask) {
    return Promise.reject(new Error('已有后台任务在运行（见右下角角标，点它可恢复进度窗口），等它完成或取消后再开始新任务'));
  }
  const ctrl = new AbortController();
  if (cancellable) { _activeFetchCtrl = ctrl; _userCancelled = false; }
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  return fetch(url, { ...options, signal: ctrl.signal })
    .then(r => r.json())
    .catch(e => {
      if (_bgTask && _activeFetchCtrl === ctrl) _bgTask.failed = true;
      if (e && e.name === 'AbortError') {
        throw new Error(_userCancelled ? '已取消' : `请求超时（超过 ${Math.round(timeoutMs / 1000)} 秒）`);
      }
      throw e;
    })
    .finally(() => {
      clearTimeout(timer);
      if (cancellable && _activeFetchCtrl === ctrl) {
        _activeFetchCtrl = null;
        if (_bgTask) _bgTask.settled = true;   // 后台任务收尾：hideOverlay 时据此弹完成提示
      }
    });
}

const API = {
  status: () => fetchJSON('/api/status', {}, 15000),
  stocks: p => fetchJSON('/api/stocks?' + new URLSearchParams(p || {}), {}, 15000),
  kline: (code, days = 180) => fetchJSON(`/api/kline?code=${encodeURIComponent(code)}&days=${days}`, {}, 30000),
  strategiesList: () => fetchJSON('/api/strategies/list', {}, 15000),
  strategiesRun: body => fetchJSON('/api/strategies/run', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 600000, true),
  conditionsList: () => fetchJSON('/api/conditions/list', {}, 15000),
  conditionsRun: body => fetchJSON('/api/conditions/run', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 600000, true),
  patternRun: body => fetchJSON('/api/pattern/run', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 600000, true),
  antRun: body => fetchJSON('/api/ant/run', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 600000, true),
  ant1000Run: body => fetchJSON('/api/ant1000/run', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 3600000, true),
  ant1000Cache: () => fetchJSON('/api/ant1000/cache', {}, 30000),
  ant1000Kline: (code, years) => fetchJSON(`/api/ant1000/kline?code=${encodeURIComponent(code)}&years=${years || 20}`, {}, 60000),
  similarRun: body => fetchJSON('/api/similar/run', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 600000, true),
  alertRun: body => fetchJSON('/api/alert/run', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 600000, true),
  alertReport: body => fetchJSON('/api/alert/report', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 60000),
  ticker: force => fetchJSON('/api/ticker' + (force ? '?refresh=1' : ''), {}, 30000),
  cycleRun: body => fetchJSON('/api/cycle/analyze', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 120000),
  futuresScan: body => fetchJSON('/api/futures/scan', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 300000, true),
  futuresSectors: () => fetchJSON('/api/futures/sectors', {}, 15000),
  biliMeta: () => fetchJSON('/api/bili/meta', {}, 20000),
  biliUps: () => fetchJSON('/api/bili/ups', {}, 15000),
  biliUpAdd: body => fetchJSON('/api/bili/ups/add', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 60000),
  biliUpRemove: body => fetchJSON('/api/bili/ups/remove', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 15000),
  biliUpRename: body => fetchJSON('/api/bili/ups/rename', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 30000),
  biliScan: body => fetchJSON('/api/bili/scan', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 3600000, true),
  biliSummary: body => fetchJSON('/api/bili/summary', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 60000),
  biliSessionReset: () => fetchJSON('/api/bili/session/reset', { method: 'POST' }, 60000),
  biliCacheClear: () => fetchJSON('/api/bili/cache/clear', { method: 'POST' }, 30000),
  biliCredSet: body => fetchJSON('/api/bili/cred/set', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 90000),
  biliCredClear: () => fetchJSON('/api/bili/cred/clear', { method: 'POST' }, 60000),
  biliAsrState: () => fetchJSON('/api/bili/asr/state', {}, 30000),
  biliAsrHelp: () => fetchJSON('/api/bili/asr/help', {}, 20000),
  biliArchive: () => fetchJSON('/api/bili/archive', {}, 20000),
  biliArchiveClear: body => fetchJSON('/api/bili/archive/clear', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}) }, 30000),
  divtimingStats: () => fetchJSON('/api/divtiming/stats', {}, 60000),
  divtimingWeek: dateStr => fetchJSON('/api/divtiming/week' + (dateStr ? `?date_str=${encodeURIComponent(dateStr)}` : ''), {}, 60000),
  divtimingFullMarket: () => fetchJSON('/api/divtiming/full_market', {}, 20000),
  syncStart: body => fetchJSON('/api/sync/start', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }, 30000),
  syncStop: () => fetchJSON('/api/sync/stop', { method: 'POST' }, 10000),
  syncProgress: () => fetchJSON('/api/sync/progress', {}, 15000),
  progress: () => fetchJSON('/api/progress', {}, 15000),
  cancel: () => fetchJSON('/api/cancel', { method: 'POST' }, 10000),
  refreshMeta: () => fetchJSON('/api/sync/refresh_meta', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' }, 30000),
  freshness: () => fetchJSON('/api/data/freshness', {}, 15000),
};

// ========= 表情包系统（30 张全量接入） =========
const MEMES = {
  welcome: ['memes/表情套组_默认_你好！.png', 'memes/开心.png', 'memes/表情套组_卫戍专用_happy.png'],
  empty: ['memes/表情套组_默认_？？？.png', 'memes/表情套组_默认_换换项目！.png',
          'memes/表情套组_虫动_？？？.png', 'memes/表情套组_虫动_换换项目！.png'],
  success: ['memes/表情套组_默认_合作愉快！.png', 'memes/表情套组_虫动_合作愉快！.png',
            'memes/表情套组_卫戍专用_noproblem.png', 'memes/表情套组_卫戍专用_respect.png',
            'memes/表情套组_卫戍专用_cooperate.png'],
  error: ['memes/表情套组_默认_对不起！.png', 'memes/表情套组_虫动_对不起！.png',
          'memes/表情套组_卫戍专用_sad.png', 'memes/表情套组_卫戍专用_sorry.png',
          'memes/表情套组_卫戍专用_scared.png', 'memes/表情套组_卫戍专用_dying.png'],
  ask: ['memes/表情套组_默认_你请先选！.png', 'memes/表情套组_默认_我想先选！.png',
        'memes/表情套组_卫戍专用_thinking.png', 'memes/表情套组_卫戍专用_call.png',
        'memes/表情套组_默认_请快些！.png'],
  thanks: ['memes/表情套组_默认_谢谢！.png', 'memes/表情套组_卫戍专用_thanks.png'],
  farewell: ['memes/表情套组_默认_再见！.png', 'memes/表情套组_卫戍专用_playingcool.png'],
};
// 加载轮播池：全部 30 张表情包都在等待时轮换登场
const MEME_ALL = [
  'memes/开心.png',
  'memes/表情套组_默认_你好！.png', 'memes/表情套组_默认_再见！.png',
  'memes/表情套组_默认_合作愉快！.png', 'memes/表情套组_默认_对不起！.png',
  'memes/表情套组_默认_很快就好！.png', 'memes/表情套组_默认_我想先选！.png',
  'memes/表情套组_默认_换换项目！.png', 'memes/表情套组_默认_要上啦！.png',
  'memes/表情套组_默认_请快些！.png', 'memes/表情套组_默认_你请先选！.png',
  'memes/表情套组_默认_？？？.png', 'memes/表情套组_默认_谢谢！.png',
  'memes/表情套组_虫动_合作愉快！.png', 'memes/表情套组_虫动_对不起！.png',
  'memes/表情套组_虫动_很快就好！.png', 'memes/表情套组_虫动_换换项目！.png',
  'memes/表情套组_虫动_？？？.png',
  'memes/表情套组_卫戍专用_call.png', 'memes/表情套组_卫戍专用_cooperate.png',
  'memes/表情套组_卫戍专用_dying.png', 'memes/表情套组_卫戍专用_happy.png',
  'memes/表情套组_卫戍专用_noproblem.png', 'memes/表情套组_卫戍专用_playingcool.png',
  'memes/表情套组_卫戍专用_respect.png', 'memes/表情套组_卫戍专用_sad.png',
  'memes/表情套组_卫戍专用_scared.png', 'memes/表情套组_卫戍专用_sorry.png',
  'memes/表情套组_卫戍专用_thanks.png', 'memes/表情套组_卫戍专用_thinking.png',
];
const pickMeme = (kind, i = 0) => {
  const pool = MEMES[kind] || MEMES.empty;
  return pool[i % pool.length];
};
const pickMemeRandom = kind => {
  const pool = MEMES[kind] || MEMES.empty;
  return pool[Math.floor(Math.random() * pool.length)];
};
const thsLink = code => `https://stockpage.10jqka.com.cn/${code}/`;

const state = { range: 'all', sectors: [], syncPoll: null, syncing: false };

// ========= 表格渲染（动态列） =========
const PCT_RE = /涨跌幅|幅度|涨跌|超额|收益|振幅|回撤|回报/i;
function renderTable(tableId, rows, opts = {}) {
  const t = $('#' + tableId);
  if (!t) return;
  const thead = t.querySelector('thead');
  const tbody = t.querySelector('tbody');
  if (!rows || rows.length === 0) {
    thead.innerHTML = '';
    tbody.innerHTML = `<tr><td style="text-align:center; padding:26px; color:var(--ink-faint);">— 暂无结果 —</td></tr>`;
    return;
  }
  const cols = Object.keys(rows[0]);
  thead.innerHTML = '<tr>' + cols.map(c => `<th>${c}</th>`).join('') + '</tr>';
  const MAX = opts.max || 300;
  const shown = rows.slice(0, MAX);
  // 点击行为说明行：有股票代码列的表格才显示（CSV 导出时因列数不符会被自动过滤）
  const hasCodeCol = cols.some(c => c === '代码' || c === 'code');
  const tipRow = hasCodeCol
    ? `<tr class="table-tip"><td colspan="${cols.length}">💡 点击代码＝快速查看（站内 K 线）· 点击名称/简称＝详细查询（同花顺新窗口）</td></tr>`
    : '';
  tbody.innerHTML = tipRow + shown.map(r => '<tr>' + cols.map(c => {
    const v = r[c];
    if (c === '代码' || c === 'code') {
      // 代码：站内切换到 K 线查看（快速查看）
      return `<td><a href="#" class="stock-link code-link" data-code="${v}" title="快速查看 · 站内看K线">${v}</a></td>`;
    }
    if (c === '名称' || c === 'name') {
      const code = r['代码'] || r['code'] || '';
      // 名称：新标签页打开同花顺（详细查询）
      return `<td><a href="${thsLink(code)}" target="_blank" rel="noopener noreferrer" class="stock-link name-link" data-code="${code}" title="详细查询 · 同花顺新窗口">${v ?? ''}</a></td>`;
    }
    if (c === '简称') {
      // 简称（预警国家队等表格）：同样跳同花顺（详细查询）
      const code = r['代码'] || r['code'] || '';
      if (!code) return `<td>${v ?? ''}</td>`;
      return `<td><a href="${thsLink(code)}" target="_blank" rel="noopener noreferrer" class="stock-link name-link" title="详细查询 · 同花顺新窗口">${v ?? ''}</a></td>`;
    }
    if (typeof v === 'number') {
      const isPct = PCT_RE.test(c);
      let cls = '';
      if (isPct) cls = v > 0 ? 'up' : (v < 0 ? 'down' : '');
      return `<td class="num ${cls}" title="${v}">${v.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}</td>`;
    }
    if (v == null || v === '') return `<td style="color:var(--ink-faint)">—</td>`;
    return `<td title="${escHtml(String(v))}">${v}</td>`;
  }).join('') + '</tr>').join('');
  if (rows.length > shown.length) {
    tbody.innerHTML += `<tr><td colspan="${cols.length}" style="text-align:center; color:var(--ink-faint); padding:10px;">…显示前 ${shown.length} / 共 ${rows.length} 行</td></tr>`;
  }
  // 代码链接：站内切 K 线（快速查看），经 switchTab 记录来源界面供「↩ 退回」
  tbody.querySelectorAll('.code-link').forEach(a => {
    a.addEventListener('click', e => {
      e.preventDefault();
      switchTab('kline');
      $('#klineCode').value = a.dataset.code;
      loadKline();
    });
  });
  // 名称链接：保持原生 target=_blank 新标签页打开同花顺，无需 JS 干预
}

// ========= 表情包反馈框 =========
function showMeme(id, kind, big, sub) {
  const box = $('#' + id);
  box.hidden = false;
  box.className = 'result-meme ' + kind;
  box.querySelector('img').src = pickMemeRandom(kind);
  box.querySelector('.meme-big').textContent = big;
  box.querySelector('.meme-sub').textContent = sub || '';
}
function hideMeme(id) { const b = $('#' + id); if (b) b.hidden = true; }

// ========= 加载进度条轮询 + 表情包轮播 =========
let _progressTimer = null;
let _memeTimer = null;
let _memeIdx = 0;

function startProgressPolling() {
  stopProgressPolling();
  $('#overlayFill').style.width = '0%';
  $('#overlayProgressText').textContent = '';
  _progressTimer = setInterval(async () => {
    try {
      const p = await API.progress();
      if (p.status === 'running') {
        const pct = Math.max(0, Math.min(100, Math.round((p.frac || 0) * 100)));
        $('#overlayFill').style.width = pct + '%';
        $('#overlayProgressText').textContent =
          pct + '%' + (p.msg ? ' · ' + p.msg : '');
      }
    } catch (e) { /* 忽略轮询异常 */ }
  }, 400);
}
function stopProgressPolling() {
  if (_progressTimer) { clearInterval(_progressTimer); _progressTimer = null; }
}

// loading 时所有表情包轮流登场（每 2.2s 换一张，不连续重复）
function startMemeCarousel() {
  stopMemeCarousel();
  _memeIdx = Math.floor(Math.random() * MEME_ALL.length);
  _memeTimer = setInterval(() => {
    _memeIdx = (_memeIdx + 1) % MEME_ALL.length;
    const img = $('#overlayImg');
    img.classList.add('swap');
    setTimeout(() => {
      img.src = MEME_ALL[_memeIdx];
      img.classList.remove('swap');
    }, 180);
  }, 2200);
}
function stopMemeCarousel() {
  if (_memeTimer) { clearInterval(_memeTimer); _memeTimer = null; }
}

// 取消正在运行的任务：通知后端停算 + 中断前端请求
async function cancelRunning() {
  _userCancelled = true;
  _bgTask = null;          // 用户主动取消：清掉后台状态，不再弹「已完成」提示
  hideBgPill();
  try { await API.cancel(); } catch (e) { /* 忽略 */ }
  if (_activeFetchCtrl) {
    try { _activeFetchCtrl.abort(); } catch (e) { /* 忽略 */ }
  }
  $('#overlayText').textContent = '已取消任务';
  const img = $('#overlayImg');
  stopMemeCarousel();
  img.src = pickMemeRandom('farewell');
  setTimeout(hideOverlay, 900);
}

function showOverlay(kind, text, opts = {}) {
  $('#overlayImg').src = pickMemeRandom(kind);
  $('#overlayText').textContent = text || '';
  $('#overlay').hidden = false;
  const cancelBtn = $('#btnCancelTask');
  // 只有筛选类长任务才显示取消按钮（初始加载/同步不显示）
  cancelBtn.hidden = !opts.cancellable;
  // 后台运行按钮与取消按钮同进退：短操作（清缓存等）没有后台运行的必要
  const bgBtn = $('#btnBgRun');
  if (bgBtn) bgBtn.hidden = !opts.cancellable;
  startProgressPolling();
  if (opts.cancellable) startMemeCarousel();
}
function hideOverlay() {
  $('#overlay').hidden = true;
  stopProgressPolling();
  stopMemeCarousel();
  // 后台任务此时收尾：右下角弹完成提示（正常完成/失败文案不同）
  if (_bgTask && _bgTask.settled) bgFinish();
}

// ========= 后台运行：收起加载遮罩，任务照常跑，右下角角标提示状态 =========
function bgLabel() {
  let t = ($('#overlayText').textContent || '任务').split('…')[0];
  t = t.replace(/（[^）]*）/g, '');   // 去成对括号（避免截出「正在抓取（串行限速…」这类残缺标签）
  t = t.split('，')[0].trim();
  if (!t) t = '任务';
  return t.length > 22 ? t.slice(0, 22) + '…' : t;
}
function hideBgPill() {
  const p = $('#bgPill');
  if (p) { p.hidden = true; p.onclick = null; }
}
// 点「后台运行」：只收起窗口，不中断请求、不通知后端取消
function minimizeOverlay() {
  const ov = $('#overlay');
  if (!ov || ov.hidden || !_activeFetchCtrl || _bgTask) return;
  _bgTask = { label: bgLabel(), failed: false, settled: false };
  ov.hidden = true;
  stopProgressPolling();
  stopMemeCarousel();
  const pill = $('#bgPill');
  pill.hidden = false;
  pill.className = 'bg-pill running';
  pill.textContent = '⏳ 后台运行中：' + _bgTask.label + '（点此恢复进度窗口）';
  pill.onclick = restoreBgOverlay;
}
// 点角标：把进度窗口弹回前台（任务仍在跑，完成后照常收尾）
function restoreBgOverlay() {
  if (!_bgTask) { hideBgPill(); return; }
  $('#bgPill').hidden = true;
  $('#overlay').hidden = false;
  $('#btnCancelTask').hidden = false;
  const bgBtn = $('#btnBgRun');
  if (bgBtn) bgBtn.hidden = false;
  startProgressPolling();
  startMemeCarousel();
}
// 后台任务结束：角标转为完成/失败提示，8 秒后自动消失
function bgFinish() {
  const t = _bgTask;
  _bgTask = null;
  if (!t) return;
  const pill = $('#bgPill');
  pill.hidden = false;
  pill.className = 'bg-pill done' + (t.failed ? ' fail' : '');
  pill.textContent = t.failed
    ? '⚠ 后台任务已结束：' + t.label + '（详情见页面提示）'
    : '✅ ' + t.label + ' 已完成，结果已显示在页面';
  pill.onclick = () => { pill.hidden = true; };
  setTimeout(() => {
    if (!_bgTask && pill.className.indexOf('done') >= 0) pill.hidden = true;
  }, 8000);
}

// ========= 预估耗时：按当前范围（全A/沪/深/北）股票数 × 单股成本估算 =========
const RANGE_STOCKS = { all: 5550, SH: 1700, SZ: 2850, BJ: 270 };
const MODULE_PER_STOCK = { strategy: 0.02, cond: 0.015, pattern: 0.045, ant: 0.02, alert: 0.008 };
function estText(kind, exchange) {
  const n = RANGE_STOCKS[exchange || 'all'] || 5550;
  const secs = Math.max(3, Math.round(n * (MODULE_PER_STOCK[kind] || 0.02)));
  return secs >= 90 ? `预计约 ${Math.round(secs / 60)} 分钟` : `预计约 ${secs} 秒`;
}

// ========= 范围 =========
function bindRange() {
  $$('#rangePicker button').forEach(b => b.addEventListener('click', () => {
    $$('#rangePicker button').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    state.range = b.dataset.range;
    $('#rangeBadge').textContent = b.textContent;
  }));
}
const rangeVal = () => state.range === 'all' ? null : state.range;

// ========= Tab（含界面回退历史栈） =========
const _panelHistory = [];   // 界面访问历史，供「↩ 退回」按钮使用

function switchTab(name, noPush) {
  const cur = $$('.tab').find(b => b.classList.contains('active'));
  if (cur && cur.dataset.tab && cur.dataset.tab !== name && !noPush) {
    _panelHistory.push(cur.dataset.tab);   // 记录来源界面
  }
  $$('.tab').forEach(b => b.classList.remove('active'));
  $$('.panel').forEach(p => p.classList.remove('active'));
  const btn = $('.tab[data-tab="' + name + '"]');
  if (btn) btn.classList.add('active');
  const panel = $('#tab-' + name);
  if (panel) panel.classList.add('active');
  if (name === 'bili' && typeof initBili === 'function') initBili();
}

function bindTabs() {
  $$('.tab').forEach(btn => btn.addEventListener('click', () => switchTab(btn.dataset.tab)));
}

// ========= 策略 =========
const STRATEGY_PARAMS = {
  turtle:  { '窗口(日)': 20, '回看(日)': 60 },
  ma_vol:  { '短期均线': 5, '长期均线': 20, '量比阈值': 1.5 },
  flag:    { '整理(日)': 15, '突破幅度(%)': 2, '振幅上限(%)': 12 },
  shake:   { '洗盘回撤上限(%)': 7 },
  limit_d: { '近期窗口(日)': 20, '最低涨幅(%)': 8 },
  rps:     { 'RPS阈值': 80, 'Top N': 100 },
};
async function initStrategy() {
  const list = await API.strategiesList();
  $('#strategyKey').innerHTML = '<option value="all">全部一起跑</option>' +
    list.map(s => `<option value="${s.key}">${s.label}</option>`).join('');
  $('#strategyKey').addEventListener('change', buildStrategyParams);
  buildStrategyParams();
}
function buildStrategyParams() {
  const key = $('#strategyKey').value;
  const params = key === 'all'
    ? Object.assign({}, ...Object.values(STRATEGY_PARAMS))
    : (STRATEGY_PARAMS[key] || {});
  $('#strategyParamsGrid').innerHTML = Object.entries(params).map(([k, v]) =>
    `<label>${k}<input type="number" data-param="${k}" value="${v}" step="0.5" /></label>`
  ).join('') || '<div style="color:var(--ink-faint);font-size:12px;">该策略无额外参数</div>';
}
async function runStrategies() {
  hideMeme('strategyMeme');
  const key = $('#strategyKey').value;
  const keys = key === 'all'
    ? Array.from($('#strategyKey').options).filter(o => o.value !== 'all').map(o => o.value)
    : [key];
  showOverlay('loading', `正在策略扫描，稍等片刻…（${estText('strategy', rangeVal())}）`, { cancellable: true });
  try {
    const data = await API.strategiesRun({ keys, exchange: rangeVal(), sectors: null });
    hideOverlay();
    // 后端返回 {rows: [...扁平...], by_strategy: {key: {label, rows}}}
    const flat = Array.isArray(data.rows) ? data.rows : [];
    if (data.by_strategy) {
      for (const v of Object.values(data.by_strategy)) {
        if (v.error) console.warn(v.error);
      }
    }
    if (flat.length === 0) {
      showMeme('strategyMeme', 'empty', '没有命中任何股票', '放宽参数或换个范围再试试');
      renderTable('strategyTable', []);
      return;
    }
    showMeme('strategyMeme', 'success', `命中 ${flat.length} 只 🎉`, '点击代码＝快速查看 · 点击名称＝详细查询');
    renderTable('strategyTable', flat, { max: 300 });
  } catch (e) {
    hideOverlay();
    if (_userCancelled) { showMeme('strategyMeme', 'farewell', '已取消这次扫描', '随时可以重新开始'); return; }
    showMeme('strategyMeme', 'error', '请求失败', String(e));
  }
}

// ========= 条件 =========
async function initConditions() {
  const list = await API.conditionsList();
  $('#condGrid').innerHTML = list.map(c => `
    <label class="cond-item">
      <input type="checkbox" value="${c.key}" />
      <div><div class="ci-name">${c.label}</div><div class="ci-desc">${c.desc || ''}</div></div>
    </label>`).join('');
  $$('#condGrid .cond-item').forEach(item => {
    const cb = item.querySelector('input');
    cb.addEventListener('change', () => item.classList.toggle('active', cb.checked));
    item.addEventListener('click', e => {
      if (e.target.tagName === 'INPUT') return;   // 点小框：原生行为，翻一次
      e.preventDefault();                          // 关键：阻断 label 默认转发
      cb.checked = !cb.checked; item.classList.toggle('active', cb.checked);   // 否则会与 label 转发二次抵消
    });
  });
  // 默认勾选前 4 个条件
  $$('#condGrid .cond-item').slice(0, 4).forEach(item => {
    const cb = item.querySelector('input');
    cb.checked = true; item.classList.add('active');
  });
}
async function runConditions() {
  hideMeme('condMeme');
  const keys = $$('#condGrid input:checked').map(c => c.value);
  if (keys.length === 0) { showMeme('condMeme', 'ask', '请先勾选条件', '至少选一个条件哦'); return; }
  showOverlay('loading', `正在按条件过滤…（${estText('cond', rangeVal())}）`, { cancellable: true });
  try {
    const cfg = {
      CHANNEL_DAYS: num('#pChannelDays', 60), CHANNEL_R2: num('#pChannelR2', 0.6),
      PULLBACK_DAYS: num('#pPullbackDays', 20), SUPPORT_MA: num('#pSupportMa', 20),
      PULLBACK_TOL: num('#pPullbackTol', 2), SMALL_YANG_DAYS: num('#pSmallYangDays', 5),
      SMALL_YANG_MAX: num('#pSmallYangMax', 3), W_BOTTOM_DAYS: num('#pWBottomDays', 60),
      W_BOTTOM_TOL: num('#pWBottomTol', 3), DIVERGENCE_DAYS: num('#pDivergenceDays', 60),
      RSI_DAYS: num('#pRsiDays', 14), VAL_YEARS: num('#pValYears', 10),
      PE_PERCENTILE: num('#pPePercentile', 10), PB_PERCENTILE: num('#pPbPercentile', 10),
      MIN_VAL_ROWS: num('#pMinValRows', 120), DIV_YIELD_MIN: num('#pDivYieldMin', 3),
      NEW_STOCK_DAYS: num('#pNewStockDays', 365), EXCLUDE_ST: $('#pExcludeSt').checked,
    };
    const data = await API.conditionsRun({ keys, exchange: rangeVal(), cfg });
    hideOverlay();
    const rows = data.rows || [];
    if (rows.length === 0) {
      showMeme('condMeme', 'empty', '没有交集', '条件太严了，放宽一点试试');
      renderTable('condTable', []);
      return;
    }
    showMeme('condMeme', 'success', `${keys.length} 条件命中 ${rows.length} 只 ✨`, '按 PE-TTM 升序展示');
    renderTable('condTable', rows, { max: 300 });
  } catch (e) {
    hideOverlay();
    if (_userCancelled) { showMeme('condMeme', 'farewell', '已取消这次筛选', '随时可以重新开始'); return; }
    showMeme('condMeme', 'error', '请求失败', String(e));
  }
}

// ========= 形态 =========
async function runPattern() {
  hideMeme('patternMeme');
  showOverlay('loading', `正在形态打分（较慢，稍候）…（${estText('pattern', rangeVal())}）`, { cancellable: true });
  try {
    const data = await API.patternRun({
      min_score: Number($('#patternMinScore').value),
      only_bottom: $('#patternOnlyBottom').checked && !$('#patternOnlyTop').checked,
      only_top: $('#patternOnlyTop').checked,
      exchange: rangeVal(),
    });
    hideOverlay();
    const results = data.results || [];
    if (results.length === 0) {
      showMeme('patternMeme', 'empty', '没有达到分数线的形态', '把分数降一点再试');
      renderTable('patternTable', []);
      return;
    }
    showMeme('patternMeme', 'success',
      `${results.length} 只形态命中 🪄`,
      Object.entries(data.skip_stats || {}).map(([k, v]) => `${k}:${v}`).join(' · '));
    renderTable('patternTable', results.map(r => ({
      '代码': r.code, '名称': r.name, '匹配度': r.raw_score, '综合分': r.score,
      '形态分': r.sim_score,
      '趋势分': r.struct_score, '评级': r.grade, '类型': r.type_label,
      '层': r.tier_short, '最像样本': r.nearest_sample, '收盘': r.close,
    })), { max: 300 });
  } catch (e) {
    hideOverlay();
    if (_userCancelled) { showMeme('patternMeme', 'farewell', '已取消这次打分', '随时可以重新开始'); return; }
    showMeme('patternMeme', 'error', '请求失败', String(e));
  }
}

// ========= 蚂蚁 =========
async function runAnt() {
  hideMeme('antMeme');
  showOverlay('loading', `蚂蚁四层扫描中…（${estText('ant', rangeVal())}）`, { cancellable: true });
  try {
    const data = await API.antRun({
      exchange: rangeVal(),
      cfg: {
        min_score: Number($('#antMinScore').value),
        only_trigger: $('#antOnlyTrigger').checked,
        scan: { lookback: num('#aLookback', 300) },
        hard_filter: { min_list_days: num('#aMinListDays', 250),
                       liquidity_quantile: num('#aLiquidityQ', 0.30) },
        pattern: { amplitude_range: [num('#aAmpMin', 0.20), num('#aAmpMax', 0.45)],
                   p_threshold: num('#aPThreshold', 0.05),
                   close_pos_range: [num('#aClosePosMin', 0.25), num('#aClosePosMax', 0.70)],
                   second_half_vol_ratio_max: num('#aSecondHalfVol', 0.85),
                   recent10_vol_ratio_max: num('#aRecent10Vol', 0.75),
                   up_down_vol_ratio_min: num('#aUpDownVol', 1.15),
                   min_up_days: 3, min_down_days: 3 },
        ranking: { min_bottom_rise_pct: num('#aBottomRise', 0.02),
                   support_touch_min: num('#aSupportTouch', 2),
                   ma_convergence_pct: num('#aMaConverge', 3),
                   ma_convergence_ratio: num('#aMaConvRatio', 0.6),
                   excess_return_min: num('#aExcessReturn', 0),
                   downside_resistance_ratio: num('#aDownside', 0.85),
                   prior_drop_min: num('#aPriorDrop', 0.10) },
        chip_filter: { enable: $('#aChipEnable').checked,
                       only_keep_up: $('#aChipKeepUp').checked },
        breakout: { volume_ratio: num('#aVolumeRatio', 1.5) },
      },
      only_pass: !$('#antOnlyTrigger').checked,
      limit: 300,
    });
    hideOverlay();
    const rows = data.rows || [];
    if (rows.length === 0) {
      showMeme('antMeme', 'empty', '这轮没扫到蚂蚁', '放宽分数阈值再试');
      renderTable('antTable', []);
      return;
    }
    const triggered = rows.filter(r => (r['启动状态'] || '').includes('启动')).length;
    showMeme('antMeme', 'success',
      `扫到 ${rows.length} 只，其中 ${triggered} 只接近启动 🐜`,
      '总分越高越值得关注');
    renderTable('antTable', rows.map(r => ({
      '代码': r['代码'], '名称': r['名称'], '板块': r['板块'],
      '总分': r['总分（0-7）'], '有效项': r['有效项数'],
      '启动状态': r['启动状态'], '振幅60日': r['60日振幅'],
      '质量': r.data_quality,
    })), { max: 300 });
  } catch (e) {
    hideOverlay();
    if (_userCancelled) { showMeme('antMeme', 'farewell', '已取消这次扫描', '随时可以重新开始'); return; }
    showMeme('antMeme', 'error', '请求失败', String(e));
  }
}

// ========= 持有个1000年试试呢？（长周期版蚂蚁） =========
async function refreshAnt1000Cache() {
  try {
    const c = await API.ant1000Cache();
    const el = $('#ant1000Cache');
    if (!el) return;
    const miss = (c.missing || 0) + (c.failed || 0);
    el.textContent = `历史数据缓存：${c.ok || 0}/${c.total || 0} 只` +
      (miss ? `（待补齐 ${miss} 只）` : '（已就绪）');
  } catch (e) { /* 忽略 */ }
}

async function runAnt1000() {
  hideMeme('ant1000Meme');
  const years = Number(($('#ant1000Years') || {}).value) || 20;
  // 首次运行预估：把缺失数量提前告诉用户（避免对长时间等待感到意外）
  let tip = `${years} 年窗口`;
  try {
    const c = await API.ant1000Cache();
    const miss = (c.missing || 0) + (c.failed || 0);
    if (miss > 50) {
      const est = Math.max(1, Math.round(miss * 2.45 / 6 / 60));   // 实测 ~2.45s/只 · 6 并发
      tip = `首次运行需下载 ${miss} 只历史数据（约 ${est} 分钟，仅一次）`;
    }
  } catch (e) { /* 忽略 */ }
  showOverlay('loading', `长周期扫描中…（${tip}）`, { cancellable: true });
  try {
    const data = await API.ant1000Run({
      years,
      exchange: rangeVal(),
      cfg: {
        hard_filter: { min_list_years: num('#a1MinYears', 5),
                       liquidity_quantile: num('#a1LiqQ', 0.30) },
        cycle: { zigzag_thr: num('#a1Zigzag', 0.30),
                 swing_dd_min: num('#a1SwingDd', 0.35),
                 swing_up_min: num('#a1SwingUp', 0.70) },
        score: { max_dd_min: num('#a1MaxDd', 40) / 100,
                 max_rebound_min: num('#a1MaxRebound', 60) / 100,
                 trend_annual_max: num('#a1TrendMax', 10),
                 downside_resistance: num('#a1Downside', 0.85),
                 low_pos_max: num('#a1LowPos', 50) / 100,
                 high_drop_min: num('#a1HighDrop', 25) / 100 },
      },
      only_pass: true,
    });
    hideOverlay();
    refreshAnt1000Cache();
    const allRows = data.rows || [];
    const minScore = num('#a1MinScore', 4);
    const rows = allRows.filter(r => (Number(r['总分（0-7）']) || 0) >= minScore);
    if (rows.length === 0) {
      showMeme('ant1000Meme', 'empty', '这轮没筛到长周期股',
        allRows.length ? `总分≥${minScore} 无结果（共 ${allRows.length} 只通过硬过滤，可调低阈值）` : '换个窗口或放宽参数再试');
      renderTable('ant1000Table', []);
      return;
    }
    const top = rows.slice(0, 3).map(r => r['名称']).join('、');
    showMeme('ant1000Meme', 'success',
      `筛到 ${rows.length} 只长周期标的 🐢（总分≥${minScore}）`,
      `前三：${top} · 总分越高「周期特征」越强 · 点击代码看长周期图`);
    renderTable('ant1000Table', rows.map(r => ({
      '代码': r['代码'], '名称': r['名称'], '板块': r['板块'],
      '总分': r['总分（0-7）'], '有效项': r['有效项数'],
      '循环数': r['周期循环数'], '上市年': r['上市年数'],
      '最大回撤%': r['历史最大回撤%'], '最大反弹%': r['最大反弹%'],
      '年化波动%': r['年化波动率%'], '趋势年化%': r['趋势年化%'],
      '当前分位%': r['当前分位%'], '距高回撤%': r['距高点回撤%'],
      '长期超额%': r['长期超额%'], '熊市韧性': r['熊市韧性'],
    })), { max: 300 });
    bindAnt1000Clicks();
    loadAnt1000Chart(rows[0]['代码']);   // 自动展示第一名
  } catch (e) {
    hideOverlay();
    if (_userCancelled) { showMeme('ant1000Meme', 'farewell', '已取消这次扫描', '随时可以重新开始'); return; }
    showMeme('ant1000Meme', 'error', '请求失败', String(e));
  }
}

// 结果表第一列「代码」改为：点击即绘制该股长周期图（覆盖默认的跳 K 线行为）
function bindAnt1000Clicks() {
  const t = $('#ant1000Table');
  if (!t) return;
  t.querySelectorAll('tbody .code-link').forEach(a => {
    const clone = a.cloneNode(true);   // 克隆以清空旧的跳转监听
    a.replaceWith(clone);
    clone.title = '点击查看该股长周期图';
    clone.addEventListener('click', e => {
      e.preventDefault();
      loadAnt1000Chart(clone.dataset.code);
    });
  });
}

async function loadAnt1000Chart(code) {
  const years = Number(($('#ant1000Years') || {}).value) || 20;
  showOverlay('loading', `加载 ${code} 长周期数据…`);
  try {
    const d = await API.ant1000Kline(code, years);
    hideOverlay();
    if (d.error) {
      showMeme('ant1000Meme', 'error', `无法加载 ${code} 长周期图`, d.error);
      return;
    }
    drawHistChart(d);
    const cv = $('#ant1000Chart');
    if (cv) { cv.hidden = false; }
  } catch (e) {
    hideOverlay();
    showMeme('ant1000Meme', 'error', '长周期图加载失败', String(e));
  }
}

// 长周期月线图：对数价格轴 + ZigZag 顶底标注 + 循环配对连线 + 上证指数对比线
function drawHistChart(d) {
  const canvas = $('#ant1000Chart');
  if (!canvas) return;
  const { ctx, W, H } = hiDPI(canvas);
  const PAD_L = 58, PAD_R = 16, PAD_T = 30, PAD_B = 34;
  ctx.clearRect(0, 0, W, H);
  const months = d.months || [];
  if (!months.length) return;
  const N = months.length;
  const closes = months.map(m => m.close);
  const idxs = d.index || [];
  // —— 数据范围（对数轴）——
  let mn = Math.min(...closes), mx = Math.max(...closes);
  idxs.forEach(p => { if (p.v > 0) { mn = Math.min(mn, p.v); mx = Math.max(mx, p.v); } });
  if (!(mn > 0)) mn = Math.max(mx * 0.001, 0.001);
  const logMin = Math.log(mn), logMax = Math.log(mx);
  const xStep = (W - PAD_L - PAD_R) / Math.max(N - 1, 1);
  const xAt = i => PAD_L + i * xStep;
  const yMap = v => H - PAD_B - ((Math.log(Math.max(v, 1e-6)) - logMin) / (logMax - logMin || 1)) * (H - PAD_T - PAD_B);
  const dateIdx = {};
  months.forEach((m, i) => { dateIdx[m.date] = i; });
  // —— 标题 ——
  ctx.fillStyle = '#2a2a2a';
  ctx.font = '600 13px sans-serif';
  const cyc = d.cycles || [];
  ctx.fillText(`${d.code} ${d.name} · 月线（对数轴） · 窗口 ${d.years} 年 · 完整循环 ${cyc.length} 次`, PAD_L, 18);
  // —— 网格（对数均匀 5 条）——
  ctx.strokeStyle = '#eeebe2'; ctx.lineWidth = 1;
  ctx.font = '10px sans-serif';
  for (let i = 0; i <= 4; i++) {
    const lv = logMin + (logMax - logMin) * i / 4;
    const y = yMap(Math.exp(lv));
    ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - PAD_R, y); ctx.stroke();
    ctx.fillStyle = '#a3a3a3';
    ctx.fillText(Math.exp(lv) >= 100 ? Math.exp(lv).toFixed(0) : Math.exp(lv).toFixed(2), 6, y + 3);
  }
  // —— 时间轴标签（约每 1/8 宽度一个年）——
  ctx.fillStyle = '#a3a3a3';
  const labelStep = Math.max(1, Math.ceil(N / 8));
  months.forEach((m, i) => {
    if (i % labelStep === 0 || i === N - 1) {
      ctx.fillText(m.date.slice(0, 4), xAt(i) - 12, H - PAD_B + 14);
    }
  });
  // —— 指数对比线（归一化到股票起点价，灰色虚线）——
  if (idxs.length >= 2) {
    const baseIdx = idxs[0].v, baseStk = closes[dateIdx[idxs[0].date]] || closes[0];
    ctx.strokeStyle = '#b9b3a4'; ctx.lineWidth = 1.2; ctx.setLineDash([5, 4]);
    ctx.beginPath();
    let started = false;
    idxs.forEach(p => {
      const i = dateIdx[p.date];
      if (i === undefined) return;
      const v = p.v / baseIdx * baseStk;   // 指数平移到股票量纲
      if (!started) { ctx.moveTo(xAt(i), yMap(v)); started = true; }
      else ctx.lineTo(xAt(i), yMap(v));
    });
    ctx.stroke(); ctx.setLineDash([]);
  }
  // —— 股票收盘折线 ——
  ctx.strokeStyle = '#b86b3f'; ctx.lineWidth = 1.6;
  ctx.beginPath();
  months.forEach((m, i) => {
    if (i === 0) ctx.moveTo(xAt(i), yMap(m.close));
    else ctx.lineTo(xAt(i), yMap(m.close));
  });
  ctx.stroke();
  // —— 循环区间底色（峰值顶 → 终顶）——
  cyc.forEach(c => {
    const i0 = dateIdx[c.peak0], i1 = dateIdx[c.peak1];
    if (i0 === undefined || i1 === undefined) return;
    ctx.fillStyle = 'rgba(184,107,63,.07)';
    ctx.fillRect(xAt(i0), PAD_T, Math.max(xAt(i1) - xAt(i0), 1), H - PAD_T - PAD_B);
  });
  // —— ZigZag 顶底标注 ——
  (d.pivots || []).forEach(p => {
    const i = dateIdx[p.date];
    if (i === undefined) return;
    const x = xAt(i), y = yMap(p.price);
    ctx.beginPath();
    if (p.kind === 'H') {           // 顶：红色倒三角
      ctx.fillStyle = '#c0392b';
      ctx.moveTo(x, y - 5); ctx.lineTo(x - 4.5, y - 13); ctx.lineTo(x + 4.5, y - 13);
    } else {                         // 底：绿色正三角
      ctx.fillStyle = '#2e8b57';
      ctx.moveTo(x, y + 5); ctx.lineTo(x - 4.5, y + 13); ctx.lineTo(x + 4.5, y + 13);
    }
    ctx.closePath(); ctx.fill();
  });
  // —— 循环配对标注（底点上标 dd/up）——
  ctx.font = '10px sans-serif';
  cyc.forEach(c => {
    const i = dateIdx[c.trough];
    if (i === undefined) return;
    const p = (d.pivots || []).find(pp => pp.date === c.trough);
    if (!p) return;
    const x = xAt(i), y = yMap(p.price);
    const yBase = Math.min(y + 30, H - PAD_B + 24);
    ctx.fillStyle = '#2e8b57';
    ctx.fillText(`↓${c.dd}%`, x - 12, yBase);
    ctx.fillStyle = '#c0392b';
    ctx.fillText(`↑${c.up}%`, x - 12, yBase + 12);
  });
  // —— 图例 ——
  ctx.font = '10.5px sans-serif';
  ctx.fillStyle = '#b86b3f';
  ctx.fillText('—— 月收盘价', W - 300, 18);
  ctx.fillStyle = '#b9b3a4';
  ctx.fillText('---- 上证指数（归一化）', W - 225, 18);
  ctx.fillStyle = '#c0392b';
  ctx.fillText('▲顶', W - 90, 18);
  ctx.fillStyle = '#2e8b57';
  ctx.fillText('▼底', W - 60, 18);
}

// ========= 相似股 =========
async function runSimilar() {
  hideMeme('similarMeme');
  const code = $('#similarCode').value.trim();
  if (!code) { showMeme('similarMeme', 'ask', '请输入目标代码', '例如 600519'); return; }
  const volMode = $('#similarVolMode').value || 'ignore';
  const volThreshold = Number($('#similarVolThreshold').value) || 50;
  const volNote = volMode === 'similar'
    ? `（已约束波动幅度相似，阈值 ±${volThreshold}%）`
    : '（无视波动幅度，只看相关性）';
  showOverlay('loading', '正在全市场匹配相似走势…', { cancellable: true });
  try {
    const data = await API.similarRun({
      target_code: code,
      ref_days: Number($('#similarRefDays').value),
      max_lag: num('#similarMaxLag', 5),
      vol_mode: ($('#similarVolMode') || {}).value || 'ignore',
      vol_threshold: num('#similarVolThreshold', 50),
      top_n: Number($('#similarTopN').value),
      vol_mode: volMode,
      vol_threshold: volThreshold,
    });
    hideOverlay();
    if (data.detail) { showMeme('similarMeme', 'error', '参数有误', JSON.stringify(data.detail).slice(0, 120)); return; }
    if (data.error) { showMeme('similarMeme', 'error', '匹配失败', String(data.error)); return; }
    const a = data.a || [], b = data.b || [], c = data.c || [];
    // 图上每类只画最符合的 2 只（后端已按相关度排序）；表格仍列全部
    const pick2 = (arr, cls) => arr.slice(0, 2).map((x, j) => ({ ...x, cls, sub: j }));
    drawSimilarChart(data.target, [
      ...pick2(a, 'a'), ...pick2(b, 'b'), ...pick2(c, 'c'),
    ]);
    const mapCols = r => ({
      '代码': r.code, '名称': r.name, '相关度': r.corr,
      '领先/滞后(日)': (r.lag === null || r.lag === undefined || r.lag === 'None') ? '—' : r.lag,
      '备注': r.note || '—',
    });
    renderTable('similarATable', a.map(mapCols), { max: 50 });
    renderTable('similarBTable', b.map(mapCols), { max: 50 });
    renderTable('similarCTable', c.map(mapCols), { max: 50 });
    showMeme('similarMeme', 'success',
      `a 类 ${a.length} · b 类 ${b.length} · c 类 ${c.length}`,
      '图上鲜红线是目标股，每类只画最像的 2 只（A蓝/B橙/C绿，虚线为第2只）；' + volNote);
  } catch (e) {
    hideOverlay();
    if (_userCancelled) { showMeme('similarMeme', 'farewell', '已取消这次匹配', '随时可以重新开始'); return; }
    showMeme('similarMeme', 'error', '请求失败', String(e));
  }
}

function drawSimilarChart(target, candidates) {
  const canvas = $('#similarChart');
  const { ctx, W, H } = hiDPI(canvas);   // 2x 超采样高清
  const PAD = 46;
  ctx.clearRect(0, 0, W, H);
  const series = [];
  if (candidates.length > 0 && candidates[0].chart) {
    const t0 = candidates[0].chart;
    const tVals = Array.isArray(t0.target) ? t0.target : (Array.isArray(t0.cand) ? t0.cand : null);
    if (tVals) series.push({ name: `目标 ${target.code} ${target.name || ''}`, vals: tVals, color: '#ff2d2d', width: 3, dash: [] });   // 目标股：鲜亮红
    // a 蓝 / b 橙 / c 绿 —— 高区分度且柔和不刺眼；类内第 1 只实线、第 2 只虚线
    const palette = { a: '#1f6fd0', b: '#e8890c', c: '#18a47c' };
    candidates.forEach(cd => {
      const vals = Array.isArray(cd.chart.cand) ? cd.chart.cand : (Array.isArray(cd.chart.candidate) ? cd.chart.candidate : null);
      if (vals) series.push({ name: `[${cd.cls.toUpperCase()}${cd.sub ? 2 : 1}] ${cd.code} ${cd.name || ''}`, vals,
                              color: palette[cd.cls] || '#999', width: 1.6, dash: cd.sub ? [6, 3] : [] });
    });
  }
  if (series.length === 0) {
    ctx.fillStyle = '#a3a3a3'; ctx.font = '13px sans-serif';
    ctx.fillText('暂无对比数据：请先运行匹配', PAD, PAD);
    return;
  }
  let mn = Infinity, mx = -Infinity;
  series.forEach(s => s.vals.forEach(v => { if (v != null) { mn = Math.min(mn, v); mx = Math.max(mx, v); } }));
  if (!isFinite(mn)) { mn = 0; mx = 1; }
  const pad = (mx - mn) * 0.08 || 1;
  mn -= pad; mx += pad;
  const yMap = v => H - PAD - ((v - mn) / (mx - mn)) * (H - PAD * 2);
  ctx.strokeStyle = '#eeebe2'; ctx.lineWidth = 1;
  for (let i = 0; i < 5; i++) {
    const y = PAD + (H - PAD * 2) * (i / 4);
    ctx.beginPath(); ctx.moveTo(PAD, y); ctx.lineTo(W - PAD, y); ctx.stroke();
    ctx.fillStyle = '#a3a3a3'; ctx.font = '10px sans-serif';
    ctx.fillText((mx - (mx - mn) * i / 4).toFixed(1), 6, y + 3);
  }
  series.forEach(s => {
    ctx.strokeStyle = s.color; ctx.lineWidth = s.width;
    ctx.setLineDash(s.dash || []);
    ctx.beginPath();
    // 关键修复：每条序列按自身长度独立铺满整个 x 轴（形态对比语义）。
    // a/b/c 三类窗口长度不同（a 类可变、b/c 各有天数），若共用"最长序列"的
    // x 轴刻度，短窗口的目标线与 a 类线只能画到左侧一小段（后半段空白）。
    const step = (W - PAD * 2) / Math.max(s.vals.length - 1, 1);
    let started = false;
    s.vals.forEach((v, i) => {
      if (v == null) return;
      const x = PAD + i * step, y = yMap(v);
      started ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      started = true;
    });
    ctx.stroke();
  });
  ctx.setLineDash([]);
  ctx.font = '11px sans-serif';
  let lx = PAD;
  series.slice(0, 7).forEach(s => {
    ctx.fillStyle = s.color;
    const label = '— ' + s.name;
    ctx.fillText(label, lx, 16);
    lx += ctx.measureText(label).width + 14;
    if (lx > W - 120) return;
  });
}

// ========= 前瞻预警 =========
let _alertLast = null;  // 最近一次预警结果（供网页报告导出复用）

async function runAlert() {
  hideMeme('alertMeme');
  const sections = [];
  [['alertSecCalendar', 'calendar'], ['alertSecStealth', 'stealth'], ['alertSecCold', 'cold'],
   ['alertSecNational', 'national'], ['alertSecForeign', 'foreign'],
   ['alertSecCrowding', 'crowding'], ['alertSecChains', 'chains']]
    .forEach(([id, key]) => { const el = $('#' + id); if (el && el.checked) sections.push(key); });
  if (sections.length === 0) {
    showMeme('alertMeme', 'ask', '请至少勾选一个模块', '核心信号会基于勾选模块自动合成');
    return;
  }
  const cfg = {
    calendar: { horizon_days: num('#aCalHorizon', 35) },
    stealth: { gain_min: num('#aGainMin', 6), inflow_min: num('#aInflowMin', 3),
               turnover_pct_max: num('#aTurnoverPctMax', 60),
               hot_gain_min: num('#aHotGain', 20),
               hot_turnover_pct_min: num('#aHotTurnoverPct', 80) },
    cold: { window: num('#aColdWindow', 60), top_n: num('#aColdTopN', 3),
            w_amount: num('#aWAmt', 0.35), w_turnover: num('#aWTur', 0.35),
            w_gain: num('#aWGain', 0.30) },
    national: { days: num('#aNatlDays', 5), groups: (() => {
      // 四列表各自可选：ETF / 汇金 / 社保 / 证金（全不勾 = 该节不拉取名单）
      const gs = [];
      [['natGrpEtf', 'etf'], ['natGrpHjj', 'huijin'], ['natGrpSsf', 'ssf'], ['natGrpZjj', 'zhengjin']]
        .forEach(([id, k]) => { const el = $('#' + id); if (el && el.checked) gs.push(k); });
      return gs;
    })() },
    crowding: { turnover_pct: num('#aCrowdTurnover', 0.90),
                amount_pct: num('#aCrowdAmount', 0.90),
                gain20_min: num('#aCrowdGain', 15),
                trigger_n: num('#aCrowdTrigger', 2) },
    foreign: { top_n: num('#aForeignTopN', 10) },
  };
  showOverlay('loading', `前瞻预警扫描中（联网采集）…（${estText('alert', rangeVal())}）`, { cancellable: true });
  try {
    const data = await API.alertRun({ cfg, sections });
    hideOverlay();
    if (data.cancelled) { showMeme('alertMeme', 'farewell', '已取消这次预警', '随时可以重新开始'); return; }
    if (data.error) { showMeme('alertMeme', 'error', '预警失败', String(data.error).slice(0, 140)); return; }
    _alertLast = data;
    renderAlert(data);
    const sig = (data.signals || []).length;
    const st = (data.stealth || []).length + (data.selling || []).length;
    showMeme('alertMeme', sig > 0 || st > 0 ? 'success' : 'empty',
      `核心信号 ${sig} 条 · 异动 ${st} 条 ⏰`,
      `耗时 ${data.elapsed || '—'}s；点击代码可站内查看 K 线`);
  } catch (e) {
    hideOverlay();
    if (_userCancelled) { showMeme('alertMeme', 'farewell', '已取消这次预警', '随时可以重新开始'); return; }
    showMeme('alertMeme', 'error', '请求失败', String(e));
  }
}

function renderAlert(data) {
  const cal = data.calendar || {};
  $('#alertMonthThemes').textContent =
    (cal['本月主线'] && cal['本月主线'].length) ? '本月主线：' + cal['本月主线'].join(' / ') : '';
  renderTable('alertSignalTable', data.signals || [], { max: 100 });
  renderTable('alertCalendarTable', (cal['日历预警'] || []).map(r => ({
    '事件': r['事件'], '日期': r['日期'], '距今(天)': r['距今(天)'], '备注': r['备注'] })), { max: 50 });
  renderTable('alertWindowTable', (cal['埋伏窗口'] || []).map(r => ({
    '主题': r['主题'], '关联事件': r['关联事件'], '事件日期': r['事件日期'],
    '距事件(天)': r['距事件(天)'], '规律窗口': r['规律窗口'] })), { max: 50 });
  renderTable('alertStealthTable', [
    ...(data.stealth || []).map(r => ({ '类型': '埋伏嫌疑', '板块': r['板块'],
      '5日涨幅%': r['5日涨幅%'], '5日主力净流入(亿)': r['5日主力净流入(亿)'],
      '换手分位%': r['换手分位%'] == null ? '—' : r['换手分位%'], '判定': r['判定'] })),
    ...(data.selling || []).map(r => ({ '类型': '出货嫌疑', '板块': r['板块'],
      '5日涨幅%': r['5日涨幅%'], '5日主力净流入(亿)': r['5日主力净流入(亿)'],
      '换手分位%': r['换手分位%'] == null ? '—' : r['换手分位%'], '判定': r['判定'] })),
  ], { max: 100 });
  renderTable('alertColdTable', data.cold || [], { max: 50 });
  // 国家队四列表：新结构 {date, groups:{etf/huijin/ssf/zhengjin:{title, rows}}}
  const nat = (data.national && data.national.groups) ? data.national : { date: '', groups: {} };
  $('#natReportDate').textContent = nat.date ? `📋 ${nat.date} 报告期 · 前十大股东名单口径 · 持股≥1000万股` : '';
  const natCols = r => ({ '简称': r['简称'], '代码': r['代码'], '股东名称': r['股东名称'],
    '持股(万股)': r['持股(万股)'], '占总股本%': r['占总股本%'], '市值(亿)': r['市值(亿)'],
    '变动': r['变动'], '排名': r['排名'] });
  const natColsEtf = r => ({ '简称': r['简称'], '代码': r['代码'], '命中ETF只数': r['命中ETF只数'],
    '合计持仓市值(亿)': r['合计持仓市值(亿)'], '持有ETF明细': r['持有ETF明细'] });
  let natCount = 0;
  [['alertNatEtfTable', 'etf', 'natCntEtf'], ['alertNatHjjTable', 'huijin', 'natCntHjj'],
   ['alertNatSsfTable', 'ssf', 'natCntSsf'], ['alertNatZjjTable', 'zhengjin', 'natCntZjj']]
    .forEach(([tid, key, cid]) => {
      const rows = (nat.groups[key] || {}).rows || [];
      natCount += rows.length;
      renderTable(tid, rows.map(key === 'etf' ? natColsEtf : natCols), { max: 50 });
      const cnt = $('#' + cid);
      if (cnt) cnt.textContent = rows.length + ' 条';
    });
  renderTable('alertForeignTable', data.foreign || [], { max: 50 });
  renderTable('alertCrowdingTable', data.crowding || [], { max: 100 });
  renderTable('alertChainTable', data.chains || [], { max: 100 });
  $('#macroNarrative').textContent = data.macro_narrative || '';
  // 宏观雷达：横向卡片阵列（每主题/信号一卡，一行并排多卡）
  $('#macroCards').innerHTML = (data.macro || []).map(m => {
    const cls = m['方向'] === '偏宽松' ? 'up' : (m['方向'] === '偏紧缩' ? 'down' : '');
    return `<div class="macro-card ${cls}">` +
      `<div class="mc-head"><span class="mc-type">${escHtml(m['类型'])}</span>` +
      `<span class="mc-dir ${cls}">${escHtml(m['方向'])}</span></div>` +
      `<div class="mc-title">${escHtml(m['触发'])}</div>` +
      `<div class="mc-detail">${escHtml(m['细节'])}</div>` +
      `<div class="mc-note">${escHtml(m['应对'])}</div></div>`;
  }).join('') || '<div class="hint">雷达无回波——近端快讯未见宏观主题相关报道</div>';
  // 排版优化：本次无数据的模块卡片整体收起，并给有数据的卡片加计数徽章
  const counts = {
    alertSignalTable: (data.signals || []).length,
    alertCalendarTable: (cal['日历预警'] || []).length,
    alertWindowTable: (cal['埋伏窗口'] || []).length,
    alertStealthTable: (data.stealth || []).length + (data.selling || []).length,
    alertColdTable: (data.cold || []).length,
    alertNatEtfTable: ((data.national || {}).groups || {}).etf ? (data.national.groups.etf.rows || []).length : 0,
    alertNatHjjTable: ((data.national || {}).groups || {}).huijin ? (data.national.groups.huijin.rows || []).length : 0,
    alertNatSsfTable: ((data.national || {}).groups || {}).ssf ? (data.national.groups.ssf.rows || []).length : 0,
    alertNatZjjTable: ((data.national || {}).groups || {}).zhengjin ? (data.national.groups.zhengjin.rows || []).length : 0,
    alertForeignTable: (data.foreign || []).length,
    alertCrowdingTable: (data.crowding || []).length,
    alertChainTable: (data.chains || []).length,
  };
  const hiddenNames = [];
  $$('#tab-alert .sim-block').forEach(b => {
    if (b.id === 'natOuterBlock') return;   // 国家队外块单独按四子表合计处理（见下方 natOuter）
    if (b.id === 'macroOuterBlock') return; // 宏观雷达单独按 macro 行数处理（见下方 macroOuter）
    const t = b.querySelector('table');
    const n = t ? (counts[t.id] ?? 0) : 0;
    b.hidden = n === 0;
    const h3 = b.querySelector('h3');
    if (!h3) return;
    if (n === 0) { hiddenNames.push(h3.textContent.trim()); return; }
    let badge = h3.querySelector('.sec-badge');
    if (!badge) { badge = document.createElement('span'); badge.className = 'sec-badge'; h3.appendChild(badge); }
    badge.textContent = n + ' 条';
  });
  const macroOuter = $('#macroOuterBlock');
  if (macroOuter) macroOuter.hidden = !(data.macro || []).length;
  const baseNotes = (data.notes || []).length ? '数据说明：' + data.notes.join(' ｜ ') : '';
  const hiddenNote = hiddenNames.length ? `（无数据已收起：${hiddenNames.join(' · ')}）` : '';
  const natOuter = $('#natOuterBlock');
  if (natOuter) natOuter.hidden = natCount === 0;   // 外块由四子表合计决定（上面的循环会误命中第一个子表的计数）
  $('#alertNotes').textContent = [baseNotes, hiddenNote].filter(Boolean).join('  ');
  refreshSecToggleState();
}

// ========= 前瞻预警：点击结果卡片标题 = 选入/踢出该模块（与上方勾选框联动） =========
const SEC_CHECKBOX = { calendar: 'alertSecCalendar', stealth: 'alertSecStealth', cold: 'alertSecCold',
  national: 'alertSecNational', foreign: 'alertSecForeign', crowding: 'alertSecCrowding', chains: 'alertSecChains' };
function refreshSecToggleState() {
  $$('.sec-toggle').forEach(h => {
    const cb = $('#' + (SEC_CHECKBOX[h.dataset.sec] || ''));
    h.classList.toggle('sec-off', !(cb && cb.checked));
  });
}
function bindSecToggles() {
  $$('.sec-toggle').forEach(h => {
    h.addEventListener('click', () => {
      const cb = $('#' + (SEC_CHECKBOX[h.dataset.sec] || ''));
      if (!cb) return;
      cb.checked = !cb.checked;
      refreshSecToggleState();
    });
  });
  refreshSecToggleState();
}

async function exportAlertReport() {
  if (!_alertLast) { showMeme('alertMeme', 'ask', '还没有预警结果', '先点「生成预警」再导出报告'); return; }
  showOverlay('loading', '正在渲染网页报告…');
  try {
    const r = await API.alertReport({ result: _alertLast });
    hideOverlay();
    if (!r.html) { showMeme('alertMeme', 'error', '报告生成失败', '后端未返回内容'); return; }
    const blob = new Blob([r.html], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = '前瞻预警_' + (_alertLast.date || new Date().toISOString().slice(0, 10)) + '.html';
    a.click();
    window.open(url, '_blank');
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    showMeme('alertMeme', 'success', '报告已导出 📄', '已在新标签页打开并开始下载');
  } catch (e) {
    hideOverlay();
    showMeme('alertMeme', 'error', '报告生成失败', String(e));
  }
}

// ========= 快讯轮播条 =========
const escHtml = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// 图表超采样：canvas 像素尺寸放大 scale 倍、CSS 尺寸不变、绘制坐标不变 → 输出清晰度翻倍
function hiDPI(canvas, scale = 2) {
  if (!canvas.dataset.bw) {           // 首次记录 HTML 属性里的逻辑尺寸（之后 width 属性已变大）
    canvas.dataset.bw = canvas.getAttribute('width');
    canvas.dataset.bh = canvas.getAttribute('height');
  }
  const W = +canvas.dataset.bw, H = +canvas.dataset.bh;
  if (canvas.width !== W * scale) {
    canvas.width = W * scale; canvas.height = H * scale;
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  return { ctx, W, H };
}

// 点击图表 → 新窗口打开高清大图（可另存）
function openChartWindow(canvas, title) {
  const img = canvas.toDataURL('image/png');
  const w = window.open('', '_blank');
  if (!w) { showOverlay('error', '新窗口被浏览器拦截，请允许弹窗后重试'); setTimeout(hideOverlay, 2500); return; }
  w.document.write('<!DOCTYPE html><html><head><meta charset="utf-8"><title>' + title + '</title>' +
    '<style>body{margin:0;background:#faf8f3;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;gap:12px}' +
    'img{max-width:97vw;max-height:90vh;box-shadow:0 10px 34px rgba(60,40,20,.18);border-radius:10px;background:#fff;cursor:zoom-out}' +
    'p{font:13px/1.6 sans-serif;color:#8a8578;margin:0}</style></head>' +
    '<body><img src="' + img + '" title="点击另存为 PNG" ' +
    'onclick="const a=document.createElement(\'a\');a.href=this.src;a.download=\'' + title + '.png\';a.click()"/>' +
    '<p>' + title + ' · 高清图 · 点击图片可另存</p></body></html>');
  w.document.close();
}

async function loadTicker(force = false) {
  try {
    const d = await API.ticker(force);
    const items = d.items || [];
    if (!items.length) return;
    const seg = items.map(it => {
      if (it.type === 'news') {
        // 关键词徽章：优先后端 kws 数组（1~2 个实体词），兼容旧 kw/kw_url 单字段
        const kws = (Array.isArray(it.kws) && it.kws.length)
          ? it.kws
          : (it.kw ? [{ kw: it.kw, url: it.kw_url || it.url }] : []);
        const kwHtml = kws.map(k =>
          `<a class="tk-kw" href="${escHtml(k.url)}" target="_blank" rel="noopener" title="${escHtml(it.time || '')} 关键词检索">【${escHtml(k.kw)}】</a>`).join('');
        return `<span class="tk-item">` + kwHtml +
          `<span class="tk-txt"><a href="${escHtml(it.url)}" target="_blank" rel="noopener" title="阅读原文">${escHtml(it.text)}</a></span></span>`;
      }
      if (it.type === 'stock') {
        return `<span class="tk-item"><a class="tk-stock" href="${escHtml(it.url)}" target="_blank" rel="noopener">📈 ${escHtml(it.name)} ${escHtml(it.code)} ↗</a></span>`;
      }
      if (it.type === 'market') {
        const pct = parseFloat(it.pct);
        const cls = pct > 0 ? 'tk-up up' : (pct < 0 ? 'tk-down down' : '');
        return `<span class="tk-item"><span class="tk-mkt ${cls}">${escHtml(it.label)} ${escHtml(it.value)} ${pct > 0 ? '+' : ''}${escHtml(it.pct)}%</span></span>`;
      }
      if (it.type === 'cci') {
        const v = parseFloat(it.value);
        const arrow = v > 0 ? '↗' : (v < 0 ? '↘' : '→');
        // 预警（|CCI|>100）标红最高优先级；否则按多空方向红/绿
        const dirCls = it.warn ? 'tk-warn' : (v > 0 ? 'tk-up up' : (v < 0 ? 'tk-down down' : ''));
        return `<span class="tk-item"><span class="tk-mkt tk-cci ${dirCls}" ` +
          `title="CCI(20)·上证指数（${escHtml(it.date || '')}）——衡量市场多空强弱，|CCI|>100 触发预警">` +
          `📊 CCI(20)·上证 ${escHtml(String(it.value))} ${arrow} ${escHtml(it.trend)}` +
          `${it.warn ? ' ⚠ 预警' : ''}</span></span>`;
      }
      if (it.type === 'fut') {   // 伦敦金/布伦特原油：暗金色（与红绿涨跌、预警红均区分）
        const pct = parseFloat(it.pct);
        const arrow = pct > 0 ? '▲' : (pct < 0 ? '▼' : '—');
        return `<span class="tk-item"><a class="tk-fut" href="${escHtml(it.url)}" target="_blank" rel="noopener" ` +
          `title="点击查看 ${escHtml(it.label)} 行情数据">◈ ${escHtml(it.label)} ${escHtml(it.value)} ${arrow} ${escHtml(it.pct)}%</a></span>`;
      }
      if (it.type === 'fut_alert') {   // 期货异动：|主连涨跌幅|≥5%，点击跳 iwencai 筛查
        const pct = parseFloat(it.pct);
        return `<span class="tk-item"><a class="tk-fut-alert" href="${escHtml(it.url)}" target="_blank" rel="noopener" ` +
          `title="期货异动预警：主力连续涨跌幅绝对值≥5%，点击筛查相关标的">⚡ 期货异动 <b>${escHtml(it.label)}</b> ${escHtml(it.pct)}%</a></span>`;
      }
      if (it.type === 'quote') {   // 小概率彩蛋一句话
        return `<span class="tk-item"><span class="tk-quote">✦ ${escHtml(it.text)} ✦</span></span>`;
      }
      return '';
    }).join('<span class="tk-sep">◆</span>');
    const track = $('#tickerTrack');
    // 内容双份拼接实现无缝横向循环；速度随内容长度自适应，悬停暂停（CSS）
    track.innerHTML = seg + '<span class="tk-sep">◆</span>' + seg + '<span class="tk-sep">◆</span>';
    track.style.setProperty('--ticker-dur', Math.max(60, items.length * 8) + 's');
    $('#tickerBar').hidden = false;
  } catch (e) { console.warn('ticker:', e); }
}

// ========= 大周期通道（可复用模块） =========
let _cycleLast = null;

async function runCycle() {
  hideMeme('cycleMeme');
  const cfg = {
    channel_window: num('#cChanWin', 48),
    trend_window: num('#cTrendWin', 120),
    upper_q: num('#cUpperQ', 0.95),
    lower_q: num('#cLowerQ', 0.05),
    high_pos: num('#cHighPos', 80),
    low_pos: num('#cLowPos', 20),
  };
  const source = $('#cycleSource').value;
  const fy = num('#cForecastYears', 3);
  showOverlay('loading', `大周期分析中（取月K）…`);
  try {
    const data = await API.cycleRun({ source, cfg, forecast_years: fy });
    hideOverlay();
    if (data.error) { showMeme('cycleMeme', 'error', '分析失败', String(data.error).slice(0, 140)); return; }
    _cycleLast = data;
    renderCycle(data);
    const v = data.verdict || {}, te = data.trend_engine || {};
    showMeme('cycleMeme', 'success', `${v.trend || '—'} · 评分 ${te.total > 0 ? '+' : ''}${te.total ?? '—'} 🧭`,
      `数据源：${data.source_label || '—'}`);
  } catch (e) {
    hideOverlay();
    showMeme('cycleMeme', 'error', '请求失败', String(e));
  }
}

function renderCycle(data) {
  const m = data.metrics || {}, v = data.verdict || {}, te = data.trend_engine || {};
  $('#cycleNotes').textContent =
    `数据源：${data.source_label || '—'}（${data.source_rows || '—'} 个月）` +
    ((data.notes || []).length ? ' ｜ ' + data.notes.join(' ｜ ') : '');
  const tot = te.total ?? 0;
  const trendCls = tot > 0 ? 'up' : 'down';
  $('#cycleTrend').innerHTML =
    `<div class="v-big ${trendCls}">${escHtml(v.trend || '—')} <span class="v-mut">多因子评分 <b>${tot > 0 ? '+' : ''}${tot}</b>/100</span></div>` +
    `<div>对数年化 <b class="${m.annualized_pct > 0 ? 'up' : 'down'}">${m.annualized_pct > 0 ? '+' : ''}${m.annualized_pct}%</b>（近 ${v.trend_window} 个月）｜ 月线 MA12 ${m.ma12} ${m.ma_bull ? '＞' : '＜'} MA36 ${m.ma36 ?? '—'}（${m.ma_bull ? '多头' : '空头'}排列）</div>` +
    `<div class="v-mut">六法加权：RSL 25% · 均线排列 20% · Weinstein 阶段 20% · 月线 MACD 15% · ROC 动量 15% · 通道位置 5%（≥50 强势 / ±20 内震荡）</div>`;
  $('#cycleFactors').innerHTML = (te.factors || []).map(x => {
    const cls = x.score > 0.15 ? 'up' : (x.score < -0.15 ? 'down' : '');
    const w = Math.min(Math.abs(x.score) * 100, 100);
    return `<div class="tf-row"><span class="tf-name" title="${escHtml(x.desc)}">${escHtml(x.name)}</span>` +
      `<span class="tf-val">${escHtml(x.value)}</span>` +
      `<span class="tf-bar"><i class="${cls}" style="width:${w}%"></i></span>` +
      `<span class="tf-score ${cls}">${x.score > 0 ? '+' : ''}${x.score}</span>` +
      `<span class="tf-judge ${cls}">${escHtml(x.judge)}</span></div>` +
      `<div class="tf-desc">${escHtml(x.desc)}</div>`;
  }).join('');
  const posCls = m.position_pct >= v.high_th ? 'up' : (m.position_pct <= v.low_th ? 'down' : '');
  $('#cyclePos').innerHTML =
    `<div class="v-big ${posCls}">通道位置 ${m.position_pct}% · 周期${escHtml(v.zone || '—')}</div>` +
    `<div>距上轨 <b>${m.dist_upper_pct > 0 ? '+' : ''}${m.dist_upper_pct}%</b> ｜ 距下轨 <b>${m.dist_lower_pct > 0 ? '+' : ''}${m.dist_lower_pct}%</b></div>` +
    `<div class="v-mut">近 ${v.channel_window} 个月通道 · 月斜率 ${m.channel_slope} ｜ 全历史分位 ${m.history_pct}%` +
    (m.chg_12m != null ? ` ｜ 近12月 ${m.chg_12m > 0 ? '+' : ''}${m.chg_12m}%` : '') + `</div>`;
  $('#cycleAdvice').innerHTML =
    '<ul>' + (data.advice || []).map(a => `<li>${escHtml(a)}</li>`).join('') + '</ul>';
  $('#cycleForecast').innerHTML =
    `<div>预测年数：<b>${data.forecast_years || '—'} 年</b>（可在上方参数区调整 1~14 年）</div>` +
    `<div>${escHtml(data.forecast_note || '')}</div>` +
    `<div class="v-mut">图示：实线 = 历史月K与通道；虚线 = 通道斜率外推（统计口径与历史一致，轨道宽度不变）</div>`;
  $('#cycleVerdicts').hidden = false;
  drawCycleChart(data);
}

function drawCycleChart(data) {
  const rows = (data.monthly || []).filter(r => r.close != null);
  // 走势预测已按要求隐藏：不再绘制外推虚线（fc 置空后所有预测分支自然跳过，
  // min/max 也不再纳入预测轨道，图面只保留历史月K与通道）
  const fc = [];
  const canvas = $('#cycleChart');
  canvas.hidden = false;
  const { ctx, W, H } = hiDPI(canvas);   // 2x 超采样高清
  const PAD = 54;
  ctx.clearRect(0, 0, W, H);
  const N = rows.length, F = fc.length, TOT = N + F;
  if (!N) return;
  // min/max 纳入预测轨道
  let mn = Math.min(...rows.map(k => Math.min(k.low ?? k.close, k.lower ?? Infinity)));
  let mx = Math.max(...rows.map(k => Math.max(k.high ?? k.close, k.upper ?? -Infinity)));
  fc.forEach(p => {
    mn = Math.min(mn, p.lower); mx = Math.max(mx, p.upper);
  });
  if (!Number.isFinite(mn) || !Number.isFinite(mx)) { mn = 0; mx = 1; }
  const pad = (mx - mn) * 0.07 || 1; mn -= pad; mx += pad;
  const xStep = (W - PAD * 2) / TOT;
  const yMap = v => H - PAD - ((v - mn) / (mx - mn)) * (H - PAD * 2);
  const xAt = i => PAD + i * xStep + xStep / 2;
  ctx.strokeStyle = '#eeebe2'; ctx.lineWidth = 1; ctx.font = '10px sans-serif';
  for (let i = 0; i <= 4; i++) {
    const y = PAD + (H - PAD * 2) * (i / 4);
    ctx.beginPath(); ctx.moveTo(PAD, y); ctx.lineTo(W - PAD, y); ctx.stroke();
    ctx.fillStyle = '#a3a3a3';
    ctx.fillText((mx - (mx - mn) * i / 4).toFixed(0), 8, y + 3);
  }
  // 预测起点竖虚线
  const fStart = rows[N - 1];
  if (F > 0) {
    ctx.strokeStyle = '#b8860b'; ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(xAt(N - 1), PAD); ctx.lineTo(xAt(N - 1), H - PAD); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#b8860b'; ctx.fillText('预测起点 →', xAt(N - 1) + 4, PAD + 10);
  }
  // 通道带填充：历史（较深）+ 预测（更浅）
  const i0 = rows.findIndex(r => r.upper != null);
  if (i0 >= 0 && N - i0 > 1) {
    ctx.beginPath();
    for (let i = i0; i < N; i++) { const x = xAt(i), y = yMap(rows[i].upper); i === i0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); }
    for (let i = N - 1; i >= i0; i--) ctx.lineTo(xAt(i), yMap(rows[i].lower));
    ctx.closePath();
    ctx.fillStyle = 'rgba(90,120,190,0.07)';
    ctx.fill();
  }
  if (F > 1) {
    ctx.beginPath();
    ctx.moveTo(xAt(N - 1), yMap(fStart.upper ?? fc[0].upper));
    fc.forEach((p, k) => ctx.lineTo(xAt(N + k), yMap(p.upper)));
    for (let k = F - 1; k >= 0; k--) ctx.lineTo(xAt(N + k), yMap(fc[k].lower));
    ctx.lineTo(xAt(N - 1), yMap(fStart.lower ?? fc[0].lower));
    ctx.closePath();
    ctx.fillStyle = 'rgba(184,134,11,0.06)';
    ctx.fill();
  }
  // 轨道线（历史实线）
  const line = (key, color, width, dash) => {
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash || []);
    let started = false;
    rows.forEach((r, i) => {
      if (r[key] == null) return;
      const x = xAt(i), y = yMap(r[key]);
      if (!started) { ctx.beginPath(); ctx.moveTo(x, y); started = true; }
      else ctx.lineTo(x, y);
    });
    ctx.stroke(); ctx.setLineDash([]);
  };
  line('upper', '#c0392b', 1.4, [6, 4]);
  line('mid', '#8a8a8a', 1, [2, 3]);
  line('lower', '#2e8b57', 1.4, [6, 4]);
  // 预测轨道（虚线延伸：从最后历史点连到各预测点）
  if (F > 0) {
    const fline = (key, color, width) => {
      ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash([5, 5]);
      ctx.beginPath();
      ctx.moveTo(xAt(N - 1), yMap(fStart[key] ?? fc[0][key]));
      fc.forEach((p, k) => ctx.lineTo(xAt(N + k), yMap(p[key])));
      ctx.stroke(); ctx.setLineDash([]);
    };
    fline('upper', '#c0392b', 1.3);
    fline('mid', '#8a8a8a', 1);
    fline('lower', '#2e8b57', 1.3);
    // 预测终点标注
    const fe = fc[F - 1];
    ctx.fillStyle = '#b8860b'; ctx.font = 'bold 11px sans-serif';
    ctx.fillText(`外推 ${fe.date} 中轨 ${fe.mid}`, Math.min(xAt(TOT - 1) - 60, W - PAD - 130), yMap(fe.mid) - 8);
  }
  // 历史月K（涨红跌绿）
  const bw = Math.max(xStep * 0.55, 1);
  rows.forEach((r, i) => {
    if (r.open == null || r.close == null) return;
    const up = r.close >= r.open;
    ctx.strokeStyle = up ? '#d23f31' : '#1d9a6c';
    ctx.fillStyle = up ? '#d23f31' : '#1d9a6c';
    const x = xAt(i);
    if (r.high != null && r.low != null) {
      ctx.beginPath(); ctx.moveTo(x, yMap(r.high)); ctx.lineTo(x, yMap(r.low)); ctx.lineWidth = 1; ctx.stroke();
    }
    const yO = yMap(r.open), yC = yMap(r.close);
    const top = Math.min(yO, yC), hgt = Math.max(Math.abs(yC - yO), 1);
    ctx.fillRect(x - bw / 2, top, bw, hgt);
  });
  // X 轴年份标注（历史 + 预测区）
  ctx.fillStyle = '#a3a3a3'; ctx.font = '10px sans-serif';
  rows.forEach((r, i) => {
    const yr = (r.date || '').slice(0, 4);
    const prev = i ? (rows[i - 1].date || '').slice(0, 4) : '';
    if (yr && yr !== prev) ctx.fillText(yr, xAt(i) - 11, H - PAD + 14);
  });
  fc.forEach((p, k) => {
    const yr = (p.date || '').slice(0, 4);
    const prev = k ? (fc[k - 1].date || '').slice(0, 4) : fStart.date.slice(0, 4);
    if (yr && yr !== prev && k % 2 === 0) ctx.fillText(yr + '(预)', xAt(N + k) - 14, H - PAD + 14);
  });
  // 图例 + 最新点
  ctx.font = '10.5px sans-serif';
  ctx.fillStyle = '#c0392b'; ctx.fillText('— 上轨', W - PAD - 200, 16);
  ctx.fillStyle = '#8a8a8a'; ctx.fillText('— 中轨', W - PAD - 150, 16);
  ctx.fillStyle = '#2e8b57'; ctx.fillText('— 下轨', W - PAD - 100, 16);
  const lastRow = rows[N - 1];
  ctx.beginPath(); ctx.arc(xAt(N - 1), yMap(lastRow.close), 3.2, 0, Math.PI * 2);
  ctx.fillStyle = '#b86b3f'; ctx.fill();
  ctx.fillStyle = '#2a2a2a'; ctx.font = 'bold 13px sans-serif';
  ctx.fillText(`${data.source_label || ''} · ${N} 个月`, PAD, 18);
  ctx.fillStyle = '#6b6b6b'; ctx.font = '11px sans-serif';
  ctx.fillText(`最新 ${lastRow.close} (${lastRow.date}) ｜ 通道位置 ${(data.metrics || {}).position_pct}%`, PAD, H - 10);
}

function exportCycleCsv() {
  if (!_cycleLast) { showMeme('cycleMeme', 'ask', '还没有分析结果', '先点「分析大周期」'); return; }
  const rows = _cycleLast.monthly || [];
  if (!rows.length) { showMeme('cycleMeme', 'empty', '没有可导出的数据', ''); return; }
  const head = '月份,开盘,最高,最低,收盘,中轨,上轨,下轨';
  const body = rows.map(r => [r.date, r.open ?? '', r.high ?? '', r.low ?? '', r.close ?? '',
    r.mid ?? '', r.upper ?? '', r.lower ?? ''].join(','));
  const csv = '\ufeff' + [head, ...body].join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '大周期_' + new Date().toISOString().slice(0, 10) + '.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}

// ========= 期货视察（主连多因子打分） =========
let _futLast = null;

async function runFutures(opts = {}) {
  hideMeme('futMeme');
  const cfg = {
    weeks: Number($('#futWeeks').value) || 8,
    top_n: Number($('#futTopN').value) || 5,
    max_news: Number($('#futNewsN').value),
    min_vol: Number($('#fMinVol').value),
    win_scale: Number($('#fWinScale').value),
    adx_scale: Number($('#fAdxScale').value),
    rsi_scale: Number($('#fRsiScale').value),
    with_news: Number($('#futNewsN').value) > 0,
    refresh_symbols: !!opts.refreshSymbols,
    weights: {
      mom: Number($('#fWMom').value), er: Number($('#fWEr').value),
      adx: Number($('#fWAdx').value), ma: Number($('#fWMa').value),
      macd: Number($('#fWMacd').value), rsi: Number($('#fWRsi').value),
      oi: Number($('#fWOi').value), news: Number($('#fWNews').value),
    },
  };
  const sec = $('#futSector').value;
  if (sec) cfg.sectors = [sec];
  showOverlay('loading', `期货视察中（近 ${cfg.weeks} 周窗口）…`);
  try {
    const data = await API.futuresScan({ cfg });
    hideOverlay();
    if (data.cancelled) { showMeme('futMeme', 'ask', '已取消', '可调整参数后重新视察'); return; }
    if (data.error) { showMeme('futMeme', 'error', '视察失败', String(data.error).slice(0, 160)); return; }
    _futLast = data;
    renderFutures(data);
    const t = (data.top || [])[0], b = (data.bottom || [])[0];
    showMeme('futMeme', 'success',
      `已评估 ${data.n_scored || 0} 个主连 🛢`,
      `最强 ${t ? t.name + ' ' + (t.score > 0 ? '+' : '') + t.score : '—'} ｜ 最弱 ${b ? b.name + ' ' + b.score : '—'} ｜ 耗时 ${data.elapsed ?? '—'}s`);
  } catch (e) {
    hideOverlay();
    showMeme('futMeme', 'error', '请求失败', String(e).slice(0, 160));
  }
}

// 迷你走势（sparkline）：canvas 绘制，涨红跌绿
function drawSpark(canvas, vals) {
  if (!canvas || !vals || vals.length < 2) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 160, h = canvas.clientHeight || 34;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  const mn = Math.min(...vals), mx = Math.max(...vals);
  const span = (mx - mn) || 1;
  const xAt = i => (i / (vals.length - 1)) * (w - 2) + 1;
  const yAt = v => h - 3 - ((v - mn) / span) * (h - 6);
  const up = vals[vals.length - 1] >= vals[0];
  // 面积
  ctx.beginPath();
  ctx.moveTo(xAt(0), yAt(vals[0]));
  vals.forEach((v, i) => ctx.lineTo(xAt(i), yAt(v)));
  ctx.lineTo(xAt(vals.length - 1), h); ctx.lineTo(xAt(0), h); ctx.closePath();
  ctx.fillStyle = up ? 'rgba(210,63,49,0.12)' : 'rgba(29,154,108,0.12)';
  ctx.fill();
  // 折线
  ctx.beginPath();
  vals.forEach((v, i) => i ? ctx.lineTo(xAt(i), yAt(v)) : ctx.moveTo(xAt(i), yAt(v)));
  ctx.strokeStyle = up ? '#d23f31' : '#1d9a6c';
  ctx.lineWidth = 1.6; ctx.stroke();
}

function futCard(r, rank) {
  const cls = r.score >= 0 ? 'up' : 'down';
  const w = Math.min(Math.abs(r.score), 100);
  const fac = (r.factors || []).map(x => {
    const c = x.score > 0.15 ? 'up' : (x.score < -0.15 ? 'down' : '');
    return `<div class="tf-row"><span class="tf-name" title="${escHtml(x.desc)}">${escHtml(x.name)}</span>` +
      `<span class="tf-val">${escHtml(x.value)}</span>` +
      `<span class="tf-score ${c}">${x.score > 0 ? '+' : ''}${x.score}</span></div>`;
  }).join('');
  const news = (r.news_hits || []).slice(0, 2)
    .map(t => `<div class="fut-news-hit">📰 ${escHtml(t)}</div>`).join('');
  return `<div class="fut-card ${cls}">
    <div class="fut-card-head">
      <span class="fut-rank">#${rank}</span>
      <span class="fut-name">${escHtml(r.name)}</span>
      <span class="fut-sym">${escHtml(r.symbol)}</span>
      <span class="fut-ex">${escHtml(r.exchange_name || r.exchange || '')}</span>
      <span class="fut-score ${cls}">${r.score > 0 ? '+' : ''}${r.score}</span>
    </div>
    <div class="fut-card-sub">
      <span class="${cls}">${escHtml(r.label)}</span> ·
      窗口涨跌 <b class="${r.chg_pct >= 0 ? 'up' : 'down'}">${r.chg_pct >= 0 ? '+' : ''}${r.chg_pct}%</b> ·
      最新 <b>${r.last.toLocaleString('zh-CN')}</b>（${escHtml(r.last_date || '')}）·
      ${escHtml(r.sector_name || '')}
    </div>
    <div class="fut-bar"><i class="${cls}" style="width:${w}%"></i></div>
    <canvas class="fut-spark" width="320" height="40" data-spark="${escHtml(JSON.stringify(r.spark || []))}"></canvas>
    <div class="fut-factors">${fac}</div>
    ${news}
  </div>`;
}

function renderFutures(data) {
  const p = data.params || {};
  $('#futNotes').textContent =
    `主连 ${data.n_symbols ?? '—'} 个 · 有效评估 ${data.n_scored ?? '—'} 个 · 窗口 近 ${p.weeks} 周 · ` +
    `数据截至 ${(data.rows || [])[0] ? data.rows[0].last_date : '—'}` +
    ((data.notes || []).length ? ' ｜ ' + data.notes.join(' ｜ ') : '') +
    ((data.filtered || []).length ? ` ｜ 已过滤不活跃 ${data.filtered.length} 个` : '');

  const n = (data.top || []).length;
  $('#futTopNLabel').textContent = n;
  $('#futBotNLabel').textContent = (data.bottom || []).length;
  $('#futTopList').innerHTML = (data.top || []).map((r, i) => futCard(r, i + 1)).join('')
    || '<div class="fut-empty">无看涨品种</div>';
  $('#futBottomList').innerHTML = (data.bottom || []).map((r, i) => futCard(r, i + 1)).join('')
    || '<div class="fut-empty">无看跌品种</div>';
  $('#futBlocks').hidden = false;
  // sparkline 逐个绘制（canvas 尺寸依赖布局，需在插入 DOM 后执行）
  $$('#futBlocks .fut-spark').forEach(cv => {
    try { drawSpark(cv, JSON.parse(cv.dataset.spark || '[]')); }
    catch (e) { /* 数据异常时静默跳过 */ }
  });

  // 板块汇总
  const secs = data.sectors || [];
  if (secs.length) {
    const mx = Math.max(...secs.map(s => Math.abs(s.avg_score)), 1);
    $('#futSectors').innerHTML = secs.map(s => {
      const c = s.avg_score > 0 ? 'up' : 'down';
      return `<div class="sec-row"><span class="sec-name">${escHtml(s.sector_name)}</span>` +
        `<span class="tf-bar"><i class="${c}" style="width:${Math.abs(s.avg_score) / mx * 100}%"></i></span>` +
        `<span class="sec-score ${c}">${s.avg_score > 0 ? '+' : ''}${s.avg_score}</span>` +
        `<span class="sec-mut">${s.n} 个 · 最强 ${escHtml(s.best_name || '—')}</span></div>`;
    }).join('');
    $('#futSectorBlock').hidden = false;
  }

  // 新闻
  const news = data.news || [];
  if (news.length) {
    $('#futNewsList').innerHTML = '<ul class="fut-news">' + news.map(x => {
      const t = escHtml(x.title || '');
      const s = Number(x.sent || 0);
      // 情绪徽章：与打分「⑧ 新闻情绪」同源（都来自 _hit_sym + _sentiment），
      // 让用户能直接看出这条新闻给品种加了正票还是负票。涨=红/跌=绿（A 股口径）。
      const sent = s > 0 ? '<span class="fut-news-sent up">偏多</span>'
        : (s < 0 ? '<span class="fut-news-sent down">偏空</span>' : '');
      const title = x.url
        ? `<a class="fut-news-title" href="${escHtml(x.url)}" target="_blank" rel="noopener" title="${t}">${t}</a>`
        : `<span class="fut-news-title" title="${t}">${t}</span>`;
      return `<li><span class="fut-news-src">${escHtml(x.source || '')}</span>` +
        `<span class="fut-news-time">${escHtml((x.time || '').slice(5, 16))}</span>` +
        sent + title +
        (x.symbols && x.symbols.length
          ? `<span class="fut-news-sym">→ ${x.symbols.map(escHtml).join('、')}</span>` : '') +
        `</li>`;
    }).join('') + '</ul>';
    $('#futNewsBlock').hidden = false;
  } else {
    $('#futNewsBlock').hidden = true;
  }

  // 全部品种表（自渲染，避免 renderTable 把期货名当股票名生成同花顺链接）
  const rows = data.rows || [];
  const t = $('#futAllTable');
  t.querySelector('thead').innerHTML =
    '<tr><th>排名</th><th>名称</th><th>代码</th><th>交易所</th><th>板块</th><th>评分</th>' +
    '<th>判定</th><th>窗口涨跌</th><th>最新价</th><th>数据日期</th><th>关联新闻</th></tr>';
  t.querySelector('tbody').innerHTML = rows.map((r, i) => {
    const c = r.score >= 0 ? 'up' : 'down';
    return `<tr><td>${i + 1}</td><td>${escHtml(r.name)}</td><td>${escHtml(r.symbol)}</td>` +
      `<td>${escHtml(r.exchange_name || r.exchange || '')}</td><td>${escHtml(r.sector_name || '')}</td>` +
      `<td class="num ${c}">${r.score > 0 ? '+' : ''}${r.score}</td><td>${escHtml(r.label)}</td>` +
      `<td class="num ${r.chg_pct >= 0 ? 'up' : 'down'}">${r.chg_pct >= 0 ? '+' : ''}${r.chg_pct}%</td>` +
      `<td class="num">${r.last.toLocaleString('zh-CN')}</td><td>${escHtml(r.last_date || '')}</td>` +
      `<td>${(r.news_hits || []).length} 条</td></tr>`;
  }).join('');
  $('#futAllWrap').hidden = false;
}

// 期货板块下拉（后端 SECTORS 字典，与后端保持同源，避免前后端硬编码漂移）
async function initFuturesSectors() {
  try {
    const list = await API.futuresSectors();
    const sel = $('#futSector');
    if (!sel || !Array.isArray(list)) return;
    sel.innerHTML = '<option value="">全部板块</option>' +
      list.map(s => `<option value="${escHtml(s.key)}">${escHtml(s.label)}</option>`).join('');
  } catch (e) { console.warn('futures sectors:', e); }
}

function exportFuturesCsv() {
  if (!_futLast) { showMeme('futMeme', 'ask', '还没有视察结果', '先点「开始视察」'); return; }
  const rows = _futLast.rows || [];
  if (!rows.length) { showMeme('futMeme', 'empty', '没有可导出的数据', ''); return; }
  const head = ['排名', '名称', '代码', '交易所', '板块', '评分', '判定', '窗口涨跌%',
                '最新价', '数据日期', '日均成交(手)', '持仓量(手)', '关联新闻数'];
  const body = rows.map((r, i) => [i + 1, r.name, r.symbol, r.exchange_name || r.exchange,
    r.sector_name, r.score, r.label, r.chg_pct, r.last, r.last_date, r.avg_vol, r.oi,
    (r.news_hits || []).length].join(','));
  const csv = '\ufeff' + [head.join(','), ...body].join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '期货视察_' + (_futLast.date || new Date().toISOString().slice(0, 10)) + '.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}

// ========= K 线 =========
async function loadKline() {
  const code = $('#klineCode').value.trim();
  if (!code) { showMeme('klineMeme', 'ask', '请输入代码', '例如 600519 / 000001'); return; }
  showOverlay('loading', '正在加载 K 线…');
  const data = await API.kline(code, Number($('#klineDays').value));
  hideOverlay();
  if (!data.bars || data.bars.length === 0) {
    showMeme('klineMeme', 'error', '没有 K 线数据', data.error || '请检查代码');
    return;
  }
  hideMeme('klineMeme');
  drawKlineCanvas(data.bars, data.code, data.display_days);
  $('#klineLink').href = thsLink(data.code);
}
function drawKlineCanvas(bars, code, displayDays) {
  // bars 含后端多取的 60 根均线预热段；K 线只显示最后 displayDays 根，
  // MA 用全量数据计算后对齐显示窗口 → 显示区间内 MA5/10/20/60 全程可画
  const dd = Math.min(displayDays || bars.length, bars.length);
  const view = bars.slice(-dd);          // 显示窗口
  const v0 = bars.length - view.length;  // 预热段偏移
  const canvas = $('#klineChart');
  const { ctx, W, H } = hiDPI(canvas);   // 2x 超采样高清
  const PAD = 50;
  ctx.clearRect(0, 0, W, H);
  const N = view.length;
  let mn = Math.min(...view.map(k => k.low)), mx = Math.max(...view.map(k => k.high));
  const ma = n => bars.map((_, i) => i < n - 1 ? null :
    bars.slice(i - n + 1, i + 1).reduce((s, b) => s + b.close, 0) / n);
  const maArrs = [ma(5), ma(10), ma(20), ma(60)];
  maArrs.forEach(arr => arr.slice(v0).forEach(v => {
    if (v != null && Number.isFinite(v)) { mn = Math.min(mn, v); mx = Math.max(mx, v); }
  }));
  const pad = (mx - mn) * 0.06 || 1; mn -= pad; mx += pad;
  const xStep = (W - PAD * 2) / N;
  const xAt = i => PAD + i * xStep + xStep / 2;
  const yMap = v => H - PAD - ((v - mn) / (mx - mn)) * (H - PAD * 2);
  ctx.strokeStyle = '#eeebe2'; ctx.lineWidth = 1;
  for (let i = 0; i < 5; i++) {
    const y = PAD + (H - PAD * 2) * (i / 4);
    ctx.beginPath(); ctx.moveTo(PAD, y); ctx.lineTo(W - PAD, y); ctx.stroke();
    ctx.fillStyle = '#a3a3a3'; ctx.font = '10px sans-serif';
    ctx.fillText((mx - (mx - mn) * i / 4).toFixed(2), 6, y + 3);
  }
  const candleW = Math.max(xStep * 0.62, 1);
  view.forEach((k, i) => {
    const x = xAt(i);
    const up = k.close >= k.open;
    const color = up ? '#c0392b' : '#2e8b57';   // 涨红 跌绿
    ctx.strokeStyle = color; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, yMap(k.high)); ctx.lineTo(x, yMap(k.low)); ctx.stroke();
    const top = yMap(Math.max(k.open, k.close));
    const bot = yMap(Math.min(k.open, k.close));
    ctx.fillStyle = color;
    ctx.fillRect(x - candleW / 2, top, candleW, Math.max(bot - top, 1));
  });
  const maColors = ['#b86b3f', '#8a6a2f', '#4a7a8a', '#7a7a7a'];
  maArrs.forEach((arr, k) => {
    ctx.strokeStyle = maColors[k]; ctx.lineWidth = 1.4;
    let started = false;
    arr.slice(v0).forEach((v, i) => {
      if (v == null) return;
      if (!started) { ctx.beginPath(); ctx.moveTo(xAt(i), yMap(v)); started = true; }
      else ctx.lineTo(xAt(i), yMap(v));
    });
    ctx.stroke();
  });
  ctx.font = '10px sans-serif';
  ctx.fillStyle = '#b86b3f'; ctx.fillText('—MA5', W - PAD - 152, 16);
  ctx.fillStyle = '#8a6a2f'; ctx.fillText('—MA10', W - PAD - 116, 16);
  ctx.fillStyle = '#4a7a8a'; ctx.fillText('—MA20', W - PAD - 80, 16);
  ctx.fillStyle = '#7a7a7a'; ctx.fillText('—MA60', W - PAD - 44, 16);
  const last = view[view.length - 1];
  ctx.fillStyle = '#2a2a2a'; ctx.font = 'bold 13px sans-serif';
  ctx.fillText(`${code} · ${N} 个交易日`, PAD, 18);
  ctx.fillStyle = '#6b6b6b'; ctx.font = '11px sans-serif';
  ctx.fillText(`最新 ${last.close} (${last.date})`, PAD, H - 10);
}

// ========= 同步 =========
function setSyncBtn(syncing) {
  const b = $('#btnSync');
  if (!b) return;
  b.textContent = syncing ? '⏹ 停止同步' : '⬇ 同步数据';
  b.classList.toggle('btn-stop', syncing);
  b.title = syncing ? '停止当前正在进行的拉取任务' : '按当前范围增量同步最新行情';
}
async function startSync() {
  if (state.syncing) {   // 同步中 → 变成「停止」
    try { await API.syncStop(); } catch (e) { console.warn(e); }
    return;              // pollSync 识别 cancelled 后复位按钮
  }
  try {
    const withValuation = $('#syncWithValuation') ? $('#syncWithValuation').checked : true;
    const r = await API.syncStart({
      exchange: rangeVal(),
      with_valuation: withValuation,
      with_dividend: $('#syncWithDividend') ? $('#syncWithDividend').checked : true,
    });
    if (r.error || r.detail) { showOverlay('error', String(r.detail || r.error)); setTimeout(hideOverlay, 2000); return; }
    if (state.syncPoll) clearInterval(state.syncPoll);
    state.syncing = true;
    setSyncBtn(true);
    $('#syncBar').hidden = false;
    pollSync();
  } catch (e) { showOverlay('error', String(e)); setTimeout(hideOverlay, 2000); }
}
function pollSync() {
  const startTs = Date.now();
  state.syncPoll = setInterval(async () => {
    try {
      const p = await API.syncProgress();
      $('#syncFill').style.width = ((p.frac || 0) * 100).toFixed(1) + '%';
      $('#syncText').textContent = `${p.msg || ''} (${((p.frac || 0) * 100).toFixed(0)}%)`;
      const logs = p.logs || [];
      if (logs.length) {
        $('#syncLogs').innerHTML = logs.slice(-15).map(l => `<div class="log-line">${l}</div>`).join('');
        $('#syncLogs').scrollTop = $('#syncLogs').scrollHeight;
      }
      if (p.status !== 'running') {
        clearInterval(state.syncPoll); state.syncPoll = null;
        state.syncing = false;
        setSyncBtn(false);
        hideOverlay();
        if (p.status === 'cancelled') {
          showMeme('strategyMeme', 'info', '已停止同步', '未完成的部分下次点「同步数据」会继续');
          await refreshDbInfo();
        } else if (p.status === 'done' || p.status === 'idle') {
          showMeme('strategyMeme', 'success', '数据同步完成 ✨', '现在可以去筛选了');
          $('#freshTip').hidden = true;   // 数据已更新，滞后提示随之消失
          await refreshDbInfo();
        } else if (p.status === 'failed' || p.status === 'error') {
          showMeme('strategyMeme', 'error', '同步失败', p.msg || '');
        }
        setTimeout(() => $('#syncBar').hidden = true, 2500);
        return;
      }
      // 兜底：轮询超过 35 分钟仍未结束，提示（同步全市场本就较慢）
      if (Date.now() - startTs > 35 * 60 * 1000) {
        clearInterval(state.syncPoll); state.syncPoll = null;
        state.syncing = false;
        setSyncBtn(false);
        hideOverlay();
        showMeme('strategyMeme', 'error', '同步耗时过长', '可能网络较慢，可查看日志或稍后重试');
        $('#syncBar').hidden = true;
      }
    } catch (e) { console.warn(e); }
  }, 1500);
}

// ========= 状态 =========
async function refreshDbInfo() {
  try {
    const s = await API.status();
    const st = s.stats || {};
    const info = $('#dbInfo');
    info.textContent =
      `${st.stocks} 只股票 · ${(st.daily_rows / 10000).toFixed(0)} 万条日线 · 数据至 ${st.daily_max || '—'}` +
      (st.last_sync ? ` · 上次同步 ${st.last_sync}` : '');
    info.title = '数据留存于本地 data/unified_data.db，重启程序不丢失；' +
      '「⬇ 同步数据」只拉取增量（自各股最新日期起继续），不会重拉全量';
    state.sectors = s.sectors || [];
    // 恢复同步状态：刷新页面后若后端仍在同步，恢复进度条 + 停止按钮（可接管并停止）
    const syncStatus = (s.sync && s.sync.status) || 'idle';
    state.syncing = (syncStatus === 'running');
    setSyncBtn(state.syncing);
    if (state.syncing && !state.syncPoll) {
      $('#syncBar').hidden = false;
      pollSync();
    }
  } catch (e) { console.warn(e); }
}

// ========= 数据滞后提示（本地数据落后最近交易日时提示同步，一天最多一次） =========
// 判定在后端：本地 000001 最新日线日期 < 最近已完成交易日 → stale。
// 节假日/盘中由交易日历兜住（此时本地与最近完成交易日一致，不提示不误报）。
function _freshTipShownToday() {
  const today = new Date().toISOString().slice(0, 10);
  return localStorage.getItem('freshTipDate') === today;
}
function showFreshTip(f) {
  const tip = $('#freshTip');
  if (!tip) return;
  $('#freshTipText').textContent =
    `⚠ 本地数据截至 ${f.local_max || '—'}，最新交易日为 ${f.last_trade_date || '—'}，` +
    '筛选与打分可能不含最新行情';
  tip.hidden = false;
}
async function checkFreshness() {
  try {
    const f = await API.freshness();
    if (!f || !f.stale) return;          // 相符（或降级/空库）→ 不显示
    if (_freshTipShownToday()) return;   // 今天已提示过 → 不再打扰
    localStorage.setItem('freshTipDate', new Date().toISOString().slice(0, 10));
    showFreshTip(f);
  } catch (e) { console.warn(e); }
}

// ========= 初始化 =========
function goBack() {
  // 「↩ 退回」：回到上一个界面；没有历史时回主页（第一个 tab）
  const first = $$('.tab')[0];
  const home = first ? first.dataset.tab : null;
  const prev = _panelHistory.pop() || home;
  if (prev) switchTab(prev, true);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function injectBackButtons() {
  $$('.panel-head').forEach(ph => {
    if (ph.querySelector('.back-btn')) return;  // 已注入跳过
    ph.insertAdjacentHTML('beforeend',
      '<button class="back-btn" title="退回到上一个界面">↩ 退回</button>');
  });
  $$('.back-btn').forEach(b => b.addEventListener('click', goBack));
}

async function init() {
  // file:// 直开检测：双击 index.html 时所有 /api 请求会被浏览器拦截，给出明确指引
  if (location.protocol === 'file:') {
    const bar = document.createElement('div');
    bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;background:#c0392b;color:#fff;padding:10px 16px;font-size:14px;line-height:1.6;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.25);';
    bar.textContent = '⚠️ 检测到直接双击打开了本地文件。请关闭本页，双击项目根目录的 start.bat 启动服务，然后访问 http://127.0.0.1:8765/ ，所有数据功能才能正常使用。';
    (document.body || document.documentElement).appendChild(bar);
  }
  bindTabs(); bindRange();
  injectBackButtons();
  $('#btnCancelTask').addEventListener('click', cancelRunning);
  const bgBtn = $('#btnBgRun');
  if (bgBtn) bgBtn.addEventListener('click', minimizeOverlay);
  bindSimilarVolToggle();
  // 月份计算机：年 → 月（四舍五入），如 1.5 年 → 18 个月
  const btnY2M = $('#btnYearToMonth');
  if (btnY2M) btnY2M.addEventListener('click', () => {
    const out = $('#cYearOut');
    const v = parseFloat($('#cYearInput').value);
    if (!Number.isFinite(v) || v < 0) { out.textContent = '请输入有效年数'; return; }
    out.textContent = Math.round(v * 12) + ' 个月';
  });
  const csvMap = [['btnStrategyCsv','strategyTable','策略扫描'],
                  ['btnCondCsv','condTable','条件筛选'],
                  ['btnPatternCsv','patternTable','形态打分'],
                  ['btnAntCsv','antTable','蚂蚁扫描'],
                  ['btnAnt1000Csv','ant1000Table','长周期筛选'],
                  ['btnSimilarCsv','similarATable','相似股A类'],
                  ['btnAlertCsv','alertSignalTable','前瞻预警核心信号']];
  csvMap.forEach(([bid, tid, label]) => {
    const b = $('#' + bid);
    if (b) b.addEventListener('click', () => downloadCsv(tid, label + '_' + new Date().toISOString().slice(0,10) + '.csv'));
  });
  $('#btnRunStrategies').addEventListener('click', runStrategies);
  $('#btnRunCond').addEventListener('click', runConditions);
  $('#btnCondAll').addEventListener('click', () => $$('#condGrid input').forEach(cb => { cb.checked = true; cb.closest('.cond-item').classList.add('active'); }));
  $('#btnCondNone').addEventListener('click', () => $$('#condGrid input').forEach(cb => { cb.checked = false; cb.closest('.cond-item').classList.remove('active'); }));
  $('#btnRunPattern').addEventListener('click', runPattern);
  $('#btnRunAnt').addEventListener('click', runAnt);
  $('#btnRunAnt1000').addEventListener('click', runAnt1000);
  refreshAnt1000Cache();   // 长周期历史数据缓存状态（异步，失败静默）
  $('#btnRunSimilar').addEventListener('click', runSimilar);
  $('#btnRunAlert').addEventListener('click', runAlert);
  $('#btnAlertReport').addEventListener('click', exportAlertReport);
  $('#btnRunCycle').addEventListener('click', runCycle);       // 此前缺失绑定，导致大周期点击无响应
  $('#btnCycleCsv').addEventListener('click', exportCycleCsv); // 此前缺失绑定
  $('#btnRunFutures').addEventListener('click', () => runFutures());
  $('#btnFuturesCsv').addEventListener('click', exportFuturesCsv);
  $('#btnFuturesRefreshSym').addEventListener('click', () => runFutures({ refreshSymbols: true }));
  bindSecToggles();
  bindTickerRefresh();
  $('#similarVolMode').addEventListener('change', e => {
    $('#similarVolThreshWrap').hidden = (e.target.value !== 'similar');
  });
  $('#btnLoadKline').addEventListener('click', loadKline);
  $('#klineCode').addEventListener('keydown', e => { if (e.key === 'Enter') loadKline(); });
  // 三张图表：点击在新窗口打开高清大图
  [['#similarChart', '相似股走势对比'], ['#cycleChart', '大周期通道'],
   ['#klineChart', 'K线走势'], ['#ant1000Chart', '长周期周期图']]
    .forEach(([sel, name]) => {
      const cv = $(sel);
      if (cv) {
        cv.style.cursor = 'zoom-in'; cv.title = '点击在新窗口打开高清大图';
        cv.addEventListener('click', () => openChartWindow(cv, name));
      }
    });
  $('#similarCode').addEventListener('keydown', e => { if (e.key === 'Enter') runSimilar(); });
  $('#btnSync').addEventListener('click', startSync);
  initDivTiming();   // 分红时点预测面板（绑定按钮，懒加载数据）
  $('#freshTipClose').addEventListener('click', () => { $('#freshTip').hidden = true; });
  $('#freshTipSync').addEventListener('click', () => {
    $('#freshTip').hidden = true;   // 开始同步即收起提示（完成后由 done 分支保持隐藏）
    startSync();
  });
  $('#btnRefreshMeta').addEventListener('click', async () => {
    showOverlay('loading', '刷新基础信息…');
    try { await API.refreshMeta(); } catch (e) {}
    hideOverlay(); refreshDbInfo();
  });
  $('#syncToggle').addEventListener('click', () => $('#syncLogs').classList.toggle('open'));

  showOverlay('loading', '正在初始化…');
  try {
    await refreshDbInfo();
    checkFreshness();   // 异步不阻塞：数据滞后提示（一天最多一次）
    await initStrategy();
    await initConditions();
    initFuturesSectors();   // 期货板块下拉（异步，失败不影响主流程）
  } catch (e) { console.error(e); }
  hideOverlay();
  maybeShowBootstrap();   // 空库时弹出启动引导（选范围 → 同步 → 解锁模块）
  loadTicker();
  setInterval(loadTicker, 5 * 60 * 1000);   // 轮播条 5 分钟自动换一批
  bindGifFloat();
  $('#brandAvatar').src = pickMeme('welcome');
}

// 刷新字幕专用键：跳过后端 5 分钟缓存强制重建，只影响轮播条（旋转动画反馈，防连点）
function bindTickerRefresh() {
  const btn = $('#tickerRefresh');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    if (btn.dataset.busy === '1') return;
    btn.dataset.busy = '1'; btn.classList.add('spinning');
    try { await loadTicker(true); }
    finally { btn.dataset.busy = '0'; btn.classList.remove('spinning'); }
  });
}

// ========= 首次启动引导：空库强制选范围并同步（每次程序启动至多 1 次） =========
let _bootstrapDone = false;
async function maybeShowBootstrap() {
  if (_bootstrapDone) return;
  let stocks = -1, dailyRows = -1;
  try {
    const s = await API.status();
    stocks = (s.stats || {}).stocks || 0;
    dailyRows = (s.stats || {}).daily_rows || 0;
  } catch (e) { return; }               // 后端异常不打扰用户
  if (stocks > 0 && dailyRows > 0) return;   // 数据齐全才跳过；"有列表无日线"的半成品状态重新引导
  const modal = $('#bootstrapModal'), btn = $('#btnBootSync');
  modal.hidden = false;                 // 全屏遮罩自带"同步完成前禁用模块"效果
  // 若同步已在进行（如刷新页面），直接接管进度条
  let cur = null;
  try { cur = await API.syncProgress(); } catch (e) {}
  if (cur && cur.status === 'running') {
    $('#bootRanges').hidden = true; $('#bootProgress').hidden = false;
    $('#bootHint').textContent = '检测到数据同步正在进行中，已接管进度…';
    bootPoll();
    return;
  }
  const RANGE_NAME = { BJ: '北交所', SH: '沪市', SZ: '深市' };
  $$('#bootRanges input').forEach(r => r.addEventListener('change', () => {
    btn.disabled = false;
    btn.textContent = '同步 ' + (RANGE_NAME[r.value] || '全 A') + ' 数据';
  }));
  btn.addEventListener('click', async () => {
    const sel = $('#bootRanges input:checked');
    if (!sel) return;
    btn.disabled = true;
    $('#bootRanges').hidden = true; $('#bootProgress').hidden = false;
    $('#bootHint').textContent = '同步完成后此窗口自动关闭，期间其他模块不可运行；请勿关闭 start.bat 服务窗口（关窗会中断同步）';
    try {
      // 空库时 meta 表为空，sync_all 无股可同步——必须先拉取股票列表（两阶段）
      $('#bootProgressText').textContent = '第 1/2 步：拉取股票列表…';
      const r0 = await API.refreshMeta();
      if (r0 && (r0.error || r0.detail)) {
        $('#bootProgressText').textContent = '拉取列表失败：' + String(r0.detail || r0.error).slice(0, 120);
        btn.disabled = false; return;
      }
      bootPoll(async () => {   // 列表完成后进入第 2 步：真实同步
        $('#bootProgressText').textContent = '第 2/2 步：同步行情数据…';
        try {
          const r = await API.syncStart({ exchange: sel.value || null, with_valuation: true, with_dividend: true });
          if (r.error || r.detail) {
            $('#bootProgressText').textContent = '启动失败：' + String(r.detail || r.error).slice(0, 120);
            btn.disabled = false; return;
          }
          bootPoll(async () => {   // 同步完成 → 解锁
            _bootstrapDone = true;
            $('#bootstrapModal').hidden = true;
            await refreshDbInfo();
            showMeme('strategyMeme', 'success', '数据同步完成 ✨', '所有模块已解锁，开始筛选吧');
          }, '第 2/2 步：');
        } catch (e) {
          $('#bootProgressText').textContent = '启动失败：' + String(e).slice(0, 120);
          btn.disabled = false;
        }
      }, '第 1/2 步：');
    } catch (e) {
      $('#bootProgressText').textContent = '启动失败：' + String(e).slice(0, 120);
      btn.disabled = false;
    }
  });
}
function bootPoll(onDone, stagePrefix) {
  const startTs = Date.now();
  const t = setInterval(async () => {
    try {
      const p = await API.syncProgress();
      $('#bootFill').style.width = ((p.frac || 0) * 100).toFixed(1) + '%';
      $('#bootProgressText').textContent =
        (stagePrefix || '') + `${p.msg || ''} (${((p.frac || 0) * 100).toFixed(0)}%)`;
      if (p.status !== 'running') {
        clearInterval(t);
        if (p.status === 'done' || p.status === 'idle') {
          if (onDone) { onDone(); return; }
        } else {
          $('#bootProgressText').textContent = '同步失败：' + (p.msg || '未知错误');
          $('#bootHint').textContent = '可关闭页面后重启程序重试，或到「数据同步」页查看日志';
          $('#btnBootSync').disabled = false;
        }
        return;
      }
      if (Date.now() - startTs > 40 * 60 * 1000) {   // 兜底：40 分钟超时
        clearInterval(t);
        $('#bootProgressText').textContent = '同步耗时过长，可查看日志或稍后重试';
        $('#btnBootSync').disabled = false;
      }
    } catch (e) {
      console.warn(e);
      if (String(e).includes('连接已中断')) {
        clearInterval(t);   // 服务已停止，停止无意义轮询并明确告知
        $('#bootProgressText').textContent = String(e);
        $('#bootHint').textContent = '恢复服务后刷新本页面即可继续';
      }
    }
  }, 1500);
}

/* ========= GIF 悬浮窗：点击切换下一张动图，可拖动，永远置顶 ========= */
const GIF_LIST = ['bunny', 'loading', 'stuck', 'smile', 'shy', 'flag', 'pat', 'jump',
  ...Array.from({ length: 24 }, (_, i) => 'new' + String(i).padStart(2, '0'))];   // 2026-09 新增 24 张动图素材
function bindGifFloat() {
  const box = $('#gifFloat'), img = $('#gifFloatImg'),
        hide = $('#gifFloatHide'), restore = $('#gifFloatRestore');
  if (!box || !img) return;
  let idx = 0;
  const saved = localStorage.getItem('gifFloatHidden') === '1';
  if (saved) { box.style.display = 'none'; restore.hidden = false; }
  // 恢复上次拖动位置
  try {
    const pos = JSON.parse(localStorage.getItem('gifFloatPos') || 'null');
    if (pos && pos.left != null) {
      box.style.left = pos.left + 'px'; box.style.top = pos.top + 'px';
      box.style.right = 'auto'; box.style.bottom = 'auto';
    }
  } catch (e) {}
  GIF_LIST.slice(0, 4).forEach(n => { const im = new Image(); im.src = '/static/gifs/' + n + '.gif'; });  // 仅预热前 4 张，其余点击切换时按需加载（32 张全预热约 25MB）

  // —— 拖动（位移 >5px 判定为拖动，否则视为点击切换）——
  let dragging = false, moved = false, sx = 0, sy = 0, ox = 0, oy = 0;
  const DRAG_TH = 5;
  box.addEventListener('mousedown', e => {
    if (e.target === hide) return;   // ✕ 按钮不触发拖动
    const r = box.getBoundingClientRect();
    sx = e.clientX; sy = e.clientY; ox = r.left; oy = r.top;
    moved = false; dragging = true;
    e.preventDefault();
  });
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const dx = e.clientX - sx, dy = e.clientY - sy;
    if (!moved && Math.hypot(dx, dy) > DRAG_TH) moved = true;
    if (moved) {
      const w = box.offsetWidth, h = box.offsetHeight;
      const L = Math.min(Math.max(ox + dx, 0), window.innerWidth - w);
      const T = Math.min(Math.max(oy + dy, 0), window.innerHeight - h);
      box.style.left = L + 'px'; box.style.top = T + 'px';
      box.style.right = 'auto'; box.style.bottom = 'auto';
    }
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    if (moved) {
      const r = box.getBoundingClientRect();
      localStorage.setItem('gifFloatPos', JSON.stringify({ left: r.left, top: r.top }));
    } else {
      switchGif();   // 未拖动 = 点击切换下一张
    }
    moved = false;
  });
  // 触屏拖动
  box.addEventListener('touchstart', e => {
    const t = e.touches[0];
    const r = box.getBoundingClientRect();
    sx = t.clientX; sy = t.clientY; ox = r.left; oy = r.top; moved = false; dragging = true;
  }, { passive: true });
  document.addEventListener('touchmove', e => {
    if (!dragging) return;
    const t = e.touches[0];
    const dx = t.clientX - sx, dy = t.clientY - sy;
    if (!moved && Math.hypot(dx, dy) > DRAG_TH) moved = true;
    if (moved) {
      e.preventDefault();
      const w = box.offsetWidth, h = box.offsetHeight;
      box.style.left = Math.min(Math.max(ox + dx, 0), window.innerWidth - w) + 'px';
      box.style.top = Math.min(Math.max(oy + dy, 0), window.innerHeight - h) + 'px';
      box.style.right = 'auto'; box.style.bottom = 'auto';
    }
  }, { passive: false });
  document.addEventListener('touchend', () => {
    if (!dragging) return;
    dragging = false;
    if (moved) {
      const r = box.getBoundingClientRect();
      localStorage.setItem('gifFloatPos', JSON.stringify({ left: r.left, top: r.top }));
    } else switchGif();
    moved = false;
  });

  function switchGif() {
    idx = (idx + 1) % GIF_LIST.length;
    img.src = '/static/gifs/' + GIF_LIST[idx] + '.gif';   // 预热过缓存，瞬时切换且保持动画
    box.classList.remove('pop'); void box.offsetWidth; box.classList.add('pop');
  }
  hide.addEventListener('click', e => {
    e.stopPropagation();
    box.style.display = 'none'; restore.hidden = true;
    localStorage.setItem('gifFloatHidden', '1');
  });
  restore.addEventListener('click', () => {
    box.style.display = ''; restore.hidden = true;
    localStorage.setItem('gifFloatHidden', '0');
  });
}

// ========= 分红时点预测（规则 a/b/c/d） =========
const divtState = { inited: false, week: null, stats: null };

async function initDivTiming() {
  if (divtState.inited) return;
  divtState.inited = true;
  $('#btnDivTiming').addEventListener('click', () => toggleDivTiming());
  $('#divtClose').addEventListener('click', () => { $('#divtPanel').hidden = true; });
  $('#divtToday').addEventListener('click', () => { $('#divtDate').value = ''; loadDivTimingWeek(''); });
  $('#divtDate').addEventListener('change', () => loadDivTimingWeek($('#divtDate').value));
  $('#divtFull').addEventListener('click', async () => {
    const el = $('#divtFlag');
    el.style.color = 'var(--ink-soft)';
    el.textContent = '查询中…';
    try {
      const r = await API.divtimingFullMarket();
      if (r && r.ok) {
        if (r.active) { el.style.color = 'var(--good)'; el.textContent = '● 全市场窗口开启（每年2月底，同步时全市场拉取一次）'; }
        else { el.style.color = 'var(--ink-soft)'; el.textContent = r.note || '未到「每年2月底」的全市场增量窗口'; }
      } else { el.style.color = 'var(--bad)'; el.textContent = (r && r.error) || '查询失败'; }
    } catch (e) { el.style.color = 'var(--bad)'; el.textContent = '查询失败：' + e.message; }
  });
}

async function toggleDivTiming() {
  const p = $('#divtPanel');
  if (!p.hidden) { p.hidden = true; return; }
  p.hidden = false;
  await initDivTiming();
  const r = await API.divtimingFullMarket().catch(() => null);
  renderDivtFlag(r);
  await loadDivTimingStats();
  await loadDivTimingWeek($('#divtDate').value || '');
}

function renderDivtFlag(r) {
  const el = $('#divtFlag');
  if (!r || !r.ok) { el.textContent = ''; return; }
  if (r.active) { el.textContent = '● 全市场窗口开启（2月底）'; el.style.color = 'var(--good)'; }
  else { el.textContent = ''; }
}

async function loadDivTimingStats() {
  const box = $('#divtStats');
  box.innerHTML = '<span class="bili-hint">统计中…</span>';
  try {
    const r = await API.divtimingStats();
    divtState.stats = r;
    if (!r || !r.ok) { box.innerHTML = `<span class="divt-empty">${(r && r.error) || '统计失败'}</span>`; return; }
    const wk = r.weekly_estimate || {};
    const cur = String(new Date().getMonth() + 1).padStart(2, '0');
    const kinds = r.kinds || {};
    const kindTxt = Object.entries(kinds).map(([k, v]) => `${k}${v}`).join(' / ');
    box.innerHTML = [
      ['可画像股票', r.profiles ?? 0],
      ['分红习惯', kindTxt || '-'],
      ['本月预计候选', wk[cur] != null ? wk[cur] : '-'],
      ['5月', wk['05'] ?? '-'], ['6月', wk['06'] ?? '-'], ['7月', wk['07'] ?? '-'],
    ].map(([k, v]) => `<span class="divt-chip">${k} <b>${v}</b></span>`).join('');
  } catch (e) {
    box.innerHTML = `<span class="divt-empty">统计失败：${e.message}</span>`;
  }
}

async function loadDivTimingWeek(dateStr) {
  const list = $('#divtList');
  list.innerHTML = '<div class="divt-empty">计算中…</div>';
  try {
    const r = await API.divtimingWeek(dateStr);
    if (!r || !r.ok) { list.innerHTML = `<div class="divt-empty">${(r && r.error) || '计算失败'}</div>`; return; }
    divtState.week = r;
    renderDivTimingWeeks(r.weeks || [], r.week_start);
    renderDivTimingList(r.candidates || [], r.notes || []);
    $('#divtCount').textContent = (r.candidates || []).length;
  } catch (e) {
    list.innerHTML = `<div class="divt-empty">计算失败：${e.message}</div>`;
  }
}

function renderDivTimingWeeks(weeks, cur) {
  const box = $('#divtWeeks');
  if (!weeks.length) { box.innerHTML = ''; return; }
  box.innerHTML = weeks.map(w => {
    const on = w.start === cur ? ' on' : '';
    const peak = w.peak ? ' peak' : '';
    return `<button class="divt-wk${on}${peak}" data-d="${w.start}" title="该周候选 ${w.n} 只">`
         + `${w.label}<br><span style="font-size:11px;opacity:.8">${w.n} 只</span></button>`;
  }).join('');
  $$('#divtWeeks .divt-wk').forEach(b => b.addEventListener('click', () => {
    $('#divtDate').value = b.dataset.d;
    loadDivTimingWeek(b.dataset.d);
  }));
}

function renderDivTimingList(items, notes) {
  const box = $('#divtList');
  const noteHtml = notes && notes.length
    ? `<div class="divt-empty" style="padding:0 0 8px">${notes.map(escHtml).join(' · ')}</div>` : '';
  if (!items.length) {
    box.innerHTML = noteHtml + '<div class="divt-empty">本周无候选（可点「2月底全市场」查看全市场窗口）。</div>';
    return;
  }
  box.innerHTML = noteHtml + items.map(it => {
    const score = it.score != null ? Number(it.score).toFixed(0) : '-';
    const kind = it.kind ? ` <span class="dv-kind">${escHtml(it.kind)}</span>` : '';
    const last = it.last ? `上次 ${escHtml(it.last)}` : '无历史';
    return `<div class="divt-item" title="${escHtml(it.reason || '')}">
      <span class="dv-code">${escHtml(it.code)}</span>
      <span class="dv-name">${escHtml(it.name || '')}</span>
      <span class="dv-score">${score}</span>
      <span class="dv-kind">${last}${kind}</span>
    </div>`;
  }).join('');
}

// ========= 仅供学习（B站 UP主内容挖掘） =========
const biliState = { ups: [], data: null, inited: false };

async function initBili() {
  if (biliState.inited) return;
  biliState.inited = true;
  await loadBiliUps();
  await loadBiliMeta();
  await loadBiliArchive();
  if ($('#biliArchiveClear')) {
    $('#biliArchiveClear').addEventListener('click', async () => {
      if (!confirm('确定清空全部本地存档？下次抓取将重新联网拉取全部时间范围。')) return;
      showOverlay('loading', '正在清空本地存档…');
      try {
        const r = await API.biliArchiveClear({});
        hideOverlay();
        showBiliNote(['已清空 ' + (r.removed || 0) + ' 个 UP主 的本地存档（下次将全量重抓）']);
        await loadBiliArchive();
      } catch (e) { hideOverlay(); showBiliNote([String(e)]); }
    });
  }
  $('#biliAddUp').addEventListener('click', addBiliUp);
  $('#biliUid').addEventListener('keydown', e => { if (e.key === 'Enter') addBiliUp(); });
  $('#biliRun').addEventListener('click', runBiliScan);
  $('#biliSummary').addEventListener('click', runBiliSummary);
  if ($('#biliAsrHelp')) {
    $('#biliAsrHelp').addEventListener('click', async () => {
      const box = $('#biliAsrHelpBox');
      if (!box.hidden) { box.hidden = true; return; }
      try {
        const h = await API.biliAsrHelp();
        const opts = (h.options || []).map(e =>
          `【${e.name}】\n  安装：${e.install || ''}\n  ${e.why || ''}\n  ${e.extra || ''}`).join('\n\n');
        box.textContent = (h.note ? h.note + '\n\n' : '')
          + (h.ready ? '' : '') + opts
          + (h.privacy ? `\n\n🔒 ${h.privacy}` : '')
          + (h.note_extra ? `\n🛡 ${h.note_extra}` : '');
        box.hidden = false;
      } catch (e) { showBiliNote(['获取安装指引失败：' + e.message]); }
    });
  }
  $('#biliSessionReset').addEventListener('click', async () => {
    showOverlay('loading', '正在重建 B站 会话…');
    try {
      const r = await API.biliSessionReset();
      hideOverlay();
      showBiliNote(['会话已重建：' + (r.has_session ? '已就绪' : '失败')]);
      await loadBiliMeta();
    } catch (e) { hideOverlay(); showBiliNote([String(e)]); }
  });
  $('#biliCacheClear').addEventListener('click', async () => {
    showOverlay('loading', '正在清空缓存…');
    try {
      const r = await API.biliCacheClear();
      hideOverlay();
      showBiliNote(['已清空 ' + (r.removed || 0) + ' 个缓存文件（下次抓取会重新联网）']);
      await loadBiliMeta();
    } catch (e) { hideOverlay(); showBiliNote([String(e)]); }
  });
  $('#biliCredSave').addEventListener('click', async () => {
    const v = $('#biliSessdata').value.trim();
    if (!v) { showBiliNote(['请先粘贴 SESSDATA']); return; }
    showOverlay('loading', '正在保存凭据并重建会话…');
    try {
      const r = await API.biliCredSet({ sessdata: v });
      hideOverlay();
      $('#biliSessdata').value = '';
      showBiliNote([r.msg || '已保存', '提示：字幕能否取到取决于该账号对目标视频的可见性。']);
      await loadBiliMeta();
    } catch (e) { hideOverlay(); showBiliNote([String(e)]); }
  });
  $('#biliCredClear').addEventListener('click', async () => {
    showOverlay('loading', '正在清除凭据…');
    try {
      const r = await API.biliCredClear();
      hideOverlay();
      showBiliNote([r.msg || '已清除']);
      await loadBiliMeta();
    } catch (e) { hideOverlay(); showBiliNote([String(e)]); }
  });
}

async function loadBiliMeta() {
  try {
    const m = await API.biliMeta();
    const d = m.defaults || {};
    const setIf = (id, v) => {
      const el = $(id);
      if (el && v !== undefined && v !== null) el.value = v;
    };
    setIf('#biliInterval', d.min_interval);
    setIf('#biliMaxReq', d.max_requests);
    setIf('#biliVidPages', d.video_pages);
    setIf('#biliMaxVideos', d.max_videos);
    setIf('#biliDynPages', d.dyn_pages);
    setIf('#biliMaxDyns', d.max_dyns);
    setIf('#biliCHot', d.comment_pages_hot);
    setIf('#biliCNew', d.comment_pages_new);
    setIf('#biliSubPages', d.sub_reply_pages);
    setIf('#biliCtxLen', d.ctx_min_len);
    if ($('#biliWithSub')) $('#biliWithSub').checked = !!d.with_subtitle;
    if ($('#biliWithDyn')) $('#biliWithDyn').checked = !!d.with_dynamic;
    if ($('#biliLocalVideo')) $('#biliLocalVideo').checked = !!d.video_local;
    setIf('#biliAsrMax', d.video_local_max);
    if ($('#biliAsrDir')) $('#biliAsrDir').value = d.video_local_dir || '';
    // 本地转写引擎状态（faster-whisper / funasr）
    const lv = m.local_video || {};
    const st = $('#biliAsrState');
    if (st) {
      if (lv.engine) {
        st.textContent = '✅ 可用引擎：' + lv.engine + (lv.note ? '（' + lv.note + '）' : '');
        st.style.color = 'var(--good)';
      } else {
        st.textContent = '⚠ 未检测到离线转写引擎（点右侧「查看引擎安装指引」）';
        st.style.color = 'var(--bad)';
      }
    }
    const c = m.cred || {};
    $('#biliCredState').textContent = c.has_sessdata
      ? ('已设置（' + (c.sessdata_masked || '') + '，' + (c.saved_at || '') + '）')
      : '未设置（视频字幕将无法读取）';
    const s = m.cache || {};
    $('#biliSessState').textContent =
      '会话：' + (m.has_session ? ('已就绪（' + m.age_min + ' 分钟前）') : '未建立')
      + ' · 本进程请求 ' + (m.requests_this_process || 0) + ' 次'
      + ' · 缓存 ' + (s.files || 0) + ' 个文件';
  } catch (e) { /* 元信息失败不影响主流程 */ }
}

async function loadBiliUps() {
  try {
    const r = await API.biliUps();
    biliState.ups = r.ups || [];
    renderBiliUps();
  } catch (e) {
    $('#biliUps').innerHTML = '<span class="bili-hint">读取失败：' + escHtml(String(e)) + '</span>';
  }
}

async function loadBiliArchive() {
  const box = $('#biliArchive');
  if (!box) return;
  try {
    const r = await API.biliArchive();
    renderBiliArchive(r);
  } catch (e) {
    box.innerHTML = '<span class="bili-hint">读取失败：' + escHtml(String(e)) + '</span>';
  }
}

function renderBiliArchive(a) {
  const box = $('#biliArchive');
  if (!box) return;
  const ups = (a && a.ups) || [];
  const hint = $('#biliArchiveHint');
  if (hint) {
    hint.textContent = ups.length
      ? ('共 ' + ups.length + ' 个 UP主 · 提及 ' + (a.total_mentions || 0)
         + ' 条 · 占用 ' + (a.mb || 0) + ' MB')
      : '暂无本地存档（首次抓取后自动生成）';
  }
  if (!ups.length) {
    box.innerHTML = '<span class="bili-hint">还没有本地存档；抓取一次后，这里会显示每个 UP主 的覆盖区间。</span>';
    return;
  }
  box.innerHTML = ups.map(u => {
    const v = u.video || {}, d = u.dynamic || {};
    return '<div class="bili-arch-row" data-uid="' + escHtml(u.uid) + '">'
      + '<span class="bili-arch-name">' + escHtml(u.name || ('UID' + u.uid)) + '</span>'
      + '<span class="bili-arch-cov">视频 ' + escHtml(v.covered ? (v.from + '~' + v.to) : '—')
      + ' ｜ 动态 ' + escHtml(d.covered ? (d.from + '~' + d.to) : '—') + '</span>'
      + '<span class="bili-arch-meta">提及 ' + (u.mentions || 0) + ' · 单元 ' + (u.units || 0)
      + (u.updated_at ? ' · ' + escHtml(u.updated_at) : '') + '</span>'
      + '<button type="button" class="btn" data-archdel="' + escHtml(u.uid) + '">清除</button>'
      + '</div>';
  }).join('');
  $$('#biliArchive button[data-archdel]').forEach(b => b.addEventListener('click', async () => {
    const uid = b.dataset.archdel;
    if (!confirm('清除该 UP主 的本地存档？下次抓取将重新联网拉取。')) return;
    showOverlay('loading', '正在清除…');
    try {
      await API.biliArchiveClear({ uid });
      hideOverlay();
      await loadBiliArchive();
    } catch (e) { hideOverlay(); showBiliNote([String(e)]); }
  }));
}

function renderBiliUps() {
  const box = $('#biliUps');
  if (!biliState.ups.length) {
    box.innerHTML = '<span class="bili-hint">还没有保存过 UP主。填入 UID（UP主 主页 '
      + 'space.bilibili.com/ 后面那串数字）后点「保存该 UP主」；保存后点名称旁的 ✎ 可自定义名称。</span>';
    return;
  }
  box.innerHTML = biliState.ups.map(u => {
    const dn = u.alias || u.name || ('UID' + u.uid);
    return '<label class="bili-up on" data-uid="' + escHtml(u.uid) + '">'
      + '<input type="checkbox" checked />'
      + (u.face ? '<img src="' + escHtml(u.face) + '" alt="" referrerpolicy="no-referrer" />' : '')
      + '<span class="bili-up-name' + (u.alias ? ' custom' : '') + '" title="'
      + (u.alias ? '自定义名称' : '自动读取的昵称') + '">' + escHtml(dn) + '</span>'
      + '<span class="uid">' + escHtml(u.uid) + '</span>'
      + '<button type="button" class="up-edit" title="自定义名称（留空=恢复自动昵称）"'
      + ' data-rename="' + escHtml(u.uid) + '" data-alias="' + escHtml(u.alias || '') + '">✎</button>'
      + '<button type="button" title="删除该 UP主" data-del="' + escHtml(u.uid) + '">✕</button>'
      + '</label>';
  }).join('');
  $$('#biliUps .bili-up').forEach(el => {
    const cb = el.querySelector('input');
    cb.addEventListener('change', () => el.classList.toggle('on', cb.checked));
  });
  $$('#biliUps button[data-del]').forEach(b => b.addEventListener('click', async e => {
    e.preventDefault();
    e.stopPropagation();
    showOverlay('loading', '正在删除…');
    try {
      const r = await API.biliUpRemove({ uid: b.dataset.del });
      biliState.ups = r.ups || [];
      renderBiliUps();
    } catch (err) { showBiliNote([String(err)]); }
    hideOverlay();
  }));
  $$('#biliUps button[data-rename]').forEach(b => b.addEventListener('click', e => {
    e.preventDefault();
    e.stopPropagation();
    const label = b.closest('.bili-up');
    if (!label || label.querySelector('.bili-name-edit')) return;
    const span = label.querySelector('.bili-up-name');
    const uid = b.dataset.rename;
    const cur = b.dataset.alias || '';
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'bili-name-edit';
    inp.value = cur;
    inp.placeholder = '自定义名称（留空=自动）';
    inp.maxLength = 20;
    span.replaceWith(inp);
    inp.focus();
    inp.select();
    // 文本框是「可交互内容」，label 不会把点击转发到勾选框，无需拦截
    let done = false;
    const finish = async save => {
      if (done) return;
      done = true;
      if (!save) { renderBiliUps(); return; }
      const name = inp.value.trim();
      if (name === cur) { renderBiliUps(); return; }
      showOverlay('loading', '正在保存名称…');
      try {
        const r = await API.biliUpRename({ uid, name });
        hideOverlay();
        if (!r.ok) { showBiliNote([r.msg || '保存失败']); return; }
        biliState.ups = r.ups || [];
        renderBiliUps();
        showBiliNote([r.msg || '已保存']);
      } catch (err) { hideOverlay(); showBiliNote([String(err)]); }
    };
    inp.addEventListener('keydown', ev => {
      if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
      else if (ev.key === 'Escape') { ev.preventDefault(); finish(false); }
    });
    inp.addEventListener('blur', () => finish(true));
  }));
}

function biliSelectedUids() {
  return $$('#biliUps .bili-up')
    .filter(el => el.querySelector('input').checked)
    .map(el => el.dataset.uid);
}

async function addBiliUp() {
  const uid = $('#biliUid').value.trim();
  if (!/^\d{1,12}$/.test(uid)) {
    showBiliNote(['UID 必须是纯数字（UP主 主页 URL 里 /space/ 之后的那串数字）']);
    return;
  }
  showOverlay('loading', '正在保存 UP主 ' + uid + '（会尝试读取昵称）…');
  try {
    const r = await API.biliUpAdd({ uid });
    hideOverlay();
    if (!r.ok) { showBiliNote([r.msg || '保存失败']); return; }
    biliState.ups = r.ups || [];
    renderBiliUps();
    $('#biliUid').value = '';
    const up = r.up || {};
    showBiliNote([(r.msg || '已保存') + '：' + (up.alias || up.name || uid)
      + '（点名称旁的 ✎ 可自定义名称）']);
  } catch (e) { hideOverlay(); showBiliNote([String(e)]); }
}

function biliCfg() {
  const num = (id, dv) => {
    const el = $(id);
    const v = el ? parseFloat(el.value) : NaN;
    return isFinite(v) ? v : dv;
  };
  return {
    days: num('#biliDays', 30),
    use_local: $('#biliUseLocal') ? $('#biliUseLocal').checked : true,
    min_interval: num('#biliInterval', 4.5),
    max_requests: num('#biliMaxReq', 260),
    video_pages: num('#biliVidPages', 2),
    max_videos: num('#biliMaxVideos', 40),
    dyn_pages: num('#biliDynPages', 3),
    max_dyns: num('#biliMaxDyns', 60),
    comment_pages_hot: num('#biliCHot', 2),
    comment_pages_new: num('#biliCNew', 1),
    sub_reply_pages: num('#biliSubPages', 1),
    ctx_min_len: num('#biliCtxLen', 3),
    with_subtitle: $('#biliWithSub') ? $('#biliWithSub').checked : true,
    with_dynamic: $('#biliWithDyn') ? $('#biliWithDyn').checked : true,
    video_local: $('#biliLocalVideo') ? $('#biliLocalVideo').checked : false,
    video_local_max: num('#biliAsrMax', 5),
    video_local_dir: $('#biliAsrDir') ? ($('#biliAsrDir').value || '').trim() : '',
  };
}

async function runBiliScan() {
  const uids = biliSelectedUids();
  if (!uids.length) { showBiliNote(['请先保存并勾选至少一个 UP主']); return; }
  const cfg = biliCfg();
  if (cfg.min_interval < 3) {
    showBiliNote(['为避免触发风控，请求间隔不建议低于 3 秒']);
    return;
  }
  $('#biliSummaryBox').hidden = true;
  $('#biliNotes').innerHTML = '';
  showOverlay('loading',
    '正在抓取（串行限速 ' + cfg.min_interval + 's/次，绝不并发）…最多约 '
    + (uids.length * (cfg.max_videos + cfg.max_dyns))
    + ' 条内容，可能需要数分钟到数十分钟；中途可点「停止」安全中断。',
    { cancellable: true });
  try {
    const data = await API.biliScan({ uids, cfg });
    hideOverlay();
    if (data.error) { showBiliNote([data.error]); return; }
    if (data.cancelled) { showBiliNote(['已取消（已抓到的部分已进缓存）']); return; }
    biliState.data = data;
    renderBiliResult(data);
    await loadBiliMeta();
    await loadBiliArchive();
  } catch (e) {
    hideOverlay();
    showBiliNote([_userCancelled ? '已取消' : String(e)]);
  }
}

function showBiliNote(lines) {
  $('#biliNotes').innerHTML = (lines || [])
    .map(t => '<div>' + escHtml(t) + '</div>').join('');
}

function renderBiliResult(data) {
  const s = data.stats || {};
  $('#biliStats').hidden = false;
  $('#biliStats').innerHTML =
    '<b>本次抓取</b>：UP主 ' + (s.ups || 0) + ' 个 · 近 <b>' + s.days + '</b> 天（'
    + escHtml(String(s.win_start || '')) + ' ~ ' + escHtml(String(s.win_end || '')) + '）'
    + ' · 视频 <b>' + (s.videos || 0) + '</b> 条 · 动态 <b>' + (s.dynamics || 0) + '</b> 条'
    + ' · 评论 <b>' + (s.comments_scanned || 0) + '</b> 条（UP主 自己 '
    + (s.up_comments || 0) + ' 条）'
    + '<br>命中 <b>' + (s.mentions || 0) + '</b> 处提及，覆盖 <b>' + (s.stocks || 0)
    + '</b> 只个股 · 共发起 <b>' + (s.requests || 0) + '</b>/' + (s.request_cap || 0)
    + ' 次请求 · 耗时 ' + (s.elapsed || 0) + 's'
    + (s.use_local
        ? '<br>🗃 本地存档：复用 <b>' + (s.local_mentions || 0) + '</b> 处提及 · 纯本地 UP主 <b>'
          + (s.local_ups || 0) + '</b> 个（0 网络）· 本轮实际抓取单元 <b>' + (s.fetched_units || 0) + '</b> 条'
        : '');
  const notes = (data.notes || []).slice();
  if (!s.has_sessdata) {
    notes.push('未设置 SESSDATA：B站 已对匿名关闭视频字幕接口，视频正文里的提及读不到；'
      + '可在「高级设置 → 可选登录凭据」填入自己的 SESSDATA（仅存本地）。');
  }
  showBiliNote(notes);
  renderBiliStocks(data.stocks || []);
}

function hlSnip(snip, code, name) {
  const esc = escHtml(snip);
  const cands = [name, code];
  for (let i = 0; i < cands.length; i++) {
    const t = cands[i];
    if (!t) continue;
    const j = esc.indexOf(t);
    if (j >= 0) {
      return esc.slice(0, j) + '<mark>' + t + '</mark>' + esc.slice(j + t.length);
    }
  }
  return esc;
}

function renderBiliStocks(stocks) {
  const box = $('#biliStocks');
  if (!stocks.length) {
    box.innerHTML = '<span class="bili-hint">本次范围内没有提取到股票提及。'
      + '可放宽时间范围、或确认该 UP主 是否会提到具体个股（含代码或简称）。</span>';
    return;
  }
  box.innerHTML = stocks.map((g, i) => {
    const cls = g.score >= 3 ? '' : ' low';
    const mts = (g.mentions || []).map(m =>
      '<li>'
      + '<span class="bili-mt-time">' + escHtml(m.time || '时间未知') + '</span>'
      + '<span class="bili-mt-src">' + escHtml(m.source_label || m.source || '') + '</span>'
      + '<span class="bili-mt-who' + (m.is_up ? ' up' : '') + '">'
      + escHtml(m.is_up ? 'UP主本人' : (m.author || '网友')) + '</span>'
      + (m.url
        ? '<a class="bili-mt-title" href="' + escHtml(m.url) + '" target="_blank" rel="noopener">'
          + escHtml((m.title || '').slice(0, 40) || '查看原处') + '</a>'
        : escHtml((m.title || '').slice(0, 40)))
      + (m.via ? '<span class="bili-hint">（' + escHtml(m.via) + '）</span>' : '')
      + '<div class="bili-mt-snip">' + hlSnip(m.snippet || '', g.code, g.name) + '</div>'
      + '</li>').join('');
    return '<div class="bili-stock" data-code="' + escHtml(g.code) + '">'
      + '<div class="bili-stock-head">'
      + '<span class="bili-idx">' + (i + 1) + '</span>'
      + '<span class="bili-code">' + escHtml(g.code) + '</span>'
      + '<span class="bili-name"><a href="https://stockpage.10jqka.com.cn/'
      + escHtml(g.code) + '/" target="_blank" rel="noopener">' + escHtml(g.name) + '</a></span>'
      + '<span class="bili-score' + cls + '">权重 ' + g.score + '</span>'
      + '<span class="bili-meta">UP主 ' + g.up_mentions + ' 处 / 其它用户 ' + g.other_mentions
      + ' 处 · 共 ' + g.total + ' 处 · 最近 ' + escHtml(g.last_time || '未知')
      + ' · 来源：' + escHtml((g.sources || []).join('、')) + '</span>'
      + '<button type="button" class="bili-expand">▸ 展开</button>'
      + '</div>'
      + '<ul class="bili-mentions" hidden>' + mts + '</ul>'
      + '</div>';
  }).join('');
  $$('#biliStocks .bili-expand').forEach(btn => {
    btn.addEventListener('click', () => {
      const ul = btn.closest('.bili-stock').querySelector('.bili-mentions');
      const open = !ul.hidden;
      ul.hidden = open;
      btn.textContent = open ? '▸ 展开' : '▾ 收起';
    });
  });
}

async function runBiliSummary() {
  if (!biliState.data) { showBiliNote(['请先执行一次抓取，再点一键总结']); return; }
  showOverlay('loading', '正在汇总分析（本地计算，不联网）…');
  try {
    const r = await API.biliSummary({ uids: [], cfg: {} });
    hideOverlay();
    if (r.error) { showBiliNote([r.error]); return; }
    $('#biliSummaryBox').hidden = false;
    const lines = (r.lines || []).map(t => {
      const i = t.indexOf('】');
      return '<p>' + (i > 0
        ? '<span class="tag">' + escHtml(t.slice(0, i + 1)) + '</span>' + escHtml(t.slice(i + 1))
        : escHtml(t)) + '</p>';
    }).join('');
    $('#biliSummaryBody').innerHTML = lines
      + (r.scanned_at ? '<p class="bili-hint">基于 ' + escHtml(r.scanned_at) + ' 的抓取结果</p>' : '');
    $('#biliSummaryBox').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  } catch (e) { hideOverlay(); showBiliNote([String(e)]); }
}

document.addEventListener('DOMContentLoaded', init);
