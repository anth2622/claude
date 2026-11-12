// SEC EDGAR: 8-K filings with Item 5.02 ("Departure of Directors or Certain Officers; Election of
// Directors; Appointment of Certain Officers") from insurance companies, found by SIC code.
import { stripHtml } from './rss.mjs';

const sleep = ms => new Promise(r => setTimeout(r, ms));
const day = d => d.toISOString().slice(0, 10);

export async function fetchSecMoves({ sicCodes, lookbackDays, userAgent, maxDocs = 150, log = console.log }) {
  const headers = { 'User-Agent': userAgent, 'Accept-Encoding': 'gzip, deflate' };
  const end = new Date();
  const start = new Date(end.getTime() - lookbackDays * 864e5);
  const hits = new Map(); // adsh -> best hit

  for (let from = 0; from < 2000; from += 100) {
    const url = `https://efts.sec.gov/LATEST/search-index?q=${encodeURIComponent('"Item 5.02"')}` +
      `&forms=8-K&dateRange=custom&startdt=${day(start)}&enddt=${day(end)}&from=${from}`;
    const res = await fetch(url, { headers, signal: AbortSignal.timeout(30000) });
    if (!res.ok) throw new Error(`EDGAR search HTTP ${res.status}`);
    const json = await res.json();
    const page = json?.hits?.hits || [];
    for (const h of page) {
      const s = h._source || {};
      const sic = (s.sics || []).find(c => sicCodes[c]);
      if (!sic) continue;
      if (s.items && !s.items.includes('5.02')) continue;
      const [adsh, file] = String(h._id).split(':');
      const prev = hits.get(adsh);
      // Prefer the 8-K body (it has the Item 5.02 text) over exhibits.
      const isMain = /^8-K/i.test(s.file_type || '');
      if (!prev || (isMain && !prev.isMain)) hits.set(adsh, { adsh, file, isMain, sic, src: s });
    }
    const total = json?.hits?.total?.value ?? 0;
    if (!page.length || from + 100 >= total) break;
    await sleep(200);
  }
  log(`  SEC: ${hits.size} insurance 8-K Item 5.02 filings in the last ${lookbackDays} days`);

  const out = [];
  for (const h of [...hits.values()].slice(0, maxDocs)) {
    const cik = String(parseInt(h.src.ciks?.[0] ?? '0', 10));
    const folder = h.adsh.replace(/-/g, '');
    const docUrl = `https://www.sec.gov/Archives/edgar/data/${cik}/${folder}/${h.file}`;
    const indexUrl = `https://www.sec.gov/Archives/edgar/data/${cik}/${folder}/${h.adsh}-index.htm`;
    const company = (h.src.display_names?.[0] || '').replace(/\s*\(.*$/, '').trim();
    let section = '';
    try {
      await sleep(150); // SEC asks for <= 10 requests/second
      const res = await fetch(docUrl, { headers, signal: AbortSignal.timeout(30000) });
      if (res.ok) section = itemSection(stripHtml(await res.text()));
    } catch (e) { log(`  SEC doc failed ${docUrl}: ${e.message}`); }
    out.push({
      adsh: h.adsh, company, sic: h.sic, date: h.src.file_date, url: indexUrl, docUrl,
      title: `${titleCase(company)}: SEC 8-K, Item 5.02`,
      summary: section,
    });
  }
  return out;
}

export function itemSection(text) {
  // Skip the cover page; take the body of Item 5.02 up to the next item / signature.
  const re = /Item\s*5\.02\.?/gi;
  let m, best = '';
  while ((m = re.exec(text))) {
    const rest = text.slice(m.index + m[0].length);
    const stop = rest.search(/Item\s*\d\.\d\d|SIGNATURES?\b/i);
    const body = (stop >= 0 ? rest.slice(0, stop) : rest.slice(0, 3000))
      .replace(/^[\s.:–—-]*(?:\(?[a-f]\)\s*)?(?:Departure of Directors[\s\S]{0,250}?Officers\.)?/i, '').trim();
    if (body.length > best.length) best = body;
    if (best.length > 200) break;
  }
  // First two or three sentences are enough for a summary.
  const sentences = best.match(/[^.!?]+(?:[.!?](?!\d)|$)/g) || [];
  let s = '';
  for (const x of sentences) { if ((s + x).length > 600) break; s += x; }
  return (s || best.slice(0, 600)).trim();
}

const titleCase = s => /[a-z]/.test(s) ? s : s.toLowerCase().replace(/\b([a-z])/g, c => c.toUpperCase())
  .replace(/\b(Inc|Corp|Co|Ltd|Plc|Llc|Lp|Sa|Ag|Nv|Se)\b/g, w => w === 'Plc' || w === 'Llc' ? w.toUpperCase() : w);
export { titleCase };
