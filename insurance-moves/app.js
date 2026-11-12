import { TYPES, LEVELS, SECTORS, FUNCTIONS, classify, bestTitle, jobFunction } from './lib/classify.js';
import { DEMO } from './demo.js';

// ---------- state ----------------------------------------------------------------------------
const KEY = 'insurance-moves:v1';
const DEFAULT_FILTERS = { q: '', level: '', type: '', sector: '', func: '', range: '90', src: '', starred: false, showHidden: false };
const state = Object.assign({
  starred: {}, hidden: {}, overrides: {}, manual: [], watch: [], seen: {}, demo: false, theme: 'auto', insightsRange: '90',
}, load());
state.filters = { ...DEFAULT_FILTERS, ...(state.filters || {}) };
let data = { updated: null, sources: [], moves: [] };
let tab = location.hash.slice(1) || 'feed';
let shown = 50;

function load() { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; } }
function save() { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* private mode */ } }

const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const safeUrl = u => (/^https?:\/\//i.test(u || '') ? u : '');
const today = () => new Date().toISOString().slice(0, 10);
const daysAgo = n => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);
const fmtDay = iso => new Date(iso + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: iso.slice(0, 4) === today().slice(0, 4) ? undefined : 'numeric' });

function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), 2200);
}

function applyTheme() {
  if (state.theme === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', state.theme);
}

// ---------- data ------------------------------------------------------------------------------
function allMoves() {
  const base = state.demo ? DEMO : data.moves;
  return [...state.manual, ...base]
    .map(m => (state.overrides[m.id] ? { ...m, ...state.overrides[m.id] } : m))
    .sort((a, b) => (b.date || '').localeCompare(a.date || ''));
}

const haystack = m => `${m.person || ''} ${m.company || ''} ${m.role || ''} ${m.title || ''} ${m.notes || ''}`.toLowerCase();
const watchMatches = (m, w) => haystack(m).includes(w.toLowerCase());
const isWatched = m => state.watch.some(w => watchMatches(m, w));

function filtered(moves = allMoves(), f = state.filters) {
  const since = f.range === 'all' ? '' : daysAgo(Number(f.range));
  const q = f.q.trim().toLowerCase();
  return moves.filter(m =>
    (f.showHidden || !state.hidden[m.id]) &&
    (!since || m.date >= since) &&
    (!q || q.split(/\s+/).every(w => haystack(m).includes(w))) &&
    (!f.level || m.level === f.level) &&
    (!f.type || m.type === f.type) &&
    (!f.sector || (m.sectors || []).includes(f.sector)) &&
    (!f.func || m.func === f.func) &&
    (!f.src || m.sourceType === f.src) &&
    (!f.starred || state.starred[m.id]));
}

// ---------- rendering: move card ---------------------------------------------------------------
function card(m) {
  const who = m.person || m.company || m.title;
  const roleLine = [m.role && `<b>${esc(m.role)}</b>`, m.person && m.company && `at ${esc(m.company)}`,
    m.from && `<span class="muted">(from ${esc(m.from)})</span>`].filter(Boolean).join(' ');
  const links = (m.links?.length ? m.links : m.url ? [{ source: m.source, url: m.url }] : []).filter(l => safeUrl(l.url));
  const tags = [
    m.type && `<span class="tag t-${esc(m.type)}">${esc(m.type)}</span>`,
    m.level && `<span class="tag lvl">${esc(m.level)}</span>`,
    m.func && `<span class="tag">${esc(m.func)}</span>`,
    ...(m.sectors || []).map(s => `<span class="tag">${esc(s)}</span>`),
  ].filter(Boolean).join('');
  const headline = m.title && m.title !== who ? `<p class="headline">${esc(m.title)}</p>` : '';
  return `<article class="card ${isWatched(m) ? 'watched' : ''} ${state.hidden[m.id] ? 'hidden' : ''}" data-id="${esc(m.id)}">
    <div class="move-head">
      <div>
        <div class="who">${esc(who)}</div>
        <div class="what">${roleLine || esc(m.company || '')}</div>
      </div>
      <button class="star ${state.starred[m.id] ? 'on' : ''}" data-act="star" aria-label="Star" aria-pressed="${!!state.starred[m.id]}">${state.starred[m.id] ? '★' : '☆'}</button>
    </div>
    ${headline}
    ${m.notes ? `<p class="notes">📝 ${esc(m.notes)}</p>` : ''}
    <div class="tags">${tags}</div>
    <div class="actions">
      <span class="src">${esc(m.date)} · ${esc(m.sourceType === 'manual' ? 'Added by you' : m.source || '')}</span>
      <span style="flex:1"></span>
      ${links.map((l, i) => `<a class="btn small link" href="${esc(l.url)}" target="_blank" rel="noopener">${i === 0 ? 'Source' : esc(l.source || 'More')} ↗</a>`).join('')}
      ${m.company && !state.watch.some(w => w.toLowerCase() === m.company.toLowerCase()) ? `<button class="btn small link" data-act="watch">+ Watch</button>` : ''}
      <button class="btn small link" data-act="edit">Edit</button>
      <button class="btn small link" data-act="hide">${state.hidden[m.id] ? 'Unhide' : 'Hide'}</button>
    </div>
  </article>`;
}

function list(moves, limit) {
  if (!moves.length) return '';
  let html = '', last = '';
  for (const m of moves.slice(0, limit)) {
    if (m.date !== last) { html += `<div class="day">${esc(fmtDay(m.date))}</div>`; last = m.date; }
    html += card(m);
  }
  if (moves.length > limit) html += `<p style="text-align:center"><button class="btn" data-act="more">Show more (${moves.length - limit} left)</button></p>`;
  return html;
}

function emptyData() {
  return `<div class="empty">
    <p><b>No moves collected yet.</b></p>
    <p class="small">The collector runs on a schedule in GitHub Actions and publishes new data here.
    You can also add moves yourself, or look around with sample data first.</p>
    <button class="btn primary" data-act="demo-on">Show sample data</button>
    <button class="btn" data-goto="add">Add a move</button>
  </div>`;
}

// ---------- views -----------------------------------------------------------------------------
const opt = (vals, cur, label) => `<option value="">${label}</option>` +
  vals.map(v => `<option value="${esc(v.value ?? v)}" ${String(v.value ?? v) === String(cur) ? 'selected' : ''}>${esc(v.label ?? v)}</option>`).join('');
const RANGES = [{ value: '7', label: 'Last 7 days' }, { value: '30', label: 'Last 30 days' }, { value: '90', label: 'Last 90 days' },
  { value: '365', label: 'Last 12 months' }, { value: 'all', label: 'All time' }];

function viewFeed() {
  const f = state.filters;
  const base = allMoves();
  if (!base.length) return emptyData();
  const moves = filtered(base);
  const sel = (name, html) => `<select data-filter="${name}" class="${f[name] && !(name === 'range') ? 'on' : ''}" aria-label="${name}">${html}</select>`;
  const anyFilter = f.q || f.level || f.type || f.sector || f.func || f.src || f.starred || f.showHidden || f.range !== DEFAULT_FILTERS.range;
  return `
    <input class="search" type="search" placeholder="Search person, company, role…" value="${esc(f.q)}" data-filter="q" aria-label="Search">
    <div class="filters">
      ${sel('range', RANGES.map(r => `<option value="${r.value}" ${r.value === f.range ? 'selected' : ''}>${r.label}</option>`).join(''))}
      ${sel('level', opt(LEVELS, f.level, 'All levels'))}
      ${sel('type', opt(TYPES, f.type, 'All move types'))}
      ${sel('sector', opt(SECTORS, f.sector, 'All sectors'))}
      ${sel('func', opt(FUNCTIONS, f.func, 'All functions'))}
      ${sel('src', opt([{ value: 'news', label: 'News' }, { value: 'sec', label: 'SEC filings' }, { value: 'manual', label: 'Added by you' }], f.src, 'All sources'))}
    </div>
    <div class="bar">
      <span class="small muted">${moves.length} move${moves.length === 1 ? '' : 's'}</span>
      <span>
        <button class="btn small ${f.starred ? 'primary' : ''}" data-toggle="starred">★ Starred</button>
        <button class="btn small ${f.showHidden ? 'primary' : ''}" data-toggle="showHidden">Show hidden</button>
        ${anyFilter ? '<button class="btn small link" data-act="reset">Reset</button>' : ''}
        <button class="btn small" data-act="csv">Export CSV</button>
      </span>
    </div>
    ${moves.length ? list(moves, shown) : '<div class="empty">No moves match these filters.</div>'}`;
}

function viewWatch() {
  const moves = allMoves().filter(m => !state.hidden[m.id]);
  const chips = state.watch.map((w, i) => {
    const n = moves.filter(m => watchMatches(m, w)).length;
    return `<span class="chip">${esc(w)} <span class="count">${n}</span><button data-unwatch="${i}" aria-label="Remove ${esc(w)}">×</button></span>`;
  }).join('');
  const hits = moves.filter(isWatched);
  const fresh = hits.filter(m => !state.seen[m.id]).length;
  return `
    <h2>Watchlist</h2>
    <p class="small muted">Follow companies, people or keywords (for example a carrier, a competitor, an executive, or “cyber”).
      Matching moves are highlighted everywhere in the app.</p>
    <form id="watch-form" class="row">
      <input name="w" placeholder="Company, person or keyword" aria-label="Add to watchlist" class="search" autocomplete="off">
      <button class="btn primary" style="flex:0 0 auto">Add</button>
    </form>
    <div class="chips">${chips || '<span class="small muted">Nothing on your watchlist yet.</span>'}</div>
    ${state.watch.length ? `<h3>${hits.length} matching move${hits.length === 1 ? '' : 's'}${fresh ? ` · ${fresh} new` : ''}</h3>` : ''}
    ${list(hits, shown)}`;
}

function counts(moves, keyFn) {
  const c = new Map();
  for (const m of moves) for (const k of [].concat(keyFn(m) || [])) if (k) c.set(k, (c.get(k) || 0) + 1);
  return [...c.entries()].sort((a, b) => b[1] - a[1]);
}
function hbars(rows, { clickable = false, order } = {}) {
  if (order) rows = order.map(k => [k, rows.find(r => r[0] === k)?.[1] || 0]).filter(r => r[1]);
  if (!rows.length) return '<p class="small muted">No data in this period.</p>';
  const max = Math.max(...rows.map(r => r[1]));
  return `<div class="hbars">${rows.map(([k, v]) => `
    ${clickable ? `<button class="lab" data-company="${esc(k)}" title="Show moves at ${esc(k)}">${esc(k)}</button>` : `<span class="lab" title="${esc(k)}">${esc(k)}</span>`}
    <span class="track" title="${esc(k)}: ${v}"><span class="fill" style="width:${(v / max) * 82}%"></span><span class="val">${v}</span></span>`).join('')}</div>`;
}
function columns(moves, range) {
  const monthly = range === '365' || range === 'all';
  const buckets = [];
  if (monthly) {
    const now = new Date(); const n = range === 'all' ? 24 : 12;
    for (let i = n - 1; i >= 0; i--) {
      const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
      buckets.push({ key, label: d.toLocaleDateString(undefined, { month: 'short', year: '2-digit' }), n: 0 });
    }
    for (const m of moves) { const b = buckets.find(b => b.key === m.date.slice(0, 7)); if (b) b.n++; }
  } else {
    const weeks = range === '7' ? 4 : range === '30' ? 8 : 13;
    for (let i = weeks - 1; i >= 0; i--) {
      const end = daysAgo(i * 7), start = daysAgo(i * 7 + 6);
      buckets.push({ start, end, label: `week of ${fmtDay(start)}`, n: 0 });
    }
    for (const m of moves) { const b = buckets.find(b => m.date >= b.start && m.date <= b.end); if (b) b.n++; }
  }
  const max = Math.max(1, ...buckets.map(b => b.n));
  return `<div class="cols" role="img" aria-label="Moves per ${monthly ? 'month' : 'week'}">${buckets.map(b =>
    `<div class="col" tabindex="0"><span class="tip">${esc(b.label)}: ${b.n}</span><i style="height:${(b.n / max) * 100}%"></i></div>`).join('')}</div>
    <div class="axis"><span>${esc(buckets[0].label)}</span><span>${monthly ? 'per month' : 'per week'}</span><span>${monthly ? esc(buckets.at(-1).label) : 'this week'}</span></div>`;
}

function viewInsights() {
  const r = state.insightsRange;
  const base = allMoves();
  if (!base.length) return emptyData();
  const moves = filtered(base, { ...DEFAULT_FILTERS, range: r });
  const up = moves.filter(m => m.type === 'Appointment' || m.type === 'Promotion').length;
  const out = moves.filter(m => m.type === 'Departure' || m.type === 'Retirement').length;
  const csuite = moves.filter(m => m.level === 'C-suite').length;
  const companies = counts(moves, m => m.company);
  const seg = RANGES.filter(x => x.value !== '7').map(x =>
    `<button class="${x.value === r ? 'on' : ''}" data-irange="${x.value}">${x.label.replace('Last ', '').replace('All time', 'All')}</button>`).join('');
  return `
    <div class="bar"><h2 style="margin:6px 0">Insights</h2><span class="seg">${seg}</span></div>
    <div class="tiles">
      <div class="tile"><div class="n">${moves.length}</div><div class="l">management moves</div></div>
      <div class="tile"><div class="n">${csuite}</div><div class="l">C-suite moves</div></div>
      <div class="tile"><div class="n">${up} / ${out}</div><div class="l">hires &amp; promotions / exits &amp; retirements</div></div>
      <div class="tile"><div class="n">${companies.length}</div><div class="l">companies with moves</div></div>
    </div>
    <div class="panel"><h3>Moves over time</h3>${columns(moves, r)}</div>
    <div class="panel"><h3>Most active companies</h3>${hbars(companies.slice(0, 10), { clickable: true })}</div>
    <div class="panel"><h3>By level</h3>${hbars(counts(moves, m => m.level), { order: LEVELS })}</div>
    <div class="panel"><h3>By move type</h3>${hbars(counts(moves, m => m.type), { order: TYPES })}</div>
    <div class="panel"><h3>By function</h3>${hbars(counts(moves, m => m.func))}</div>
    <div class="panel"><h3>By sector</h3>${hbars(counts(moves, m => m.sectors))}</div>
    <p class="small muted">Counts exclude moves you've hidden. Tap a company to see its moves.</p>`;
}

function moveForm(m = {}, { editing = false } = {}) {
  const sel = (name, vals, cur, blank) => `<select name="${name}">${opt(vals, cur, blank)}</select>`;
  return `<form id="move-form" data-id="${esc(m.id || '')}">
    ${editing ? '' : `<div class="field"><label for="paste">Quick add from a headline (optional)</label>
      <textarea id="paste" placeholder="Paste a headline or announcement, e.g. “Acme Mutual names Jane Doe chief claims officer”"></textarea>
      <button type="button" class="btn small" data-act="parse" style="margin-top:6px">Fill in from text</button></div>`}
    <div class="row"><div class="field"><label>Person</label><input name="person" value="${esc(m.person)}" placeholder="Jane Doe"></div>
      <div class="field"><label>Company</label><input name="company" value="${esc(m.company)}" placeholder="Acme Mutual" required></div></div>
    <div class="field"><label>Role / title</label><input name="role" value="${esc(m.role)}" placeholder="Chief Claims Officer">
      <div class="hint">Level and function are suggested from the title.</div></div>
    <div class="row"><div class="field"><label>Move</label>${sel('type', TYPES, m.type || 'Appointment', '—')}</div>
      <div class="field"><label>Level</label>${sel('level', LEVELS, m.level, '—')}</div></div>
    <div class="row"><div class="field"><label>Function</label>${sel('func', FUNCTIONS, m.func, '—')}</div>
      <div class="field"><label>Sector</label>${sel('sector', SECTORS, (m.sectors || [])[0], '—')}</div></div>
    <div class="row"><div class="field"><label>Date</label><input name="date" type="date" value="${esc(m.date || today())}" required></div>
      <div class="field"><label>Previous company</label><input name="from" value="${esc(m.from)}" placeholder="Optional"></div></div>
    <div class="field"><label>Source link</label><input name="url" type="url" value="${esc(m.url)}" placeholder="https://…"></div>
    <div class="field"><label>Notes</label><textarea name="notes" placeholder="Private notes, e.g. who to contact, what it means for you">${esc(m.notes)}</textarea></div>
    <div class="bar">
      ${editing && m.sourceType === 'manual' ? '<button type="button" class="btn" data-act="delete" style="color:var(--bad)">Delete</button>' : '<span></span>'}
      <span>${editing ? '<button type="button" class="btn" data-act="close">Cancel</button>' : ''}
      <button class="btn primary">${editing ? 'Save' : 'Add move'}</button></span>
    </div>
  </form>`;
}

function viewAdd() {
  return `<h2>Add a move</h2>
    <p class="small muted">Heard about a move that isn't in the feed? Add it here. It's saved on this device and included in exports and insights.</p>
    ${moveForm()}`;
}

function viewMore() {
  const rows = (data.sources || []).map(s => `<tr><td>${esc(s.name)}</td>
    <td class="${s.ok ? 'ok' : 'err'}">${s.ok ? `✓ ${s.moves} of ${s.items}` : `✗ ${esc(s.error)}`}</td></tr>`).join('');
  const counts = { collected: data.moves.length, manual: state.manual.length, starred: Object.keys(state.starred).length, hidden: Object.keys(state.hidden).length };
  return `
    <h2>Data</h2>
    <div class="panel small">
      <p style="margin-top:0">Last collected: <b>${data.updated ? new Date(data.updated).toLocaleString() : 'not yet'}</b><br>
      ${counts.collected} collected moves · ${counts.manual} added by you · ${counts.starred} starred · ${counts.hidden} hidden</p>
      ${rows ? `<table class="status"><tr><th>Source (last run)</th><th>Moves found</th></tr>${rows}</table>` : ''}
      <p class="muted">Moves are picked out of headlines and SEC filings automatically, so some will be missed or misread.
      Use <b>Edit</b> to correct one and <b>Hide</b> to drop false hits. Your changes are kept on this device.</p>
    </div>
    <div class="panel">
      <h3>Export &amp; backup</h3>
      <div class="actions" style="gap:8px">
        <button class="btn" data-act="csv-all">All moves as CSV</button>
        <button class="btn" data-act="backup">Back up my data</button>
        <label class="btn">Restore backup<input type="file" accept="application/json" id="restore" hidden></label>
      </div>
      <p class="small muted">The backup holds your watchlist, stars, hidden items, edits and moves you added. Use it to move to another phone.</p>
    </div>
    <div class="panel">
      <h3>Display</h3>
      <div class="bar"><span>Theme</span><span class="seg">${['auto', 'light', 'dark'].map(t =>
        `<button class="${state.theme === t ? 'on' : ''}" data-theme-set="${t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join('')}</span></div>
      <div class="bar"><span>Sample data <span class="small muted">(fictional, for trying the app)</span></span>
        <button class="btn small ${state.demo ? 'primary' : ''}" data-act="${state.demo ? 'demo-off' : 'demo-on'}">${state.demo ? 'On' : 'Off'}</button></div>
    </div>
    <div class="panel small">
      <h3>Where the data comes from</h3>
      <p>Insurance trade-press feeds and Google News searches for appointments, promotions, departures and retirements,
      plus SEC 8-K filings (Item 5.02, officer and director changes) from US-listed insurers and brokers. Only
      management-level roles are kept: C-suite, president/MD, EVP/SVP, VP, head/director, board, and manager.
      Each item links to its source.</p>
    </div>`;
}

// ---------- sheet (edit) ----------------------------------------------------------------------
function openEdit(id) {
  const m = allMoves().find(x => x.id === id);
  if (!m) return;
  const bg = document.createElement('div');
  bg.className = 'sheet-bg';
  bg.innerHTML = `<div class="sheet" role="dialog" aria-modal="true" aria-label="Edit move"><h2 style="margin-top:4px">Edit move</h2>${moveForm(m, { editing: true })}</div>`;
  document.body.append(bg);
  bg.addEventListener('click', e => { if (e.target === bg || e.target.closest('[data-act="close"]')) bg.remove(); });
  wireForm(bg, m);
}

function readForm(form) {
  const v = Object.fromEntries(new FormData(form).entries());
  return {
    person: v.person.trim() || null, company: v.company.trim() || null, role: v.role.trim() || null,
    type: v.type || null, level: v.level || null, func: v.func || null, sectors: v.sector ? [v.sector] : [],
    date: v.date || today(), from: v.from.trim() || null, url: safeUrl(v.url.trim()), notes: v.notes.trim() || null,
  };
}

function wireForm(root, existing) {
  const form = $('#move-form', root);
  const role = form.elements.role;
  role.addEventListener('change', () => {
    const t = bestTitle(role.value);
    if (t && !form.elements.level.value) form.elements.level.value = t.level;
    const fn = jobFunction(role.value);
    if (fn && !form.elements.func.value) form.elements.func.value = fn;
  });
  form.addEventListener('click', e => {
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'parse') {
      const text = $('#paste', form).value.trim();
      if (!text) return;
      const c = classify({ title: text });
      const set = (k, v) => { if (v && form.elements[k]) form.elements[k].value = v; };
      set('person', c.person); set('company', c.company); set('role', c.role); set('type', c.type);
      set('level', c.level); set('func', c.func); set('sector', c.sectors[0]);
      toast(c.isMove ? 'Filled in. Check the fields before saving.' : 'Couldn\'t find a move in that text. Fill it in by hand.');
    }
    if (act === 'delete' && confirm('Delete this move?')) {
      state.manual = state.manual.filter(m => m.id !== existing.id);
      save(); root.remove?.(); render(); toast('Deleted');
    }
  });
  form.addEventListener('submit', e => {
    e.preventDefault();
    const v = readForm(form);
    if (existing) {
      if (existing.sourceType === 'manual') {
        const i = state.manual.findIndex(m => m.id === existing.id);
        state.manual[i] = { ...state.manual[i], ...v };
      } else {
        state.overrides[existing.id] = v;
      }
      save(); root.remove(); render(); toast('Saved');
    } else {
      state.manual.unshift({ id: 'm-' + Date.now().toString(36), ...v, title: $('#paste', form)?.value.trim() || '', source: 'You', sourceType: 'manual',
        links: v.url ? [{ source: 'Source', url: v.url }] : [] });
      save(); toast('Move added'); go('feed');
    }
  });
}

// ---------- CSV / backup ----------------------------------------------------------------------
function download(name, text, type) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}
function csv(moves) {
  const cols = ['date', 'person', 'company', 'role', 'level', 'type', 'func', 'sectors', 'from', 'source', 'url', 'title', 'notes', 'starred'];
  const cell = v => { const s = Array.isArray(v) ? v.join('; ') : String(v ?? ''); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const rows = moves.map(m => cols.map(c => cell(c === 'starred' ? (state.starred[m.id] ? 'yes' : '') : c === 'func' ? m.func : m[c])).join(','));
  download(`insurance-moves-${today()}.csv`, [cols.join(','), ...rows].join('\n'), 'text/csv');
}

// ---------- shell -----------------------------------------------------------------------------
function updateBadge() {
  const n = allMoves().filter(m => !state.hidden[m.id] && !state.seen[m.id] && isWatched(m)).length;
  const b = $('#watch-badge');
  b.hidden = !n || tab === 'watch'; b.textContent = n > 99 ? '99+' : n;
}

function render() {
  const views = { feed: viewFeed, watch: viewWatch, insights: viewInsights, add: viewAdd, more: viewMore };
  if (!views[tab]) tab = 'feed';
  $('#view').innerHTML = views[tab]();
  document.querySelectorAll('.tabs button').forEach(b => (b.dataset.tab === tab ? b.setAttribute('aria-current', 'page') : b.removeAttribute('aria-current')));
  if (tab === 'add') wireForm(document);
  if (tab === 'watch') { for (const m of allMoves()) if (isWatched(m)) state.seen[m.id] = 1; save(); }
  $('#updated').textContent = state.demo ? 'Sample data' : data.updated ? `Updated ${timeAgo(data.updated)}` : '';
  updateBadge();
}
function timeAgo(iso) {
  const h = (Date.now() - new Date(iso)) / 36e5;
  return h < 1 ? 'just now' : h < 24 ? `${Math.floor(h)}h ago` : `${Math.floor(h / 24)}d ago`;
}
function go(t) { tab = t; shown = 50; history.replaceState(null, '', '#' + t); render(); scrollTo(0, 0); }

document.querySelector('.tabs').addEventListener('click', e => { const b = e.target.closest('button'); if (b) go(b.dataset.tab); });

const view = $('#view');
view.addEventListener('input', e => {
  if (e.target.dataset.filter !== 'q') return;
  state.filters.q = e.target.value; save();
  clearTimeout(view.t);
  view.t = setTimeout(() => { const pos = e.target.selectionStart; render(); const s = $('.search'); s.focus(); s.setSelectionRange(pos, pos); }, 200);
});
view.addEventListener('change', e => {
  const f = e.target.dataset.filter;
  if (f && f !== 'q') { state.filters[f] = e.target.value; shown = 50; save(); render(); }
  if (e.target.id === 'restore') restore(e.target.files[0]);
});
view.addEventListener('submit', e => {
  if (e.target.id !== 'watch-form') return;
  e.preventDefault();
  const w = e.target.elements.w.value.trim();
  if (w && !state.watch.some(x => x.toLowerCase() === w.toLowerCase())) { state.watch.push(w); save(); }
  render(); $('#watch-form input')?.focus();
});
view.addEventListener('click', e => {
  const el = e.target.closest('[data-act],[data-toggle],[data-goto],[data-unwatch],[data-company],[data-irange],[data-theme-set]');
  if (!el) return;
  const id = el.closest('[data-id]')?.dataset.id;
  const d = el.dataset;
  if (d.goto) return go(d.goto);
  if (d.toggle) { state.filters[d.toggle] = !state.filters[d.toggle]; save(); return render(); }
  if (d.unwatch) { state.watch.splice(Number(d.unwatch), 1); save(); return render(); }
  if (d.company) { state.filters = { ...DEFAULT_FILTERS, range: state.insightsRange, q: d.company }; save(); return go('feed'); }
  if (d.irange) { state.insightsRange = d.irange; save(); return render(); }
  if (d.themeSet) { state.theme = d.themeSet; save(); applyTheme(); return render(); }
  const m = id && allMoves().find(x => x.id === id);
  switch (d.act) {
    case 'star': state.starred[id] ? delete state.starred[id] : (state.starred[id] = 1); break;
    case 'hide': state.hidden[id] ? delete state.hidden[id] : (state.hidden[id] = 1); if (state.hidden[id]) toast('Hidden. Turn on “Show hidden” to see it again.'); break;
    case 'watch': if (m?.company) { state.watch.push(m.company); toast(`Watching ${m.company}`); } break;
    case 'edit': return openEdit(id);
    case 'more': shown += 50; break;
    case 'reset': state.filters = { ...DEFAULT_FILTERS }; break;
    case 'csv': return csv(filtered());
    case 'csv-all': return csv(allMoves());
    case 'backup': {
      const { starred, hidden, overrides, manual, watch } = state;
      return download(`insurance-moves-backup-${today()}.json`, JSON.stringify({ app: 'insurance-moves', v: 1, starred, hidden, overrides, manual, watch }, null, 1), 'application/json');
    }
    case 'demo-on': state.demo = true; toast('Showing fictional sample data'); break;
    case 'demo-off': state.demo = false; break;
    default: return;
  }
  save(); render();
});

async function restore(file) {
  if (!file) return;
  try {
    const b = JSON.parse(await file.text());
    if (b.app !== 'insurance-moves') throw new Error('not a backup from this app');
    for (const k of ['starred', 'hidden', 'overrides']) state[k] = { ...state[k], ...(b[k] || {}) };
    const ids = new Set(state.manual.map(m => m.id));
    state.manual.push(...(b.manual || []).filter(m => !ids.has(m.id)));
    state.watch = [...new Set([...state.watch, ...(b.watch || [])])];
    save(); render(); toast('Backup restored');
  } catch (e) { toast(`Couldn't restore: ${e.message}`); }
}

async function loadData() {
  try {
    const res = await fetch('data/moves.json', { cache: 'no-cache' });
    if (res.ok) data = await res.json();
  } catch { /* offline with no cache */ }
  render();
}

applyTheme();
render();
loadData();
if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
