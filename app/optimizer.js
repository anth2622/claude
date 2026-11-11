// Works out the cheapest way to buy a list: one store vs. mixing stores.
//
// offers[itemId][storeId] = [{ id, title, price, ... }] sorted cheapest first
// picks[itemId][storeId]  = offer id the user chose instead of the cheapest
// fees[storeId]           = delivery/service fee added once if the store is used

import { buyCount } from './parser.js';

export function chosenOffer(itemId, storeId, offers, picks) {
  const list = offers[itemId]?.[storeId];
  if (!list || !list.length) return null;
  const pickId = picks[itemId]?.[storeId];
  return (pickId && list.find(o => o.id === pickId)) || list[0];
}

function evaluate(items, storeIds, offers, picks, fees) {
  const lines = [];
  const missing = [];
  const used = new Set();
  let subtotal = 0;
  for (const item of items) {
    let best = null;
    for (const s of storeIds) {
      const offer = chosenOffer(item.id, s, offers, picks);
      if (!offer) continue;
      const cost = offer.price * buyCount(item);
      if (!best || cost < best.cost) best = { item, storeId: s, offer, cost };
    }
    if (best) { lines.push(best); used.add(best.storeId); subtotal += best.cost; } else missing.push(item);
  }
  const feeTotal = [...used].reduce((sum, s) => sum + (Number(fees[s]) || 0), 0);
  return { storeIds: [...used], lines, missing, subtotal, fees: feeTotal, total: subtotal + feeTotal };
}

const better = (a, b) =>
  a.missing.length !== b.missing.length ? a.missing.length < b.missing.length : a.total < b.total - 0.005;

export function computePlans(items, pricedStoreIds, offers, picks, fees) {
  const single = pricedStoreIds
    .map(s => ({ ...evaluate(items, [s], offers, picks, fees), storeIds: [s], kind: 'single' }))
    .sort((a, b) => (better(a, b) ? -1 : better(b, a) ? 1 : 0));

  // Try every combination of stores (fees make "cheapest per item" not always cheapest overall).
  let mix = null;
  const n = Math.min(pricedStoreIds.length, 12);
  for (let mask = 1; mask < (1 << n); mask++) {
    const subset = pricedStoreIds.filter((_, i) => mask & (1 << i));
    const plan = evaluate(items, subset, offers, picks, fees);
    if (!mix || better(plan, mix)) mix = plan;
  }
  if (mix) mix.kind = 'mix';

  const bestSingle = single[0] || null;
  const savings = mix && bestSingle && mix.missing.length === bestSingle.missing.length
    ? Math.max(0, bestSingle.total - mix.total) : 0;

  return { single, mix, bestSingle, savings };
}
