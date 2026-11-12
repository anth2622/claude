// Run: node --test insurance-moves/test
import test from 'node:test';
import assert from 'node:assert/strict';
import { classify } from '../lib/classify.js';
import { parseFeed } from '../collector/rss.mjs';
import { itemSection } from '../collector/sec.mjs';
import { buildMove, merge } from '../collector/collect.mjs';

// All names below are fictional.
const cases = [
  ['Northbridge Mutual appoints Jane Doe as chief underwriting officer',
    { type: 'Appointment', level: 'C-suite', person: 'Jane Doe', company: 'Northbridge Mutual', func: 'Underwriting' }],
  ['Jane Doe joins Harbor Re as head of casualty treaty',
    { type: 'Appointment', level: 'Head / Director', person: 'Jane Doe', company: 'Harbor Re' }],
  ['Acme Insurance CEO John Smith to retire; Mary Lee named successor',
    { type: 'Succession', level: 'C-suite', person: 'John Smith', company: 'Acme Insurance' }],
  ["Contoso Specialty's CFO Peter Brown steps down",
    { type: 'Departure', person: 'Peter Brown', company: 'Contoso Specialty' }],
  ['Fabrikam promotes Alex Kim to executive vice president, claims',
    { type: 'Promotion', level: 'EVP / SVP', role: 'Executive Vice President, Claims', func: 'Claims' }],
  ['Former Marsh executive Sam Ortiz joins Example MGA as managing director',
    { level: 'President / MD', person: 'Sam Ortiz', company: 'Example MGA' }],
  ['Tailspin Insurance elects Ruth Park to board of directors', { level: 'Board', person: 'Ruth Park' }],
  ['Woodgrove Re hires Chris Wong as SVP, cyber reinsurance', { role: 'SVP, Cyber Reinsurance', sectors: ['Reinsurance'] }],
  ['Example Brokerage names new president of US retail', { company: 'Example Brokerage', person: null }],
];
for (const [title, want] of cases) {
  test(`classify: ${title}`, () => {
    const got = classify({ title });
    assert.equal(got.isMove, true);
    for (const [k, v] of Object.entries(want)) assert.deepEqual(got[k], v, k);
  });
}

test('not a move: earnings and vice president alone', () => {
  assert.equal(classify({ title: 'Q3 earnings beat estimates at Example Life' }).isMove, false);
  assert.equal(classify({ title: 'Vice president of the United States visits flood zone' }).isMove, false);
});

test('requireInsurance drops non-insurance moves', () => {
  assert.equal(classify({ title: 'Big Bank names Jo Day CFO' }, { requireInsurance: true }).isMove, false);
  assert.equal(classify({ title: 'Big Insurer names Jo Day CFO' }, { requireInsurance: true }).isMove, true);
});

test('SEC Item 5.02 text', () => {
  const doc = 'UNITED STATES SECURITIES ... Item 5.02 Departure of Directors or Certain Officers ☐ ... ' +
    'Item 5.02. Departure of Directors or Certain Officers; Election of Directors; Appointment of Certain Officers; Compensatory Arrangements of Certain Officers. ' +
    'On November 2, 2026, Robert Gray, the Company\'s Chief Operating Officer, notified the Company of his intention to resign, effective December 31, 2026. ' +
    'Mr. Gray\'s decision was not the result of any disagreement. Item 9.01 Financial Statements and Exhibits.';
  const s = itemSection(doc);
  assert.match(s, /^On November 2, 2026, Robert Gray/);
  const m = buildMove({ key: 'x', title: 'Example Holdings: SEC 8-K, Item 5.02', summary: s, link: 'https://sec.gov/x', date: '2026-11-03' },
    { source: 'SEC 8-K', sourceType: 'sec', defaults: { company: 'Example Holdings', sectors: ['P&C'] } });
  assert.equal(m.type, 'Departure');
  assert.equal(m.level, 'C-suite');
  assert.equal(m.person, 'Robert Gray');
  assert.equal(m.company, 'Example Holdings');
  assert.deepEqual(m.sectors, ['P&C']);
});

test('RSS + Google News publisher suffix', () => {
  const xml = `<rss><channel><item><title><![CDATA[Northbridge Mutual appoints Jane Doe as CFO - Example Wire]]></title>
    <link>https://news.google.com/rss/articles/abc</link><pubDate>Tue, 10 Nov 2026 14:00:00 GMT</pubDate>
    <description>&lt;a href="x"&gt;Northbridge&lt;/a&gt; news</description><source url="https://x">Example Wire</source></item></channel></rss>`;
  const [item] = parseFeed(xml);
  const m = buildMove(item, { source: 'Google News', sourceType: 'news' });
  assert.equal(m.title, 'Northbridge Mutual appoints Jane Doe as CFO');
  assert.equal(m.source, 'Example Wire');
  assert.equal(m.date, '2026-11-10');
});

test('Atom feed', () => {
  const [e] = parseFeed('<feed><entry><title>A &amp; B names Jo Ray president</title><link rel="alternate" href="https://e/1"/><updated>2026-11-01T00:00:00Z</updated></entry></feed>');
  assert.equal(e.title, 'A & B names Jo Ray president');
  assert.equal(e.link, 'https://e/1');
});

test('merge de-duplicates the same person + company across sources', () => {
  const a = { id: '1', date: '2026-11-01', person: 'Jane Doe', company: 'Northbridge Mutual', url: 'u1', links: [{ source: 'A', url: 'u1' }] };
  const b = { id: '2', date: '2026-11-03', person: 'Jane  Doe', company: 'Northbridge mutual', url: 'u2', links: [{ source: 'B', url: 'u2' }] };
  const c = { id: '3', date: '2026-11-02', person: 'Sam Ortiz', company: 'Example MGA', url: 'u3', links: [] };
  const { moves, added } = merge([a], [b, c, { ...a }], new Date('2026-11-12'));
  assert.equal(added, 1);
  assert.equal(moves.length, 2);
  assert.equal(moves.find(m => m.id === '1').links.length, 2);
});
