/* 국장 매매 타이밍 — 앱 로직
 * 데이터: data/signals.json (engine/analyze.py 생성), data/catalog.json, data/backtest.json
 */
'use strict';

/* ------------------------------------------------------------------ 유틸 */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const LS = {
  holdings: 'gj.holdings.v1',
  prefs: 'gj.prefs.v1',
  snaps: 'gj.snapshots.v1',
};

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];

const nf = new Intl.NumberFormat('ko-KR');
const fmt = (n) => nf.format(Math.round(Number(n) || 0));
const fmtPct = (v, digits = 2) => `${Number(v) > 0 ? '+' : ''}${Number(v || 0).toFixed(digits)}%`;
const fmtPrice = (n) => `${fmt(n)}원`;
const fmtDateLabel = (iso) => {
  if (!iso) return '';
  const d = new Date(`${iso}T00:00:00+09:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return `${iso} (${WEEKDAYS[d.getDay()]})`;
};
const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

const signalClass = (signal) => ({ BUY: 'buy', WATCH: 'watch', SELL: 'sell' }[signal] || 'na');
const pnlClass = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : 'flat');

/* 지수 5일선 상태 — analyze.py index_summary() 의 allow_new_buy */
function marketInfo(market) {
  const idx = state.data?.index || {};
  return idx[String(market || '').toLowerCase()] || null;
}
function marketAllows(market) {
  const info = marketInfo(market);
  return !info || info.allow_new_buy !== false;
}
const marketName = (key) => (key === 'kospi' ? '코스피' : key === 'kosdaq' ? '코스닥' : key);

/* ----------------------------------------------------------------- state */
const state = {
  data: null,
  catalog: [],
  stockMap: new Map(),
  holdings: [],
  records: [],
  backtest: null,
  prefs: { budget: 1000000, feeMode: 'benefit', budgetByCode: {} },
  tab: 'picks',
  editingCode: null,
  sheetCode: null,
};

function loadPrefs() {
  try {
    const raw = JSON.parse(localStorage.getItem(LS.prefs) || 'null');
    if (raw && typeof raw === 'object') {
      state.prefs = {
        budget: Number(raw.budget) > 0 ? Number(raw.budget) : 1000000,
        feeMode: raw.feeMode === 'standard' ? 'standard' : 'benefit',
        budgetByCode: raw.budgetByCode && typeof raw.budgetByCode === 'object' ? raw.budgetByCode : {},
      };
    }
  } catch (e) { /* 저장값이 깨져도 기본값으로 시작 */ }
}
function savePrefs() {
  try { localStorage.setItem(LS.prefs, JSON.stringify(state.prefs)); } catch (e) { /* 용량 초과 등 무시 */ }
}
function loadHoldings() {
  try {
    const raw = localStorage.getItem(LS.holdings);
    if (raw === null) return null;
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : null;
  } catch (e) { return null; }
}
function saveHoldings() {
  try { localStorage.setItem(LS.holdings, JSON.stringify(state.holdings)); } catch (e) { /* 무시 */ }
}
function loadRecords() {
  try {
    const arr = JSON.parse(localStorage.getItem(LS.snaps) || '[]');
    return Array.isArray(arr) ? arr : [];
  } catch (e) { return []; }
}
function saveRecords() {
  try { localStorage.setItem(LS.snaps, JSON.stringify(state.records.slice(-30))); } catch (e) { /* 무시 */ }
}

/* ------------------------------------------------- URL 해시로 종목 가져오기
 * #import=<base64(UTF-8 JSON)> — URL 해시는 HTTP 요청으로 서버에 전송되지 않으므로
 * 보유 종목을 서버·저장소에 두지 않고 기기 브라우저 localStorage로만 옮길 수 있다.
 * 받는 형식: [{"code":"005930","name":"삼성전자","qty":10,"avgPrice":70000}, ...]
 */
const IMPORT_PREFIX = '#import=';

function decodeBase64Utf8(b64) {
  const binary = atob(b64.replace(/-/g, '+').replace(/_/g, '/'));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new TextDecoder('utf-8').decode(bytes);
}

function normalizeImportedHoldings(raw) {
  const list = Array.isArray(raw) ? raw : (raw && Array.isArray(raw.holdings) ? raw.holdings : null);
  if (!list) throw new Error('holdings 배열이 없습니다');
  const out = [];
  for (const h of list) {
    if (!h || typeof h !== 'object') continue;
    const code = String(h.code ?? '').trim().toUpperCase();
    if (!/^[0-9A-Z]{6}$/.test(code)) continue;
    const qty = Math.round(Number(h.qty) || 0);
    const avg = Math.round(Number(h.avgPrice ?? h.avg_price) || 0);
    if (qty < 1 || avg < 1) continue;
    out.push({ code, name: String(h.name || code), qty, avg_price: avg });
  }
  return out;
}

function clearImportHash() {
  try { history.replaceState(null, '', location.pathname + location.search); } catch (e) { /* 무시 */ }
}

function applyImportHash() {
  const hash = location.hash || '';
  const at = hash.indexOf(IMPORT_PREFIX);
  if (at < 0) return false;
  const encoded = hash.slice(at + IMPORT_PREFIX.length).split('&')[0];
  let list;
  try {
    list = normalizeImportedHoldings(JSON.parse(decodeBase64Utf8(encoded)));
    if (!list.length) throw new Error('가져올 종목이 없습니다');
  } catch (err) {
    console.warn('보유 종목 가져오기 실패:', err);
    clearImportHash();
    toast('보유 종목을 불러오지 못했어요. 링크를 다시 확인해 주세요');
    return false;
  }
  state.holdings = list;
  saveHoldings();
  clearImportHash();
  toast(`보유 종목 ${list.length}종목 불러오기 완료`);
  return true;
}

/* ------------------------------------------------------------------ 통신 */
async function fetchJSON(path) {
  const res = await fetch(`${path}${path.includes('?') ? '&' : '?'}t=${Date.now()}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${path} ${res.status}`);
  return res.json();
}

/* ------------------------------------------------------------------ 부팅 */
async function boot() {
  loadPrefs();
  state.records = loadRecords();
  bindStaticEvents();
  renderAdapterCode();
  const imported = applyImportHash();

  try {
    state.data = await fetchJSON('data/signals.json');
  } catch (err) {
    showLoadError(err);
    return;
  }

  // 카탈로그·백테스트는 부가 정보 — 실패해도 화면은 뜬다
  const [catalog, backtest] = await Promise.all([
    fetchJSON('data/catalog.json').catch(() => null),
    fetchJSON('data/backtest.json').catch(() => null),
  ]);
  state.catalog = (catalog && catalog.stocks) || [];
  state.backtest = backtest;

  buildStockMap();
  seedHoldingsIfNeeded();
  seedRecord();
  renderAll();
  registerServiceWorker();

  if (imported) switchTab('holdings');
  else {
    const tab = new URLSearchParams(location.search).get('tab');
    if (tab && ['picks', 'holdings', 'rules'].includes(tab)) switchTab(tab);
  }

  // 앱이 이미 열린 상태에서 가져오기 링크를 붙여넣은 경우
  window.addEventListener('hashchange', () => {
    if (!applyImportHash()) return;
    seedHoldingsIfNeeded();
    renderAll();
    switchTab('holdings');
  });
}

function buildStockMap() {
  const map = new Map();
  for (const s of state.catalog) {
    map.set(s.code, {
      code: s.code, name: s.name, market: s.market,
      close: s.close, change_pct: s.change_pct, signal: null,
      target: null, stop: null, entry: null, score: null, rsi14: null,
      vol_ratio: null, analyzed: false,
    });
  }
  for (const s of state.data.stocks) {
    map.set(s.code, { ...map.get(s.code), ...s, analyzed: true });
  }
  state.stockMap = map;
}

/* 개인 정리·손절 기준가 — 비어있으면 키를 만들지 않아 기존 저장 데이터와 그대로 호환된다 */
function normalizeExit(raw) {
  const out = {};
  const target = Math.round(Number(raw.exitTarget ?? raw.exit_target) || 0);
  const stop = Math.round(Number(raw.exitStop ?? raw.exit_stop) || 0);
  if (target > 0) out.exitTarget = target;
  if (stop > 0) out.exitStop = stop;
  return out;
}

function seedHoldingsIfNeeded() {
  const stored = loadHoldings();
  if (stored !== null) {
    state.holdings = stored.filter((h) => h && h.code).map((h) => ({
      code: String(h.code),
      name: h.name || (state.stockMap.get(String(h.code))?.name ?? String(h.code)),
      qty: Math.max(1, Math.round(Number(h.qty) || 0)),
      avg_price: Math.max(1, Math.round(Number(h.avg_price) || 0)),
      ...normalizeExit(h),
    }));
    return;
  }
  const seed = state.data.holdings_seed;
  const list = seed && Array.isArray(seed.holdings) ? seed.holdings : [];
  state.holdings = list.map((h) => ({
    code: String(h.code),
    name: h.name,
    qty: Math.max(1, Math.round(Number(h.qty) || 1)),
    avg_price: Math.max(1, Math.round(Number(h.avg_price) || 1)),
    ...normalizeExit(h),
  }));
  saveHoldings();
}

function seedRecord() {
  const marketDate = state.data.market_date;
  const picks = state.data.picks.map((p) => ({
    code: p.code, name: p.name, signal: p.signal, score: p.score, close: p.close,
  }));
  const exist = state.records.findIndex((r) => r.date === marketDate);
  const rec = { date: marketDate, picks, savedAt: new Date().toISOString() };
  if (exist >= 0) state.records[exist] = rec;
  else state.records.push(rec);
  saveRecords();
}

/* --------------------------------------------------------------- 렌더링 */
function renderAll() {
  renderHeader();
  renderMarketCard();
  renderChips();
  renderPicks();
  renderRebound();
  renderAllList();
  renderHoldings();
  renderRecords();
  renderBacktest();
  renderDataNotes();
  renderDatalist();
}

function renderHeader() {
  const updated = new Date(state.data.generated_at);
  const hh = String(updated.getHours()).padStart(2, '0');
  const mm = String(updated.getMinutes()).padStart(2, '0');
  $('#marketDate').textContent = `기준일 ${fmtDateLabel(state.data.market_date)} · ${hh}:${mm} 갱신`;
}

function renderMarketCard() {
  const idx = state.data.index || {};
  const kospi = idx.kospi;
  const kosdaq = idx.kosdaq;
  const sum = state.data.summary;
  const box = $('#marketCard');
  if (!kospi && !kosdaq) { box.textContent = '시장 지수 정보를 불러오지 못했어요.'; return; }

  const row = (label, d) => {
    if (!d) return '';
    const cls = d.change_pct > 0 ? 'up' : d.change_pct < 0 ? 'down' : 'flat';
    const hasMa5 = d.ma5 !== undefined && d.ma5 !== null;
    const allowed = d.allow_new_buy !== false;
    const ma5Line = hasMa5
      ? `<div class="mk-ma5">
          <span>5일선 ${fmt(d.ma5)} ${allowed ? '위' : '아래'} (${fmtPct(d.above_ma5_pct, 2)})</span>
          <span class="mk-badge ${allowed ? 'ok' : 'hold'}">${allowed ? '신규 매수 가능' : '신규 매수 보류'}</span>
        </div>`
      : '';
    return `<div class="mk-top">
      <span class="mk-label">${label}</span>
      <div class="mk-index"><b>${fmt(d.level)}</b><span class="chg ${cls}">${fmtPct(d.change_pct)}</span></div>
      <span class="mk-phase ${String(d.phase).includes('상승') || String(d.phase).includes('반등') ? 'up' : 'down'}">${escapeHtml(d.phase)}</span>
    </div>${ma5Line}`;
  };

  const k = kospi || kosdaq;
  const blocked = ['kospi', 'kosdaq'].filter((key) => idx[key] && idx[key].allow_new_buy === false);
  const below = k.level < k.ma20;
  let note;
  if (blocked.length) {
    note = `${blocked.map(marketName).join('·')}가 5일선 아래예요. 이 시장 종목은 매수 조건을 채워도 <b>신규 매수 보류</b>로 내려 관망만 합니다. 새로 사기보다 보유 종목의 목표·손절 관리에 집중하는 구간이에요.`;
  } else if (below) {
    note = `지수가 20일선(${fmt(k.ma20)}) 아래예요. 이런 구간에서는 추천 종목도 계획 금액의 절반만, 손절선은 짧게 가져가세요.`;
  } else {
    note = `지수가 5일선(${fmt(k.ma5)}) 위에서 버티고 있어요. 매수 신호 종목은 분할로 접근해도 좋습니다.`;
  }

  const blockedCount = state.data.summary.blocked_buy || 0;
  const blockedLine = blockedCount
    ? `<p class="mk-sub">오늘 매수 조건을 채웠지만 5일선 아래라 보류된 종목 <b>${blockedCount}개</b></p>`
    : '';

  box.innerHTML = `
    ${row('코스피', kospi)}
    ${kosdaq ? `<div style="height:10px"></div>${row('코스닥', kosdaq)}` : ''}
    <p class="mk-sub">분석 ${sum.analyzed}종목 · 평균 점수 ${sum.avg_score}점 · 오늘 평균 등락 ${fmtPct(sum.avg_change_pct)}</p>
    ${blockedLine}
    <p class="mk-note">${note}</p>`;
}

function renderChips() {
  const s = state.data.summary;
  $('#summaryChips').innerHTML = `
    <div class="chip"><span class="dot buy"></span>매수 <b>${s.buy}</b></div>
    <div class="chip"><span class="dot watch"></span>관망 <b>${s.watch}</b></div>
    <div class="chip"><span class="dot sell"></span>주의 <b>${s.sell}</b></div>`;
}

function pickCard(s) {
  const cls = signalClass(s.signal);
  const chg = s.change_pct > 0 ? 'up' : s.change_pct < 0 ? 'down' : 'flat';
  const blocked = marketAllows(s.market) === false;
  const blockedFlag = blocked
    ? '<div class="pick-flag hold">신규 매수 보류 — ' + escapeHtml(s.market) + ' 지수 5일선 아래</div>'
    : '';
  const atr = s.atr_pct
    ? `<p class="pick-vol">14일 변동성(ATR) <b>${s.atr_pct}%</b> 기준 목표·손절 · +3% 도달 시 본전 손절</p>`
    : '';
  return `
  <article class="pick-card ${cls}" data-code="${s.code}" role="button" tabindex="0" aria-label="${escapeHtml(s.name)} 주문 계산 열기">
    <div class="pick-head">
      <div>
        <div class="pick-name">${escapeHtml(s.name)}</div>
        <div class="pick-meta">${s.code} · ${s.market}${s.sector ? ` · ${escapeHtml(s.sector)}` : ''}</div>
      </div>
      <span class="pill ${cls}">${s.signal_label}</span>
    </div>
    ${blockedFlag}
    <div class="pick-price">
      <b>${fmt(s.close)}</b><span class="won">원</span>
      <span class="chg ${chg}">${fmtPct(s.change_pct)}</span>
      <div class="score-wrap">
        <div class="score-bar"><i style="width:${s.score}%; background:${s.signal === 'BUY' ? 'var(--green)' : s.signal === 'WATCH' ? 'var(--amber)' : 'var(--red)'}"></i></div>
        <span class="score-num"><b>${s.score}</b>점</span>
      </div>
    </div>
    <div class="price-grid">
      <div><span>진입가</span><b>${fmt(s.entry)}</b></div>
      <div class="t"><span>목표 ${fmtPct(s.target_pct, 1)}</span><b>${fmt(s.target)}</b></div>
      <div class="s"><span>손절 ${fmtPct(s.stop_pct, 1)}</span><b>${fmt(s.stop)}</b></div>
    </div>
    ${atr}
    <p class="reason">${escapeHtml(s.reason)}</p>
    <button class="pick-cta" type="button">주문 계산 · mPOP 가이드</button>
  </article>`;
}

function renderPicks() {
  const picks = state.data.picks || [];
  const head = $('#pickHead');
  if (head) {
    const blockedCount = picks.filter((s) => marketAllows(s.market) === false).length;
    head.textContent = blockedCount
      ? `신규 매수 보류 ${blockedCount}종목 · 점수 참고용`
      : '매수 적합도 점수 순';
  }
  $('#pickList').innerHTML = picks.length
    ? picks.map(pickCard).join('')
    : '<div class="card placeholder">오늘은 매수 신호가 없어요. 관망 구간입니다.</div>';
  $$('#pickList [data-code]').forEach((el) => {
    el.addEventListener('click', (e) => {
      if (e.target.closest('button, a')) e.preventDefault();
      openSheet(el.dataset.code);
    });
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openSheet(el.dataset.code); }
    });
  });
}

/* ------------------------------------------- 과매도 반등 관찰 (참고 전용)
 * engine/indicators.py 의 rebound_watch — 5일 -8% 이하 + RSI 30 미만.
 * 매수 신호(signal/score)와 별개 트랙이다: ATR 목표·손절을 붙이지 않고,
 * 카드를 눌러 종목 상세 시트만 연다.
 */
function reboundCard(s) {
  const chg = s.change_pct > 0 ? 'up' : s.change_pct < 0 ? 'down' : 'flat';
  const deep = s.deep_rebound
    ? '<span class="rebound-badge">깊은 과매도</span>'
    : '';
  return `
  <article class="rebound-card" data-code="${s.code}" role="button" tabindex="0" aria-label="${escapeHtml(s.name)} 상세 열기">
    <div class="rb-top">
      <div class="rb-id">
        <div class="rb-name">${escapeHtml(s.name)}</div>
        <div class="rb-meta">${s.code} · ${escapeHtml(s.market)}</div>
      </div>
      <div class="rb-price">
        <b>${fmt(s.close)}</b><span class="won">원</span>
        <span class="chg ${chg}">${fmtPct(s.change_pct)}</span>
      </div>
    </div>
    <div class="rb-row">
      <span class="rb-chip ret">5일 <b>${fmtPct(s.ret5, 1)}</b></span>
      <span class="rb-chip rsi">RSI <b>${s.rsi14}</b></span>
      ${deep}
    </div>
  </article>`;
}

function renderRebound() {
  const list = state.data.rebound_watch || [];
  const hint = $('#reboundCount');
  if (hint) hint.textContent = list.length ? `${list.length}종목 · 5일 낙폭순` : '오늘 0종목';
  const box = $('#reboundList');
  if (!box) return;
  box.innerHTML = list.length
    ? list.map(reboundCard).join('')
    : '<div class="card empty">오늘 조건에 해당하는 급락 반등 관찰 종목이 없어요.</div>';
  $$('#reboundList [data-code]').forEach((el) => {
    el.addEventListener('click', () => openSheet(el.dataset.code));
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openSheet(el.dataset.code); }
    });
  });
}

function renderAllList() {
  const rows = state.data.stocks || [];
  $('#allCount').textContent = `${rows.length}종목 · 점수순`;
  $('#allList').innerHTML = rows.map((s) => {
    const cls = signalClass(s.signal);
    const chg = s.change_pct > 0 ? 'up' : s.change_pct < 0 ? 'down' : 'flat';
    const held = s.blocked_from === 'BUY' || (s.flags || []).includes('지수5일선하회');
    return `<div class="row" data-code="${s.code}" role="button" tabindex="0">
      <span class="dot ${cls}"></span>
      <div>
        <div class="row-name">${escapeHtml(s.name)}<small>${s.code}</small>${held ? '<span class="mini-flag">5일선↓ 보류</span>' : ''}</div>
        <div class="row-sub">RSI ${s.rsi14} · 거래량 ${s.vol_ratio}배 · 고점대비 ${fmtPct(s.drawdown_from_high20, 1)}</div>
      </div>
      <div class="row-score">${s.score}점</div>
      <div class="row-right"><b>${fmt(s.close)}</b><span class="chg ${chg}">${fmtPct(s.change_pct)}</span></div>
    </div>`;
  }).join('');
  $$('#allList [data-code]').forEach((el) => el.addEventListener('click', () => openSheet(el.dataset.code)));
}

/* 내가 정한 정리·손절 기준가 (localStorage 전용) */
function exitInfo(h, close) {
  const et = Number(h.exitTarget) || 0;
  const es = Number(h.exitStop) || 0;
  if (!et && !es) return { badge: '', line: '' };
  let badge = '';
  if (et && close >= et) badge = '<span class="exit-badge target">🎯 정리 구간 도달</span>';
  else if (es && close <= es) badge = '<span class="exit-badge stop">⚠️ 손절선 이탈</span>';
  const parts = [];
  if (et) {
    const gap = (et / close - 1) * 100;
    parts.push(`<span class="${close >= et ? 'up' : ''}">내 정리 ${fmt(et)}원 <em>${close >= et ? '도달' : `까지 ${fmtPct(gap, 1)}`}</em></span>`);
  }
  if (es) {
    const gap = (es / close - 1) * 100;
    parts.push(`<span class="${close <= es ? 'down' : ''}">내 손절 ${fmt(es)}원 <em>${close <= es ? '이탈' : `까지 ${fmtPct(gap, 1)}`}</em></span>`);
  }
  return { badge, line: `<div class="exit-line">${parts.join('<i>·</i>')}</div>` };
}

function holdingView(h) {
  const stock = state.stockMap.get(h.code);
  const close = stock?.close ?? h.avg_price;
  const value = close * h.qty;
  const cost = h.avg_price * h.qty;
  const pnl = value - cost;
  const pnlPct = cost ? (pnl / cost) * 100 : 0;
  const cls = signalClass(stock?.signal);
  let range = '';
  if (stock && stock.target && stock.stop) {
    const pct = Math.min(100, Math.max(0, ((close - stock.stop) / (stock.target - stock.stop)) * 100));
    range = `
      <div class="range">
        <div class="range-bar"><span class="range-dot" style="left:${pct}%"></span></div>
        <div class="range-labels">
          <span class="stop">손절 ${fmt(stock.stop)}</span>
          <span>현재 ${fmt(close)}</span>
          <span class="target">목표 ${fmt(stock.target)}</span>
        </div>
      </div>`;
  }
  const targetDist = stock?.target ? ((stock.target / close - 1) * 100) : null;
  const stopDist = stock?.stop ? ((stock.stop / close - 1) * 100) : null;
  const exit = exitInfo(h, close);

  return `
  <article class="hold-card" data-code="${h.code}">
    <div class="hold-top">
      <div>
        <div class="hold-name">${escapeHtml(h.name)}</div>
        <div class="hold-meta">${h.qty}주 · 평균 ${fmt(h.avg_price)}원 · 현재 ${fmt(close)}원</div>
      </div>
      <div class="hold-tags">
        ${exit.badge}
        <span class="pill ${cls}">${stock?.signal_label || '분석 전'}</span>
      </div>
    </div>
    <div class="hold-nums">
      <b>${fmt(value)}원</b>
      <span class="pnl ${pnlClass(pnl)}">${pnl >= 0 ? '+' : ''}${fmt(pnl)}원 (${fmtPct(pnlPct, 1)})</span>
    </div>
    ${exit.line}
    ${range}
    ${targetDist !== null ? `<div class="hold-meta" style="margin-top:8px">목표까지 ${fmtPct(targetDist, 1)} · 손절까지 ${fmtPct(stopDist, 1)}</div>` : ''}
    ${stock?.stop && h.avg_price > stock.stop ? '<p class="hold-note">평균단가가 손절선보다 높아요. 표시된 목표·손절가는 지금 새로 진입할 때 기준입니다.</p>' : ''}
    <div class="hold-actions">
      <button type="button" data-act="edit" data-code="${h.code}">수정</button>
      <button type="button" data-act="delete" data-code="${h.code}">삭제</button>
    </div>
  </article>`;
}

function renderHoldings() {
  const totalValue = state.holdings.reduce((acc, h) => acc + (state.stockMap.get(h.code)?.close ?? h.avg_price) * h.qty, 0);
  const totalCost = state.holdings.reduce((acc, h) => acc + h.avg_price * h.qty, 0);
  const pnl = totalValue - totalCost;
  const pnlPct = totalCost ? (pnl / totalCost) * 100 : 0;
  const winners = state.holdings.filter((h) => (state.stockMap.get(h.code)?.close ?? h.avg_price) > h.avg_price).length;

  $('#holdSummary').innerHTML = `
    <div class="total-label">내 계좌 평가금액</div>
    <div class="total-value">${fmt(totalValue)}원</div>
    <div class="total-pnl ${pnlClass(pnl)}">${pnl >= 0 ? '+' : ''}${fmt(pnl)}원 (${fmtPct(pnlPct, 1)})</div>
    <div class="total-grid">
      <div><span>매입금액</span><b>${fmt(totalCost)}원</b></div>
      <div><span>보유 종목</span><b>${state.holdings.length}개</b></div>
      <div><span>수익 종목</span><b>${winners}개</b></div>
    </div>`;

  const list = $('#holdingList');
  if (!state.holdings.length) {
    list.innerHTML = `<div class="card empty"><b>아직 담긴 종목이 없어요.</b><br>위의 <b>+ 종목 추가</b>로 관심·보유 종목을 넣거나,<br>오늘의 픽 카드에서 <b>내 종목에 담기</b>를 눌러보세요.</div>`;
    return;
  }
  list.innerHTML = state.holdings.map(holdingView).join('');
  $$('#holdingList [data-act]').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (btn.dataset.act === 'edit') startEdit(btn.dataset.code);
      else removeHolding(btn.dataset.code);
    });
  });
}

function renderRecords() {
  const records = state.records.slice().reverse().slice(0, 6);
  const box = $('#recordCard');
  if (!records.length) {
    box.innerHTML = '<p class="body-text">추천 기록이 아직 없어요. 앱을 열 때마다 그날의 픽이 자동으로 저장됩니다.</p>';
    return;
  }
  box.innerHTML = records.map((r) => {
    const items = r.picks.map((p) => {
      const cur = state.stockMap.get(p.code);
      const ret = cur && cur.close && p.close ? ((cur.close / p.close - 1) * 100) : null;
      const cls = ret === null ? 'flat' : ret > 0 ? 'up' : ret < 0 ? 'down' : 'flat';
      return `<span>${escapeHtml(p.name)} <em class="chg ${cls}">${ret === null ? '-' : fmtPct(ret, 1)}</em></span>`;
    }).join('<i>·</i>');
    return `<div class="record-row">
      <div class="record-date">${r.date.slice(5)}</div>
      <div class="record-body">
        <b>${r.picks.length}종목 추천</b>
        <div class="picks">${items}</div>
      </div>
    </div>`;
  }).join('') + '<p class="footnote">괄호 안은 추천 당시 종가 대비 현재가 수익률이에요.</p>';
}

function renderBacktest() {
  const bt = state.backtest;
  const card = $('#backtestCard');
  if (!bt || !bt.summary) { card.classList.add('hidden'); return; }
  const s = bt.summary;
  const be = s.breakevens ?? 0;
  const beRate = s.breakeven_rate ?? (s.trades ? (be / s.trades) * 100 : 0);
  const avgStop = Math.abs(Number(s.avg_stop_pct) || 0);
  const avgTarget = Number(s.avg_target_pct) || 0;
  card.classList.remove('hidden');
  card.innerHTML = `
    <div class="section-head" style="margin:0 0 6px"><h2 style="font-size:14px">신호 과거 검증</h2><span class="hint">${bt.period}</span></div>
    ${bt.rule ? `<p class="bt-rule">${escapeHtml(bt.rule)}</p>` : ''}
    <div class="bt-line"><span>검증한 매수 신호</span><b>${fmt(s.trades)}건</b></div>
    <div class="bt-line"><span>목표 도달</span><b class="up">${s.win_rate.toFixed(1)}%</b></div>
    <div class="bt-line"><span>손절</span><b class="down">${s.loss_rate.toFixed(1)}%</b></div>
    <div class="bt-line"><span>본전 손절 (+3% 뒤 되밀림)</span><b>${beRate.toFixed(1)}%</b></div>
    <div class="bt-line"><span>10일 내 평균 수익률</span><b class="${s.avg_return >= 0 ? 'up' : 'down'}">${fmtPct(s.avg_return, 2)}</b></div>
    <div class="bt-line"><span>평균 손절 폭 / 목표 폭</span><b>${avgStop.toFixed(1)}% / ${avgTarget.toFixed(1)}%</b></div>
    ${typeof s.pf === 'number' ? `<div class="bt-line"><span>이익/손실 비율(PF)</span><b>${s.pf.toFixed(2)}</b></div>` : ''}
    ${s.blocked_by_index ? `<div class="bt-line"><span>5일선 아래로 건너뛴 신호</span><b>${fmt(s.blocked_by_index)}건</b></div>` : ''}
    <p class="footnote">${escapeHtml(bt.note || '')}</p>`;
}

function renderDataNotes() {
  const p = state.data.params || {};
  const notes = [
    `신호는 ${fmtDateLabel(state.data.market_date)} 종가 기준 일봉으로 계산했습니다. 장중 실시간 가격은 새로고침 때 반영돼요.`,
    `손절·목표는 종목별 14일 변동성(ATR)으로 정합니다. 손절 = 1.5×ATR14(3.5~7.0%), 목표 = 손절의 2배(7~14%), 수익 +3%에 닿으면 손절선을 본전(진입가)으로 옮깁니다.`,
    `코스피·코스닥 지수가 각각 5일선 아래면 그 시장 종목은 매수 조건을 채워도 🟡 관망으로 내려 “신규 매수 보류”로 표시합니다.`,
    `수수료는 삼성증권 평생혜택 비대면 ${(p.fee_presets?.benefit?.rate * 100 || 0.00364).toFixed(5)}%, 일반 온라인 ${(p.fee_presets?.standard?.rate * 100 || 0.015).toFixed(3)}% 중 선택해 계산합니다.`,
    '매도 세금은 코스피·코스닥 모두 0.20%(증권거래세 0.05% + 농특세 0.15%, 2026년 기준)로 계산했어요.',
    `주문가는 KRX 호가단위로 맞췄습니다. ${p.tick_table || ''}`,
    '내 종목의 정리·손절 기준가는 직접 정한 개인 기준으로, 이 폰에만 저장되고 서버·계좌로 전송되지 않아요.',
    '일봉 데이터 출처: 네이버 금융 공개 시세. 투자 자문이 아니라 참고 도구이며, 최종 판단과 주문은 본인이 mPOP에서 직접 합니다.',
  ];
  $('#dataNotes').innerHTML = notes.map((n) => `<li>${escapeHtml(n)}</li>`).join('');
}

function renderDatalist() {
  const options = [];
  for (const s of state.stockMap.values()) {
    options.push(`<option value="${escapeHtml(s.name)}">${s.code} · ${s.market}</option>`);
  }
  $('#stockOptions').innerHTML = options.join('');
}

const ADAPTER_SNIPPET = `// brokerAdapter 규격 v1 — 이 6개만 구현하면 주문 경로를 교체할 수 있어요.
const brokerAdapter = {
  id: 'manual-mpop',            // 'samsung-pop' | 'pickle-alert' | 'paper'
  name: '수동 주문 (mPOP)',

  // 현재가 조회 (장중 갱신·신호 재계산용)
  async getQuote(code) { /* → { code, price, ts } */ },

  // 잔고·예수금
  async getBalance() { /* → { cash, holdings: [{ code, qty, avgPrice }] } */ },

  // 지정가 매수/매도
  async placeOrder({ code, side, qty, price, type = 'limit' }) {
    // → { ok, orderId, filledQty, filledPrice }
  },

  // mPOP '자동감시주문(스탑로스)' 등록
  async placeBracket({ code, qty, targetPrice, stopPrice }) {
    // → { ok, bracketId }
  },

  async cancelOrder(orderId) { /* → { ok } */ },
  async getOrderStatus(orderId) { /* → { state, filledQty } */ },
};`;

function renderAdapterCode() {
  $('#adapterCode').textContent = ADAPTER_SNIPPET;
}

/* ------------------------------------------------------------ 주문 계산 */
function feeRateFor(mode) {
  const presets = state.data?.params?.fee_presets;
  if (presets?.[mode]?.rate != null) return presets[mode].rate;
  return mode === 'standard' ? 0.00015 : 0.0000364;
}
function taxRateFor(market) {
  const t = state.data?.params?.tax_rate;
  return t?.[market] ?? 0.002;
}

function computePlan(stock, budget, feeMode) {
  const feeRate = feeRateFor(feeMode);
  const taxRate = taxRateFor(stock.market);
  const entry = stock.entry;
  const qty = Math.floor(budget / entry);
  const buyAmount = entry * qty;
  const buyFee = Math.round(buyAmount * feeRate);
  const sellAmount = stock.target * qty;
  const sellFee = Math.round(sellAmount * feeRate);
  const sellTax = Math.round(sellAmount * taxRate);
  const netProfit = sellAmount - buyAmount - buyFee - sellFee - sellTax;
  const grossProfit = sellAmount - buyAmount;
  const stopAmount = stock.stop * qty;
  const stopFee = Math.round(stopAmount * feeRate);
  const stopTax = Math.round(stopAmount * taxRate);
  const maxLoss = buyAmount - stopAmount + buyFee + stopFee + stopTax;
  return {
    qty, buyAmount, buyFee, sellAmount, sellFee, sellTax, grossProfit, netProfit,
    netPct: buyAmount ? (netProfit / buyAmount) * 100 : 0,
    maxLoss, maxLossPct: buyAmount ? (maxLoss / buyAmount) * 100 : 0,
    costTotal: buyFee + sellFee + sellTax,
    remain: budget - buyAmount - buyFee,
    feeRate, taxRate,
  };
}

function planCellsHTML(stock, plan) {
  if (plan.qty === 0) {
    return `<div class="calc-cell wide" style="grid-column:1/-1"><span>예산 부족</span><b style="font-size:13px;color:var(--amber)">1주(${fmt(stock.entry)}원)보다 예산이 적어요</b>
      <small>예산을 ${fmt(stock.entry)}원 이상으로 올려보세요.</small></div>`;
  }
  return `
    <div class="calc-cell"><span>매수 수량</span><b>${plan.qty}주</b><small>${fmt(stock.entry)}원 지정가</small></div>
    <div class="calc-cell"><span>매수 금액</span><b>${fmt(plan.buyAmount)}원</b><small>수수료 ${fmt(plan.buyFee)}원</small></div>
    <div class="calc-cell profit"><span>목표 도달 순수익</span><b>+${fmt(plan.netProfit)}원</b><small>세전 +${fmt(plan.grossProfit)} / ${fmtPct(plan.netPct)}</small></div>
    <div class="calc-cell loss"><span>손절 시 최대 손실</span><b>-${fmt(plan.maxLoss)}원</b><small>-${Math.abs(plan.maxLossPct).toFixed(2)}% · 수수료·세금 포함</small></div>
    <div class="calc-cell wide"><span>왕복 비용 (수수료 + 세금)</span><b>${fmt(plan.costTotal)}원</b>
      <small>수수료율 ${(plan.feeRate * 100).toFixed(5)}% · 매도세 ${(plan.taxRate * 100).toFixed(2)}% · 남은 예산 ${fmt(plan.remain)}원</small></div>`;
}

/* ----------------------------------------------------------------- 시트 */
function openSheet(code) {
  const stock = state.stockMap.get(code);
  if (!stock) return;
  const isAnalyzed = stock.analyzed;
  const cls = signalClass(stock.signal);
  const chg = (stock.change_pct ?? 0) > 0 ? 'up' : (stock.change_pct ?? 0) < 0 ? 'down' : 'flat';
  state.sheetCode = code;

  const budget = Math.max(10000, Math.round(state.prefs.budgetByCode[code] || state.prefs.budget || 1000000));
  const body = $('#sheetBody');

  if (!isAnalyzed) {
    body.innerHTML = `
      <div class="sheet-head">
        <div>
          <h2 id="sheetTitle">${escapeHtml(stock.name)}</h2>
          <div class="meta">${stock.code} · ${stock.market}</div>
        </div>
        <button class="sheet-close" type="button" aria-label="닫기">✕</button>
      </div>
      <div class="sheet-price"><b>${fmt(stock.close)}</b><span class="won">원</span><span class="chg ${chg}">${fmtPct(stock.change_pct || 0)}</span></div>
      <p class="sheet-reason">이 종목은 아직 신호 분석 대상이 아니에요. 현재가는 네이버 시세 기준이고, 다음 분석 때 자동으로 포함할 수 있어요.</p>
      <div class="sheet-actions">
        <button class="primary-btn" type="button" data-act="add-holding">내 종목에 담기</button>
        <button class="ghost-btn" type="button" data-act="close">닫기</button>
      </div>`;
    bindSheetBase(stock);
    showSheet();
    return;
  }

  const plan = computePlan(stock, budget, state.prefs.feeMode);
  body.innerHTML = `
    <div class="sheet-head">
      <div>
        <h2 id="sheetTitle">${escapeHtml(stock.name)}</h2>
        <div class="meta">${stock.code} · ${stock.market}${stock.sector ? ` · ${escapeHtml(stock.sector)}` : ''} · 점수 ${stock.score}</div>
      </div>
      <button class="sheet-close" type="button" aria-label="닫기">✕</button>
    </div>

    <div class="sheet-price">
      <b>${fmt(stock.close)}</b><span class="won">원</span>
      <span class="chg ${chg}">${fmtPct(stock.change_pct)}</span>
      <span class="pill ${cls}" style="margin-left:auto">${stock.signal_label}</span>
    </div>
    <p class="sheet-reason">${escapeHtml(stock.reason)}</p>

    <div class="metric-chips">
      <span class="metric">20일선 <b>${fmt(stock.ma20)}</b></span>
      <span class="metric">60일선 <b>${fmt(stock.ma60)}</b></span>
      <span class="metric">RSI <b>${stock.rsi14}</b></span>
      <span class="metric">거래량 <b>${stock.vol_ratio}배</b></span>
      <span class="metric">고점대비 <b>${fmtPct(stock.drawdown_from_high20, 1)}</b></span>
      ${stock.atr_pct ? `<span class="metric">ATR14 <b>${stock.atr_pct}%</b></span>` : ''}
    </div>

    <div class="sheet-section">
      <h3>3단 콤보 주문 기준</h3>
      <div class="combo-grid">
        <div><span>목표</span><b class="up">${fmtPct(stock.target_pct, 1)}</b><small>${fmt(stock.target)}원</small></div>
        <div><span>손절</span><b class="down">${fmtPct(stock.stop_pct, 1)}</b><small>${fmt(stock.stop)}원</small></div>
        <div><span>본전 손절</span><b>+${stock.breakeven_trigger_pct ?? 3}%</b><small>도달 시 진입가로</small></div>
      </div>
      <p class="warn-line">
        손절 폭은 이 종목 14일 변동성(ATR ${stock.atr_pct ?? '-'}%)의 1.5배를 3.5~7.0% 범위로 자른 값이고, 목표는 손절의 2배예요.
        ${marketAllows(stock.market) ? '' : `<b>지금은 ${escapeHtml(stock.market)} 지수가 5일선 아래라 신규 매수 보류 상태예요.</b>`}
      </p>
    </div>

    <div class="sheet-section">
      <h3>주문 계산 (투자 예산 기준)</h3>
      <div class="budget-row">
        <input class="budget-input" id="budgetInput" type="text" inputmode="numeric" value="${fmt(budget)}" aria-label="투자 예산">
        <span class="budget-unit">원</span>
      </div>
      <div class="quick-chips" id="quickChips">
        ${[500000, 1000000, 2000000, 5000000].map((v) => `<button type="button" data-budget="${v}" class="${v === budget ? 'active' : ''}">${v / 10000}만</button>`).join('')}
      </div>
      <div class="segmented" id="feeToggle">
        <button type="button" data-fee="benefit" class="${state.prefs.feeMode === 'benefit' ? 'active' : ''}">평생혜택 비대면<br>${(feeRateFor('benefit') * 100).toFixed(5)}%</button>
        <button type="button" data-fee="standard" class="${state.prefs.feeMode === 'standard' ? 'active' : ''}">일반 온라인<br>${(feeRateFor('standard') * 100).toFixed(3)}%</button>
      </div>
      <div class="calc-grid" id="calcGrid">${planCellsHTML(stock, plan)}</div>
      <div class="copy-row">
        <button class="copy-btn" type="button" data-copy="target">목표가 복사 <span class="amount">${fmt(stock.target)}</span></button>
        <button class="copy-btn" type="button" data-copy="stop">손절가 복사 <span class="amount">${fmt(stock.stop)}</span></button>
      </div>
    </div>

    <div class="sheet-section">
      <h3>삼성증권 mPOP 주문 가이드</h3>
      <ol class="steps">
        <li><b>주식 → 매수</b>에서 종목코드 <b>${stock.code}</b>를 검색해요.</li>
        <li>주문가격에 <b>${fmt(stock.entry)}원</b>(진입가), 수량 <b id="stepQty">${plan.qty}주</b>를 넣고 <b>지정가</b>로 주문합니다.</li>
        <li>체결되면 <b>자동감시주문(스탑로스)</b>에서 목표 <b>${fmt(stock.target)}원</b> · 손절 <b>${fmt(stock.stop)}원</b>을 등록해요.</li>
      </ol>
      <p class="warn-line">계산은 종목별 KRX 호가단위와 수수료·매도세를 반영한 예상치입니다. 실제 체결 금액·세금은 증권사 명세와 다를 수 있어요.</p>
    </div>

    <div class="sheet-actions">
      <button class="primary-btn" type="button" data-act="add-holding">내 종목에 담기</button>
      <button class="ghost-btn" type="button" data-act="close">닫기</button>
    </div>`;

  bindSheetBase(stock);

  const budgetInput = $('#budgetInput');
  const refreshCalc = () => {
    const b = Math.max(0, parseInt(String(budgetInput.value).replace(/[^\d]/g, ''), 10) || 0);
    state.prefs.budgetByCode[stock.code] = b;
    state.prefs.budget = b || state.prefs.budget;
    savePrefs();
    const p = computePlan(stock, b, state.prefs.feeMode);
    $('#calcGrid').innerHTML = planCellsHTML(stock, p);
    const q = $('#stepQty');
    if (q) q.textContent = `${p.qty}주`;
  };

  budgetInput.addEventListener('input', () => {
    const digits = String(budgetInput.value).replace(/[^\d]/g, '').slice(0, 10);
    budgetInput.value = digits ? fmt(digits) : '';
  });
  budgetInput.addEventListener('change', refreshCalc);
  budgetInput.addEventListener('blur', () => {
    refreshCalc();
    if (!budgetInput.value) budgetInput.value = fmt(state.prefs.budget);
  });

  $$('#quickChips [data-budget]').forEach((btn) => {
    btn.addEventListener('click', () => {
      budgetInput.value = fmt(Number(btn.dataset.budget));
      $$('#quickChips [data-budget]').forEach((b) => b.classList.toggle('active', b === btn));
      refreshCalc();
    });
  });

  $$('#feeToggle [data-fee]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.prefs.feeMode = btn.dataset.fee;
      savePrefs();
      $$('#feeToggle [data-fee]').forEach((b) => b.classList.toggle('active', b === btn));
      refreshCalc();
    });
  });

  showSheet();
}

function bindSheetBase(stock) {
  const body = $('#sheetBody');
  body.querySelectorAll('[data-act="close"]').forEach((b) => b.addEventListener('click', closeSheet));
  body.querySelector('.sheet-close')?.addEventListener('click', closeSheet);
  body.querySelectorAll('[data-act="add-holding"]').forEach((b) => {
    b.addEventListener('click', () => addToHoldingsFromSheet(stock));
  });
  body.querySelectorAll('[data-copy]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const which = btn.dataset.copy;
      const value = which === 'target' ? stock.target : stock.stop;
      const label = which === 'target' ? '목표가' : '손절가';
      copyText(String(value)).then((ok) => {
        toast(ok ? `${label} ${fmt(value)}원을 복사했어요` : '복사가 안 됐어요. 숫자를 길게 눌러 복사해 주세요');
      });
    });
  });
}

function showSheet() {
  $('#sheet').classList.remove('hidden');
  $('#sheetBackdrop').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}

function closeSheet() {
  $('#sheet').classList.add('hidden');
  $('#sheetBackdrop').classList.add('hidden');
  document.body.style.overflow = '';
  state.sheetCode = null;
}

async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* 아래 fallback 시도 */ }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch (e) { return false; }
}

/* --------------------------------------------------------------- 내 종목 */
function addToHoldingsFromSheet(stock) {
  if (state.holdings.some((h) => h.code === stock.code)) {
    toast('이미 내 종목에 담겨 있어요');
    closeSheet();
    switchTab('holdings');
    return;
  }
  let qty = 1;
  let price = Number(stock.close) || Number(stock.entry) || 0;
  if (stock.analyzed && stock.entry) {
    const budget = Math.max(0, Math.round(state.prefs.budgetByCode[stock.code] || state.prefs.budget || 1000000));
    const plan = computePlan(stock, budget, state.prefs.feeMode);
    qty = Math.max(1, plan.qty);
    price = stock.entry;
  }
  state.holdings.push({ code: stock.code, name: stock.name, qty, avg_price: price });
  saveHoldings();
  renderHoldings();
  closeSheet();
  switchTab('holdings');
  toast(`${stock.name} ${qty}주를 내 종목에 담았어요`);
}

function removeHolding(code) {
  const h = state.holdings.find((x) => x.code === code);
  if (!h) return;
  state.holdings = state.holdings.filter((x) => x.code !== code);
  saveHoldings();
  renderHoldings();
  toast(`${h.name}을(를) 내 종목에서 뺐어요`);
}

function startEdit(code) {
  const h = state.holdings.find((x) => x.code === code);
  if (!h) return;
  state.editingCode = code;
  $('#addForm').classList.remove('hidden');
  $('#stockSearch').value = h.name;
  $('#avgPriceInput').value = h.avg_price;
  $('#qtyInput').value = h.qty;
  $('#exitTargetInput').value = h.exitTarget || '';
  $('#exitStopInput').value = h.exitStop || '';
  $('#saveHoldingBtn').textContent = '수정 저장';
  $('#formMsg').classList.add('hidden');
  $('#stockSearch').focus({ preventScroll: true });
  $('#addForm').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function resetForm() {
  state.editingCode = null;
  $('#addForm').reset();
  $('#addForm').classList.add('hidden');
  $('#saveHoldingBtn').textContent = '담기';
  $('#formMsg').classList.add('hidden');
  $('#exitTargetInput').value = '';
  $('#exitStopInput').value = '';
}

function resolveStock(text) {
  const q = String(text || '').trim();
  if (!q) return null;
  const byCode = state.stockMap.get(q.toUpperCase()) || state.stockMap.get(q);
  if (byCode) return byCode;
  const exact = Array.from(state.stockMap.values()).find((s) => s.name === q);
  if (exact) return exact;
  const loose = Array.from(state.stockMap.values()).find((s) => s.name.replace(/\s/g, '') === q.replace(/\s/g, ''));
  if (loose) return loose;
  const partial = Array.from(state.stockMap.values()).filter((s) => s.name.includes(q));
  return partial.length === 1 ? partial[0] : null;
}

function handleFormSubmit(e) {
  e.preventDefault();
  const msg = $('#formMsg');
  const stock = resolveStock($('#stockSearch').value);
  const avg = Math.round(Number($('#avgPriceInput').value));
  const qty = Math.round(Number($('#qtyInput').value));
  const exitTarget = Math.round(Number($('#exitTargetInput').value)) || 0;
  const exitStop = Math.round(Number($('#exitStopInput').value)) || 0;
  msg.classList.remove('hidden', 'error');

  if (!stock) {
    msg.textContent = '종목을 찾지 못했어요. 이름이나 종목코드(6자리)로 다시 입력해 주세요.';
    msg.classList.add('error');
    return;
  }
  if (!(avg > 0)) { msg.textContent = '매수 평균가를 입력해 주세요.'; msg.classList.add('error'); return; }
  if (!(qty > 0)) { msg.textContent = '보유 수량을 1주 이상 입력해 주세요.'; msg.classList.add('error'); return; }
  if (exitTarget && exitStop && exitStop >= exitTarget) {
    msg.textContent = '손절 기준가는 정리 기준가보다 낮아야 해요.';
    msg.classList.add('error');
    return;
  }

  const editingCode = state.editingCode;
  if (editingCode && editingCode !== stock.code && state.holdings.some((h) => h.code === stock.code)) {
    msg.textContent = '이미 담긴 종목이에요. 목록에서 수정해 주세요.';
    msg.classList.add('error');
    return;
  }
  if (state.holdings.some((h) => h.code === stock.code) && editingCode !== stock.code) {
    msg.textContent = `${stock.name}은(는) 이미 내 종목에 있어요.`;
    msg.classList.add('error');
    return;
  }

  const item = {
    code: stock.code, name: stock.name, qty, avg_price: avg,
    ...normalizeExit({ exitTarget, exitStop }),
  };
  if (editingCode) {
    // 수정 시 목록 순서를 그대로 둔다
    state.holdings = state.holdings.map((h) => (h.code === editingCode ? item : h));
  } else {
    state.holdings.push(item);
  }
  saveHoldings();
  renderHoldings();
  toast(editingCode ? `${stock.name} 정보를 수정했어요` : `${stock.name} ${qty}주를 담았어요`);
  resetForm();
}

/* ------------------------------------------------------------------ 탭 */
function switchTab(tab) {
  state.tab = tab;
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${tab}`));
  $$('.tabbar .tab').forEach((b) => {
    const on = b.dataset.tab === tab;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  window.scrollTo(0, 0);
}

function bindStaticEvents() {
  $$('.tabbar .tab').forEach((b) => b.addEventListener('click', () => switchTab(b.dataset.tab)));
  $('#sheetBackdrop').addEventListener('click', closeSheet);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSheet(); });

  $('#toggleAddBtn').addEventListener('click', () => {
    const form = $('#addForm');
    if (form.classList.contains('hidden')) {
      form.classList.remove('hidden');
      $('#saveHoldingBtn').textContent = '담기';
      $('#stockSearch').focus({ preventScroll: true });
    } else {
      resetForm();
    }
  });
  $('#cancelAddBtn').addEventListener('click', resetForm);
  $('#addForm').addEventListener('submit', handleFormSubmit);
  $('#refreshBtn').addEventListener('click', refreshData);
}

async function refreshData() {
  const btn = $('#refreshBtn');
  btn.classList.add('spin');
  try {
    state.data = await fetchJSON('data/signals.json');
    buildStockMap();
    seedRecord();
    renderAll();
    if (state.sheetCode) openSheet(state.sheetCode);
    toast(`최신 신호로 갱신했어요 (${state.data.market_date})`);
  } catch (e) {
    toast('갱신에 실패했어요. 잠시 뒤 다시 시도해 주세요');
  } finally {
    setTimeout(() => btn.classList.remove('spin'), 500);
  }
}

function showLoadError(err) {
  console.error('신호 데이터를 불러오지 못했습니다:', err);
  $('#pickList').innerHTML = `<div class="card placeholder">신호 데이터를 불러오지 못했어요.<br>우측 상단 새로고침을 눌러 다시 시도해 주세요.</div>`;
  $('#marketCard').textContent = '시장 정보를 불러오지 못했어요.';
}

/* --------------------------------------------------------------- 토스트 */
let toastTimer = null;
function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 2200);
}

/* ------------------------------------------------------------------ SW */
function registerServiceWorker() {
  if (!('serviceWorker' in navigator) || location.protocol === 'file:') return;
  navigator.serviceWorker.register('sw.js').catch((err) => {
    console.warn('서비스 워커 등록 실패:', err);
  });
}

document.addEventListener('DOMContentLoaded', boot);
