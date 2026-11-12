#!/usr/bin/env node
// Collects insurance-industry management moves from news feeds and SEC filings into ../data/moves.json.
// Run: node insurance-moves/collector/collect.mjs   (Node 18+; no npm install needed)
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { classify } from '../lib/classify.js';
import { parseFeed } from './rss.mjs';
import { fetchSecMoves, titleCase } from './sec.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const DATA = process.env.MOVES_FILE || join(here, '..', 'data', 'moves.json');
const KEEP_DAYS = Number(process.env.KEEP_DAYS || 730);
const MAX_ITEMS = 6000;
const UA = process.env.SEC_USER_AGENT ||
  `insurance-moves-tracker/1.0 (${process.env.GITHUB_REPOSITORY ? 'github.com/' + process.env.GITHUB_REPOSITORY : 'personal use'})`;

const hash = s => createHash('sha1').update(s).digest('hex').slice(0, 12);
const norm = s => (s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
const isoDay = d => (d && !isNaN(d) ? d : new Date()).toISOString().slice(0, 10);

export function buildMove(raw, { source, sourceType, requireInsurance = false, defaults = {} }) {
  let title = raw.title || '';
  let publisher = raw.publisher || '';
  // Google News titles end with " - Publisher".
  if (/news\.google\./.test(raw.link || '')) {
    const m = title.match(/^(.*) [-–] ([^-–]{2,60})$/);
    if (m) { title = m[1]; publisher = publisher || m[2]; }
  }
  const c = classify({ title, summary: raw.summary }, { requireInsurance });
  if (!c.isMove) return null;
  return {
    id: hash(raw.key || raw.link || title),
    date: typeof raw.date === 'string' ? raw.date : isoDay(raw.date),
    title, summary: raw.summary || '', url: raw.link,
    source: publisher && sourceType === 'news' ? `${publisher}` : source,
    via: source, sourceType,
    person: c.person, company: defaults.company || c.company,
    role: c.role, level: c.level, type: c.type, func: c.func,
    sectors: c.sectors.length ? c.sectors : (defaults.sectors || []),
    links: [{ source: publisher || source, url: raw.link }],
  };
}

export function merge(existing, incoming, now = new Date()) {
  const byId = new Map(existing.map(m => [m.id, m]));
  const keyOf = m => (m.person && m.company ? `${norm(m.person)}|${norm(m.company)}` : null);
  const byKey = new Map();
  for (const m of existing) { const k = keyOf(m); if (k) byKey.set(k, m); }
  let added = 0;
  for (const m of incoming) {
    if (byId.has(m.id)) continue;
    const k = keyOf(m);
    const twin = k && byKey.get(k);
    if (twin && Math.abs(new Date(twin.date) - new Date(m.date)) < 21 * 864e5) {
      // Same person + company reported elsewhere: keep one record, list every source.
      if (!twin.links.some(l => l.url === m.url)) twin.links.push(...m.links);
      if (m.sourceType === 'sec' && !twin.links.some(l => /sec\.gov/.test(l.url))) twin.links.push(...m.links);
      continue;
    }
    m.collected = now.toISOString();
    byId.set(m.id, m); if (k) byKey.set(k, m);
    added++;
  }
  const cutoff = isoDay(new Date(now - KEEP_DAYS * 864e5));
  const moves = [...byId.values()].filter(m => m.date >= cutoff)
    .sort((a, b) => b.date.localeCompare(a.date) || (b.collected || '').localeCompare(a.collected || ''))
    .slice(0, MAX_ITEMS);
  return { moves, added };
}

async function main() {
  const config = JSON.parse(await readFile(process.env.SOURCES_FILE || join(here, 'sources.json'), 'utf8'));
  let data = { updated: null, sources: [], moves: [] };
  try { data = JSON.parse(await readFile(DATA, 'utf8')); } catch { /* first run */ }

  const incoming = [];
  const status = [];
  for (const feed of config.feeds.filter(f => f.enabled !== false)) {
    try {
      const res = await fetch(feed.url, { headers: { 'User-Agent': 'Mozilla/5.0 (compatible; insurance-moves-tracker/1.0)' }, signal: AbortSignal.timeout(30000) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const items = parseFeed(await res.text());
      const moves = items.map(i => buildMove(i, { source: feed.name, sourceType: 'news', requireInsurance: feed.requireInsurance }))
        .filter(Boolean);
      incoming.push(...moves);
      status.push({ name: feed.name, ok: true, items: items.length, moves: moves.length });
      console.log(`✓ ${feed.name}: ${items.length} items, ${moves.length} look like management moves`);
    } catch (e) {
      status.push({ name: feed.name, ok: false, error: e.message });
      console.log(`✗ ${feed.name}: ${e.message}`);
    }
  }

  if (config.sec?.enabled) {
    try {
      if (!process.env.SEC_USER_AGENT) console.log('  (tip: set SEC_USER_AGENT to "Your Name your@email" — SEC asks for contact info)');
      const lookbackDays = Number(process.env.SEC_LOOKBACK_DAYS || (data.moves.length ? 4 : 45));
      const filings = await fetchSecMoves({ sicCodes: config.sec.sicCodes, lookbackDays, userAgent: UA });
      const moves = filings.map(f => buildMove(
        { key: f.adsh, title: f.title, summary: f.summary, link: f.url, date: f.date },
        { source: 'SEC 8-K', sourceType: 'sec', defaults: { company: titleCase(f.company), sectors: [config.sec.sicCodes[f.sic]] } },
      )).filter(Boolean);
      // An Item 5.02 filing is an officer/director change even when the wording defeats the classifier,
      // except pure compensation updates, which classify() already filters out (no move verb).
      incoming.push(...moves);
      status.push({ name: 'SEC EDGAR 8-K Item 5.02', ok: true, items: filings.length, moves: moves.length });
      console.log(`✓ SEC: ${filings.length} filings, ${moves.length} officer/director moves`);
    } catch (e) {
      status.push({ name: 'SEC EDGAR 8-K Item 5.02', ok: false, error: e.message });
      console.log(`✗ SEC: ${e.message}`);
    }
  }

  const { moves, added } = merge(data.moves, incoming);
  const out = { updated: new Date().toISOString(), sources: status, moves };
  await writeFile(DATA, JSON.stringify(out, null, 1) + '\n');
  console.log(`Added ${added} new moves; ${moves.length} total.`);
  if (status.every(s => !s.ok)) process.exitCode = 1;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) main();
