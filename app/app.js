import { parseList, buyCount, describe } from './parser.js';
import { computePlans, chosenOffer } from './optimizer.js';
import { PROVIDERS, allStores, workerFetch } from './providers.js';

// ---------- persistence ----------

const DEFAULT_SETTINGS = {
  demo: true,
  workerUrl: '',
  appKey: '',
  zip: '',
  kroger: { enabled: false, locationId: '', locationName: '', modality: 'PICKUP', token: '', refresh: '', expires: 0 },
  instacart: { enabled: false },
  shopify: [],
  fees: {},
};

function load(key, fallback) {
  try { const v = JSON.parse(localStorage.getItem(key)); return v ?? fallback; } catch { return fallback; }
}
function save(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } }

const settings = { ...DEFAULT_SETTINGS, ...load('gc.settings', {}) };
settings.kroger = { ...DEFAULT_SETTINGS.kroger, ...settings.kroger };
settings.instacart = { ...DEFAULT_SETTINGS.instacart, ...settings.instacart };
const saveSettings = () => save('gc.settings', settings);

const state = {
  tab: 'list',
  listText: load('gc.listText', ''),
  parsedText: load('gc.parsedText', ''),
  items: load('gc.items', []),
  offers: {},
  picks: {},
  errors: {},
  progress: null,
  planKey: 'mix',
  carts: null,
  expanded: null,
  krogerLocations: [],
};

// ---------- small utils ----------

const $ = sel => document.querySelector(sel);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = n => `$${(Number(n) || 0).toFixed(2)}`;
const fee = store => (settings.fees[store.id] ?? store.defaultFee ?? 0);
function feesMap(stores) { const m = {}; for (const s of stores) m[s.id] = fee(s); return m; }

function toast(msg, isError = false) {
  const t = $('#toast');
  t.textContent = msg; t.className = `toast show${isError ? ' error' : ''}`;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.className = 'toast'; }, 3800);
}

async function pool(tasks, limit = 4) {
  let i = 0;
  const run = async () => { while (i < tasks.length) { const t = tasks[i++]; await t(); } };
  await Promise.all(Array.from({ length: Math.min(limit, tasks.length) }, run));
}

// ---------- Kroger OAuth return (#kroger_token=...) ----------

(function handleOAuthReturn() {
  if (!location.hash.includes('kroger_token=')) return;
  const p = new URLSearchParams(location.hash.slice(1));
  settings.kroger.token = p.get('kroger_token');
  settings.kroger.refresh = p.get('kroger_refresh') || '';
  settings.kroger.expires = Date.now() + (Number(p.get('expires_in')) || 1800) * 1000;
  saveSettings();
  history.replaceState(null, '', location.pathname + location.search);
  state.tab = 'settings';
  setTimeout(() => toast('Kroger account connected'), 300);
})();

async function freshKrogerToken() {
  const k = settings.kroger;
  if (k.token && Date.now() < k.expires - 60000) return;
  if (!k.refresh) { k.token = ''; saveSettings(); return; }
  try {
    const r = await workerFetch(settings, '/kroger/refresh', { method: 'POST', body: JSON.stringify({ refresh_token: k.refresh }) });
    k.token = r.access_token; k.refresh = r.refresh_token || k.refresh;
    k.expires = Date.now() + (r.expires_in || 1800) * 1000;
  } catch { k.token = ''; k.refresh = ''; }
  saveSettings();
}

// ---------- actions ----------

function parseInput() {
  const text = $('#list-input').value;
  state.listText = text; save('gc.listText', text);
  state.parsedText = text; save('gc.parsedText', text);
  state.items = parseList(text); save('gc.items', state.items);
  state.offers = {}; state.picks = {}; state.errors = {}; state.carts = null;
  render();
}

async function findPrices() {
  const stores = allStores(settings).filter(s => s.priced);
  if (!state.items.length) return toast('Add some ingredients first', true);
  if (!stores.length) {
    state.tab = 'compare'; render();
    return toast('No stores with prices are connected. Turn on demo stores or connect a store in Settings.', true);
  }
  state.offers = {}; state.errors = {}; state.carts = null;
  const tasks = [];
  for (const item of state.items) {
    state.offers[item.id] = {};
    for (const store of stores) {
      tasks.push(async () => {
        try {
          state.offers[item.id][store.id] = await PROVIDERS[store.provider].search(item, store, settings);
        } catch (e) {
          state.offers[item.id][store.id] = [];
          state.errors[store.id] = e.message;
        }
        state.progress.done++;
        renderProgress();
      });
    }
  }
  state.progress = { done: 0, total: tasks.length };
  state.tab = 'compare'; render();
  await pool(tasks, 5);
  state.progress = null;
  render();
  const errs = Object.entries(state.errors);
  if (errs.length) {
    const name = id => stores.find(s => s.id === id)?.name || id;
    toast(`${name(errs[0][0])}: ${errs[0][1]}`, true);
  }
}

function currentPlans() {
  const stores = allStores(settings).filter(s => s.priced);
  return { stores, ...computePlans(state.items, stores.map(s => s.id), state.offers, state.picks, feesMap(stores)) };
}

function selectedPlan(plans) {
  if (state.planKey === 'mix') return plans.mix;
  return plans.single.find(p => p.storeIds[0] === state.planKey) || plans.mix;
}

async function createCarts() {
  const plans = currentPlans();
  const plan = selectedPlan(plans);
  const stores = allStores(settings);
  state.carts = [];
  const byStore = {};
  for (const line of plan?.lines || []) (byStore[line.storeId] ||= []).push(line);
  if (Object.keys(byStore).some(id => id === 'kroger')) await freshKrogerToken();
  render();

  for (const [storeId, lines] of Object.entries(byStore)) {
    const store = stores.find(s => s.id === storeId);
    const entry = { store, status: 'working' };
    state.carts.push(entry); render();
    try { Object.assign(entry, await PROVIDERS[store.provider].createCart(store, lines, settings), { status: 'ok' }); }
    catch (e) { Object.assign(entry, { status: 'error', message: e.message }); }
    render();
  }
}

async function sendToInstacart(items) {
  const store = allStores(settings).find(s => s.id === 'instacart');
  if (!store) return toast('Turn on Instacart in Settings first', true);
  const btn = document.activeElement;
  if (btn) btn.disabled = true;
  try {
    const res = await PROVIDERS.instacart.createCart(store, items, settings, 'My grocery list');
    state.instacartUrl = res.url;
    render();
    window.open(res.url, '_blank');
  } catch (e) { toast(e.message, true); }
  finally { if (btn) btn.disabled = false; }
}

// ---------- rendering ----------

function render() {
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === state.tab));
  const view = $('#view');
  view.innerHTML = state.tab === 'list' ? renderList() : state.tab === 'compare' ? renderCompare() : renderSettings();
  if (state.tab === 'list') {
    const ta = $('#list-input');
    if (ta) ta.value = state.listText;
  }
}

function renderProgress() {
  const el = $('#progress');
  if (el && state.progress) {
    el.querySelector('.bar span').style.width = `${(100 * state.progress.done) / state.progress.total}%`;
    el.querySelector('.label').textContent = `Checking prices… ${state.progress.done}/${state.progress.total}`;
  }
}

function renderList() {
  const stores = allStores(settings);
  const items = state.items.map(item => `
    <li class="item">
      <div class="item-main">
        <span class="item-name">${esc(item.name)}</span>
        <span class="muted small">${esc(describe(item))}</span>
      </div>
      <div class="stepper" aria-label="Quantity to buy">
        <button data-act="qty" data-id="${item.id}" data-d="-1" aria-label="Fewer">−</button>
        <span>${buyCount(item)}</span>
        <button data-act="qty" data-id="${item.id}" data-d="1" aria-label="More">+</button>
      </div>
      <button class="icon-btn" data-act="remove" data-id="${item.id}" aria-label="Remove ${esc(item.name)}">✕</button>
    </li>`).join('');

  return `
    <section class="card">
      <h2>Ingredient list</h2>
      <p class="muted small">One item per line. Quantities like “2 lb”, “1 dozen” or “3 cans” work.</p>
      <textarea id="list-input" rows="8" placeholder="2 lb chicken breast&#10;1 dozen eggs&#10;1 gallon milk&#10;3 cans black beans&#10;cilantro&#10;2 avocados"></textarea>
      <div class="row">
        <button class="btn secondary" data-act="parse">Update list</button>
        <button class="btn ghost" data-act="sample">Use sample</button>
      </div>
    </section>
    ${state.items.length ? `
    <section class="card">
      <div class="row between"><h2>${state.items.length} item${state.items.length === 1 ? '' : 's'}</h2><span class="muted small">qty to buy</span></div>
      <ul class="items">${items}</ul>
      <button class="btn primary full" data-act="find">Compare prices</button>
      ${stores.some(s => s.id === 'instacart') ? '<button class="btn secondary full" data-act="instacart-all">Send whole list to Instacart</button>' : ''}
      <p class="muted small center">Checking: ${stores.filter(s => s.priced).map(s => esc(s.name)).join(', ') || 'no priced stores yet'}</p>
    </section>` : ''}`;
}

function renderCompare() {
  if (state.progress) {
    return `<section class="card" id="progress"><p class="label">Checking prices… 0/${state.progress.total}</p><div class="bar"><span></span></div></section>`;
  }
  if (!state.items.length) return `<section class="card empty"><p>Add an ingredient list first.</p><button class="btn primary" data-act="tab" data-tab="list">Go to list</button></section>`;
  if (!Object.keys(state.offers).length) return `<section class="card empty"><p>Prices haven't been checked yet.</p><button class="btn primary" data-act="find">Compare prices</button></section>`;

  const plans = currentPlans();
  const { stores, mix, single, savings } = plans;
  const plan = selectedPlan(plans);
  const storeName = id => esc(stores.find(s => s.id === id)?.name || id);
  const instacart = allStores(settings).find(s => s.id === 'instacart');

  const option = (key, title, p, extra = '') => `
    <button class="plan ${state.planKey === key ? 'selected' : ''}" data-act="plan" data-key="${esc(key)}">
      <div class="plan-top"><span class="plan-title">${title}</span><span class="plan-total">${money(p.total)}</span></div>
      <div class="muted small">${money(p.subtotal)} items${p.fees ? ` + ${money(p.fees)} fees` : ''}${p.missing.length ? ` · <span class="warn">${p.missing.length} not found</span>` : ''}${extra}</div>
    </button>`;

  const mixDesc = mix ? ` · ${mix.storeIds.map(storeName).join(' + ')}` : '';
  const planHtml = `
    <section class="card">
      <h2>Cheapest options</h2>
      ${mix ? option('mix', `Mix &amp; match${savings > 0.005 ? ` <span class="badge">save ${money(savings)}</span>` : ''}`, mix, mixDesc) : ''}
      <h3>All at one store</h3>
      ${single.map(p => option(p.storeIds[0], storeName(p.storeIds[0]), p)).join('')}
      ${instacart ? `<div class="note">Instacart doesn't share prices with outside apps, so it isn't in this comparison. You can still send the list and see prices inside Instacart.
        <button class="btn secondary full" data-act="instacart-all">Send list to Instacart</button>${state.instacartUrl ? `<a class="small" href="${esc(state.instacartUrl)}" target="_blank" rel="noopener">Open Instacart list again ↗</a>` : ''}</div>` : ''}
      ${Object.keys(state.errors).length ? `<div class="note error">${Object.entries(state.errors).map(([id, m]) => `<b>${storeName(id)}:</b> ${esc(m)}`).join('<br>')}</div>` : ''}
    </section>`;

  const lineByItem = {};
  for (const l of plan?.lines || []) lineByItem[l.item.id] = l;

  const itemRows = state.items.map(item => {
    const line = lineByItem[item.id];
    const open = state.expanded === item.id;
    const perStore = stores.map(s => {
      const list = state.offers[item.id]?.[s.id] || [];
      const chosen = chosenOffer(item.id, s.id, state.offers, state.picks);
      if (!list.length) return `<div class="store-offers"><div class="store-name">${esc(s.name)}</div><div class="muted small">Not found</div></div>`;
      return `<div class="store-offers"><div class="store-name">${esc(s.name)}</div>
        ${list.slice(0, 6).map(o => `
          <label class="offer ${chosen && chosen.id === o.id ? 'chosen' : ''}">
            <input type="radio" name="pick-${item.id}-${esc(s.id)}" data-act="pick" data-item="${item.id}" data-store="${esc(s.id)}" data-offer="${esc(o.id)}" ${chosen && chosen.id === o.id ? 'checked' : ''}>
            ${o.image ? `<img src="${esc(o.image)}" alt="" loading="lazy">` : ''}
            <span class="offer-title">${esc(o.title)}${o.size ? ` <span class="muted">· ${esc(o.size)}</span>` : ''}</span>
            <span class="offer-price">${money(o.price)}${o.onSale ? '<span class="sale">sale</span>' : ''}</span>
          </label>`).join('')}</div>`;
    }).join('');
    return `
      <li class="result ${open ? 'open' : ''}">
        <button class="result-head" data-act="expand" data-id="${item.id}" aria-expanded="${open}">
          <span class="item-main"><span class="item-name">${esc(item.name)} <span class="muted">×${buyCount(item)}</span></span>
          <span class="muted small">${line ? `${esc(line.offer.title)} @ ${storeName(line.storeId)}` : '<span class="warn">Not found in this plan</span>'}</span></span>
          <span class="offer-price">${line ? money(line.cost) : '—'}</span>
        </button>
        ${open ? `<div class="result-body">${perStore}</div>` : ''}
      </li>`;
  }).join('');

  const cartsHtml = state.carts ? `
    <section class="card">
      <h2>Your carts</h2>
      ${state.carts.map(c => `
        <div class="cart ${c.status}">
          <div class="row between"><b>${esc(c.store.name)}</b><span class="small">${c.status === 'working' ? 'Creating…' : c.status === 'ok' ? 'Ready' : 'Failed'}</span></div>
          ${c.message ? `<p class="small">${esc(c.message)}</p>` : ''}
          ${c.url ? `<a class="btn primary full" href="${esc(c.url)}" target="_blank" rel="noopener">Open cart to review &amp; check out ↗</a>` : ''}
        </div>`).join('')}
      <p class="muted small">Nothing has been purchased. Each order is placed only when you check out in that store's app or site.</p>
    </section>` : '';

  return `${planHtml}
    <section class="card">
      <div class="row between"><h2>Items</h2><span class="muted small">tap to swap products</span></div>
      <ul class="results">${itemRows}</ul>
      <div class="total-row"><span>Total${plan?.fees ? ` (incl. ${money(plan.fees)} fees)` : ''}</span><b>${money(plan?.total)}</b></div>
      <button class="btn primary full" data-act="carts" ${plan?.lines.length ? '' : 'disabled'}>Build cart${plan && plan.storeIds.length > 1 ? 's' : ''} for this plan</button>
      <p class="muted small center">Builds carts only. You review and check out yourself.</p>
    </section>
    ${cartsHtml}`;
}

function renderSettings() {
  const k = settings.kroger;
  const priced = allStores(settings).filter(s => s.priced);
  const needsWorker = !settings.workerUrl;
  return `
    <section class="card">
      <h2>Stores &amp; fees</h2>
      <label class="switch"><input type="checkbox" data-set="demo" ${settings.demo ? 'checked' : ''}><span>Demo stores (made-up prices)</span></label>
      ${priced.map(s => `
        <div class="row between fee-row"><span>${esc(s.name)}</span>
          <label class="fee">Fee $<input type="number" inputmode="decimal" step="0.01" min="0" data-fee="${esc(s.id)}" value="${fee(s)}"></label></div>`).join('') || '<p class="muted small">No stores connected yet.</p>'}
      <p class="muted small">Delivery or service fee is added once per store you use. The mix-and-match plan takes it into account.</p>
    </section>

    <section class="card">
      <h2>Backend</h2>
      <p class="muted small">Kroger and Instacart need your small backend (see README). It keeps your API keys off the phone.</p>
      <label class="field">Backend URL<input type="url" data-set="workerUrl" placeholder="https://grocery-api.you.workers.dev" value="${esc(settings.workerUrl)}"></label>
      <label class="field">App key (optional)<input type="password" data-set="appKey" value="${esc(settings.appKey)}" autocomplete="off"></label>
    </section>

    <section class="card">
      <h2>Kroger family</h2>
      <p class="muted small">Kroger, Ralphs, Fred Meyer, King Soopers, Fry's, Smith's, Harris Teeter and more. Real store prices, and adds items to your real cart.</p>
      <label class="switch"><input type="checkbox" data-set="kroger.enabled" ${k.enabled ? 'checked' : ''}><span>Use Kroger</span></label>
      ${k.enabled ? `
        <div class="row">
          <input type="text" inputmode="numeric" maxlength="5" placeholder="ZIP" data-set="zip" value="${esc(settings.zip)}" class="zip">
          <button class="btn secondary" data-act="kroger-find" ${needsWorker ? 'disabled' : ''}>Find stores</button>
        </div>
        ${state.krogerLocations.length ? `<select data-act="kroger-loc">
          <option value="">Choose a store…</option>
          ${state.krogerLocations.map(l => `<option value="${esc(l.locationId)}" ${l.locationId === k.locationId ? 'selected' : ''}>${esc(l.label)}</option>`).join('')}
        </select>` : ''}
        <p class="small">Store: <b>${esc(k.locationName || 'none selected')}</b></p>
        <label class="field">Order type<select data-set="kroger.modality">
          <option value="PICKUP" ${k.modality === 'PICKUP' ? 'selected' : ''}>Pickup</option>
          <option value="DELIVERY" ${k.modality === 'DELIVERY' ? 'selected' : ''}>Delivery</option></select></label>
        ${k.token || k.refresh
          ? '<p class="small ok">✓ Kroger account connected</p><button class="btn ghost" data-act="kroger-logout">Disconnect</button>'
          : `<button class="btn secondary full" data-act="kroger-login" ${needsWorker ? 'disabled' : ''}>Connect Kroger account (for cart)</button>`}
      ` : ''}
    </section>

    <section class="card">
      <h2>Instacart</h2>
      <p class="muted small">Sends your list to Instacart. You pick the store there, see prices, and check out. Instacart doesn't share prices, so it can't be compared here.</p>
      <label class="switch"><input type="checkbox" data-set="instacart.enabled" ${settings.instacart.enabled ? 'checked' : ''}><span>Use Instacart</span></label>
    </section>

    <section class="card">
      <h2>Shopify stores</h2>
      <p class="muted small">Any grocery shop built on Shopify. You need the store's Storefront API token, so this suits stores you own or partner with.</p>
      ${settings.shopify.map((s, i) => `
        <div class="row between shop-row"><span><b>${esc(s.name || s.domain)}</b><br><span class="muted small">${esc(s.domain)}</span></span>
        <button class="icon-btn" data-act="shop-remove" data-i="${i}" aria-label="Remove">✕</button></div>`).join('')}
      <details><summary>Add a Shopify store</summary>
        <label class="field">Name<input id="shop-name" placeholder="Local Farm Co-op"></label>
        <label class="field">Store domain<input id="shop-domain" placeholder="your-store.myshopify.com" autocapitalize="off"></label>
        <label class="field">Storefront access token<input id="shop-token" autocomplete="off" autocapitalize="off"></label>
        <button class="btn secondary full" data-act="shop-add">Add store</button>
      </details>
    </section>
    <p class="muted small center">Settings and tokens stay on this phone.</p>`;
}

// ---------- events ----------

const SAMPLE = `2 lb chicken breast
1 dozen eggs
1 gallon milk
3 cans black beans
2 avocados
1 bunch cilantro
1 lb rice
shredded cheese
tortillas
2 limes`;

document.addEventListener('click', async e => {
  const el = e.target.closest('[data-act], .tabs button');
  if (!el) return;
  if (el.matches('.tabs button')) { state.tab = el.dataset.tab; render(); window.scrollTo(0, 0); return; }
  const act = el.dataset.act;
  if (el.tagName === 'INPUT' || el.tagName === 'SELECT') return;     // handled by change
  switch (act) {
    case 'tab': state.tab = el.dataset.tab; render(); break;
    case 'parse': parseInput(); break;
    case 'sample': $('#list-input').value = SAMPLE; parseInput(); break;
    case 'remove':
      state.items = state.items.filter(i => i.id !== el.dataset.id); save('gc.items', state.items); render(); break;
    case 'qty': {
      const item = state.items.find(i => i.id === el.dataset.id);
      item.buyQty = Math.max(1, buyCount(item) + Number(el.dataset.d));
      save('gc.items', state.items); render(); break;
    }
    case 'find': if ($('#list-input') && $('#list-input').value !== state.parsedText) parseInput(); findPrices(); break;
    case 'plan': state.planKey = el.dataset.key; state.carts = null; render(); break;
    case 'expand': state.expanded = state.expanded === el.dataset.id ? null : el.dataset.id; render(); break;
    case 'carts': createCarts(); break;
    case 'instacart-all': sendToInstacart(state.items); break;
    case 'kroger-find': {
      el.disabled = true;
      try {
        const res = await workerFetch(settings, `/kroger/locations?zip=${encodeURIComponent(settings.zip)}`);
        state.krogerLocations = (res.data || []).map(l => ({
          locationId: l.locationId,
          label: `${l.name} – ${l.address?.addressLine1 || ''}, ${l.address?.city || ''}`,
        }));
        if (!state.krogerLocations.length) toast('No stores found near that ZIP', true);
      } catch (err) { toast(err.message, true); }
      render(); break;
    }
    case 'kroger-login':
      location.href = `${settings.workerUrl.replace(/\/+$/, '')}/kroger/login`; break;
    case 'kroger-logout':
      Object.assign(settings.kroger, { token: '', refresh: '', expires: 0 }); saveSettings(); render(); break;
    case 'shop-add': {
      const domain = $('#shop-domain').value.trim(); const token = $('#shop-token').value.trim();
      if (!domain || !token) return toast('Domain and token are required', true);
      settings.shopify.push({ name: $('#shop-name').value.trim(), domain, token }); saveSettings(); render(); break;
    }
    case 'shop-remove': settings.shopify.splice(Number(el.dataset.i), 1); saveSettings(); render(); break;
  }
});

document.addEventListener('change', e => {
  const el = e.target;
  if (el.dataset.act === 'pick') {
    (state.picks[el.dataset.item] ||= {})[el.dataset.store] = el.dataset.offer;
    state.carts = null; render(); return;
  }
  if (el.dataset.act === 'kroger-loc') {
    const loc = state.krogerLocations.find(l => l.locationId === el.value);
    settings.kroger.locationId = el.value; settings.kroger.locationName = loc ? loc.label : '';
    saveSettings(); render(); return;
  }
  if (el.dataset.fee) { settings.fees[el.dataset.fee] = Math.max(0, Number(el.value) || 0); saveSettings(); return; }
  if (el.dataset.set) {
    const path = el.dataset.set.split('.');
    const target = path.length > 1 ? settings[path[0]] : settings;
    target[path[path.length - 1]] = el.type === 'checkbox' ? el.checked : el.value.trim();
    saveSettings();
    if (el.type === 'checkbox' || el.tagName === 'SELECT') render();
  }
});

document.addEventListener('input', e => {
  if (e.target.id === 'list-input') { state.listText = e.target.value; save('gc.listText', state.listText); }
});

render();

if ('serviceWorker' in navigator && location.protocol === 'https:') {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}
