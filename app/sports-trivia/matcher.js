// Decides whether a spoken or typed response matches a clue's answer.
// Forgiving on purpose: speech recognition mangles names, so it accepts last names,
// close spellings (edit distance) and sound-alike spellings.

const STOP = new Set(['jr', 'sr', 'ii', 'iii', 'junior', 'the']);
const LEAD = /^(?:(?:um+|uh+|er+|okay|ok|so|i think|i think its|is it|its|it is)\s+)*(?:(?:who|what|whos|whats)\s+(?:is|are|was|were)?\s*)?/;

export function norm(s) {
  return String(s || '')
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[’'`.]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

export function stripLead(s) {
  return s.replace(LEAD, '').replace(/\s*\b(right|maybe)$/, '').trim();
}

export function isQuestionForm(raw) {
  const n = norm(raw);
  return /^(who|what|whos|whats)\b/.test(n) || /\?\s*$/.test(String(raw));
}

function dropStops(s) { return s.split(' ').filter(w => w && !STOP.has(w)).join(' '); }

export function acceptedAnswers(clue) {
  const full = dropStops(norm(clue.a));
  const set = new Set([full]);
  const parts = full.split(' ');
  if (clue.last !== false && parts.length > 1 && parts.at(-1).length >= 3) set.add(parts.at(-1));
  for (const x of clue.acc || []) set.add(dropStops(norm(x)));
  set.delete('');
  return [...set];
}

function lev(a, b) {
  if (a === b) return 0;
  const m = a.length, n = b.length;
  if (!m || !n) return m || n;
  let prev = Array.from({ length: n + 1 }, (_, i) => i);
  for (let i = 1; i <= m; i++) {
    const cur = [i];
    for (let j = 1; j <= n; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    prev = cur;
  }
  return prev[n];
}
const sim = (a, b) => 1 - lev(a, b) / Math.max(a.length, b.length);

// Rough sound-alike key: "jokic" and "yokich" both become "yk".
export function phonetic(s) {
  return s.replace(/\s+/g, '')
    .replace(/ph/g, 'f').replace(/gh/g, 'g').replace(/sch/g, 'sh').replace(/tch/g, 'ch')
    .replace(/ck|q/g, 'k').replace(/c(?=[eiy])/g, 's').replace(/ch/g, 'k').replace(/c/g, 'k')
    .replace(/dj|j/g, 'y').replace(/[zx]/g, 's').replace(/w/g, 'v').replace(/kh/g, 'k')
    .replace(/(.)\1+/g, '$1')
    .replace(/(?!^)[aeiouyh]/g, '')
    .replace(/(.)\1+/g, '$1');
}

function windows(words, size) {
  const out = [];
  for (let i = 0; i + size <= words.length; i++) out.push(words.slice(i, i + size).join(' '));
  return out;
}

function matchOne(resp, acc) {
  if (!resp) return false;
  if (resp === acc) return true;
  const words = resp.split(' ');
  const size = acc.split(' ').length;
  const cands = new Set([resp, ...windows(words, size), ...windows(words, size + 1)]);
  for (const c of cands) {
    if (c === acc) return true;
    const threshold = acc.length <= 4 ? 1 : acc.length <= 8 ? 0.75 : 0.8;
    if (acc.length > 3 && sim(c, acc) >= threshold) return true;
    const pa = phonetic(acc);
    if (acc.length >= 5 && pa.length >= 3 && phonetic(c) === pa) return true;
  }
  return false;
}

// responses: one string or several speech-recognition alternatives.
export function isCorrect(responses, clue) {
  const list = (Array.isArray(responses) ? responses : [responses])
    .map(r => dropStops(stripLead(norm(r)))).filter(Boolean);
  const accs = acceptedAnswers(clue);
  return list.some(r => accs.some(a => matchOne(r, a)));
}
