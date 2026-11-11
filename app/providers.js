// Store connectors. Each store gives the app:
//   search(item)        -> offers [{ id, title, price, size, image, ... }]
//   createCart(lines)   -> { url, message }  (a cart/checkout link; never places the order)

import { buyCount } from './parser.js';

// ---------- helpers ----------

function workerFetch(settings, path, opts = {}) {
  if (!settings.workerUrl) throw new Error('Set your backend URL in Settings first.');
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (settings.appKey) headers['X-App-Key'] = settings.appKey;
  return fetch(settings.workerUrl.replace(/\/+$/, '') + path, { ...opts, headers }).then(async r => {
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || `Backend error ${r.status}`);
    return body;
  });
}

// Keep results whose title mentions every main word of the ingredient.
function relevant(itemName, offers) {
  const words = itemName.toLowerCase().replace(/[^a-z0-9 ]/g, ' ').split(' ')
    .filter(w => w.length > 2).map(w => w.replace(/(es|s)$/, ''));
  if (!words.length) return offers;
  const keep = offers.filter(o => {
    const t = o.title.toLowerCase();
    return words.every(w => t.includes(w));
  });
  return keep.length ? keep : offers;
}

const byPrice = (a, b) => a.price - b.price;

// ---------- Demo stores (made-up prices so you can try the app) ----------

const DEMO_STORES = [
  { id: 'demo:freshmart', name: 'FreshMart (demo)', mult: 1.0, defaultFee: 3.99 },
  { id: 'demo:valuegrocer', name: 'ValueGrocer (demo)', mult: 0.88, defaultFee: 5.99 },
  { id: 'demo:corner', name: 'Corner Market (demo)', mult: 1.12, defaultFee: 0 },
];

function hash(str) {
  let h = 2166136261;
  for (const c of str) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); }
  return (h >>> 0) / 4294967296;
}

const demo = {
  stores: () => DEMO_STORES.map(s => ({ id: s.id, name: s.name, provider: 'demo', priced: true, defaultFee: s.defaultFee })),
  async search(item, store) {
    const s = DEMO_STORES.find(d => d.id === store.id);
    await new Promise(r => setTimeout(r, 150 + hash(store.id + item.name) * 350));
    if (hash(store.id + item.name + 'stock') < 0.08) return [];      // occasionally out of stock
    const base = 1.5 + hash(item.name) * 7;
    const name = item.name.replace(/\b\w/g, c => c.toUpperCase());
    return [
      { id: 'store', title: `${s.name.split(' ')[0]} ${name}`, brand: 'Store brand' },
      { id: 'name', title: `Organic ${name}`, brand: 'Name brand' },
      { id: 'prem', title: `Premium ${name}`, brand: 'Premium' },
    ].map((o, k) => ({
      ...o,
      price: Math.round(base * s.mult * (1 + k * 0.35) * (0.9 + hash(store.id + item.name + k) * 0.25) * 100) / 100,
      size: '1 ct',
    })).sort(byPrice);
  },
  async createCart(store, lines) {
    return {
      url: null,
      message: `Demo store: ${lines.length} item(s) would be added to your ${store.name} cart. Connect a real store in Settings.`,
    };
  },
};

// ---------- Kroger family (Kroger, Ralphs, Fred Meyer, King Soopers, Fry's, Smith's, ...) ----------

const kroger = {
  stores(settings) {
    const k = settings.kroger;
    if (!k.enabled || !k.locationId) return [];
    return [{ id: 'kroger', name: k.locationName || 'Kroger', provider: 'kroger', priced: true, defaultFee: 0 }];
  },
  async search(item, store, settings) {
    const q = new URLSearchParams({ term: item.name, locationId: settings.kroger.locationId, limit: '12' });
    const res = await workerFetch(settings, `/kroger/products?${q}`);
    const offers = (res.data || []).map(p => {
      const it = (p.items || [])[0] || {};
      const regular = it.price?.regular || 0;
      const promo = it.price?.promo || 0;
      const price = promo > 0 && promo < regular ? promo : regular;
      const img = (p.images || []).find(i => i.perspective === 'front') || (p.images || [])[0];
      const thumb = img?.sizes?.find(s => s.size === 'thumbnail') || img?.sizes?.[0];
      return {
        id: p.upc || p.productId, upc: p.upc || p.productId, title: p.description, brand: p.brand,
        price, regular, onSale: price < regular, size: it.size, image: thumb?.url,
      };
    }).filter(o => o.price > 0);
    return relevant(item.name, offers).sort(byPrice);
  },
  async createCart(store, lines, settings) {
    const k = settings.kroger;
    if (!k.token) throw new Error('Connect your Kroger account in Settings so items can go into your cart.');
    const items = lines.map(l => ({ upc: l.offer.upc, quantity: buyCount(l.item), modality: k.modality || 'PICKUP' }));
    await workerFetch(settings, '/kroger/cart', {
      method: 'POST', body: JSON.stringify({ items }), headers: { Authorization: `Bearer ${k.token}` },
    });
    return { url: 'https://www.kroger.com/cart', message: `Added ${items.length} item(s) to your Kroger-family cart. Review and check out there.` };
  },
};

// ---------- Shopify stores (Storefront API, called straight from the phone) ----------

const SHOPIFY_SEARCH = `query($q: String!) {
  products(first: 10, query: $q) { edges { node {
    title vendor featuredImage { url }
    variants(first: 5) { edges { node { id title availableForSale price { amount currencyCode } } } }
  } } }
}`;

const SHOPIFY_CART = `mutation($lines: [CartLineInput!]!) {
  cartCreate(input: { lines: $lines }) { cart { checkoutUrl } userErrors { message } }
}`;

async function shopifyGql(shop, query, variables) {
  const domain = shop.domain.replace(/^https?:\/\//, '').replace(/\/.*$/, '');
  const version = shop.apiVersion || '2025-07';
  const r = await fetch(`https://${domain}/api/${version}/graphql.json`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Shopify-Storefront-Access-Token': shop.token },
    body: JSON.stringify({ query, variables }),
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok || body.errors) throw new Error(body.errors?.[0]?.message || `Shopify error ${r.status}`);
  return body.data;
}

const shopify = {
  stores: settings => settings.shopify.map(s => ({
    id: `shopify:${s.domain}`, name: s.name || s.domain, provider: 'shopify', priced: true, defaultFee: 0, shop: s,
  })),
  async search(item, store) {
    const data = await shopifyGql(store.shop, SHOPIFY_SEARCH, { q: item.name });
    const offers = [];
    for (const { node: p } of data.products.edges) {
      for (const { node: v } of p.variants.edges) {
        if (!v.availableForSale) continue;
        offers.push({
          id: v.id, variantId: v.id, price: Number(v.price.amount), brand: p.vendor,
          title: v.title && v.title !== 'Default Title' ? `${p.title} – ${v.title}` : p.title,
          image: p.featuredImage?.url,
        });
      }
    }
    return relevant(item.name, offers).sort(byPrice);
  },
  async createCart(store, lines) {
    const data = await shopifyGql(store.shop, SHOPIFY_CART, {
      lines: lines.map(l => ({ merchandiseId: l.offer.variantId, quantity: buyCount(l.item) })),
    });
    const res = data.cartCreate;
    if (res.userErrors?.length) throw new Error(res.userErrors[0].message);
    return { url: res.cart.checkoutUrl, message: 'Cart created. The link opens checkout, and nothing is ordered until you pay there.' };
  },
};

// ---------- Instacart (Developer Platform: builds a list you open in Instacart; no prices) ----------

const INSTACART_UNITS = {
  lb: 'pound', oz: 'ounce', g: 'gram', kg: 'kilogram', cup: 'cup', tbsp: 'tablespoon', tsp: 'teaspoon',
  gallon: 'gallon', quart: 'quart', pint: 'pint', liter: 'liter', ml: 'milliliter', can: 'can',
  package: 'package', bunch: 'bunch', head: 'head', each: 'each', clove: 'clove',
};

const instacart = {
  stores: settings => (settings.instacart.enabled
    ? [{ id: 'instacart', name: 'Instacart', provider: 'instacart', priced: false, defaultFee: 0 }] : []),
  async createCart(store, items, settings, title) {
    const line_items = items.map(item => {
      let qty = item.quantity;
      let unit = INSTACART_UNITS[item.unit] || 'each';
      if (item.unit === 'dozen') { qty *= 12; unit = 'each'; }
      return { name: item.name, display_text: item.raw || item.name, line_item_measurements: [{ quantity: qty, unit }] };
    });
    const res = await workerFetch(settings, '/instacart/list', {
      method: 'POST', body: JSON.stringify({ title: title || 'Grocery list', line_items }),
    });
    return { url: res.url, message: 'Opens Instacart with your list. Pick a store, review the items, and check out yourself.' };
  },
};

export const PROVIDERS = { demo, kroger, shopify, instacart };

export function allStores(settings) {
  return [
    ...(settings.demo ? demo.stores() : []),
    ...kroger.stores(settings),
    ...shopify.stores(settings),
    ...instacart.stores(settings),
  ];
}

export { workerFetch };
