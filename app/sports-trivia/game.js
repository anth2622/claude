import { CATEGORIES, FINALS } from './clues.js';
import { isCorrect, isQuestionForm } from './matcher.js';

// ---------- storage ----------
const load = (k, d) => { try { const v = JSON.parse(localStorage.getItem(k)); return v ?? d; } catch { return d; } };
const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } };

const settings = {
  players: ['Player 1'],
  length: 'full',       // 'full' = 2 rounds + final, 'quick' = 1 round + final
  readAloud: true,
  autoListen: true,
  questionForm: false,  // require "Who is ...?"
  seconds: 20,
  ...load('st.settings', {}),
};
const saveSettings = () => save('st.settings', settings);

let game = load('st.game', null);
const saveGame = () => save('st.game', game);
let best = load('st.best', 0);

// Per-screen UI state (not saved).
const ui = { screen: 'home', clue: null, phase: null, answerer: null, locked: [], msg: '', result: null, wager: null, transcript: '' };

// ---------- helpers ----------
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = n => (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString();
const shuffle = a => { const b = [...a]; for (let i = b.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [b[i], b[j]] = [b[j], b[i]]; } return b; };
const pick = a => a[Math.floor(Math.random() * a.length)];
const COLORS = ['#ffcc33', '#4fd1c5', '#f687b3', '#9ae6b4'];

function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), 2600);
}

// ---------- speech out ----------
const canSpeak = 'speechSynthesis' in window;
let voice = null;
function chooseVoice() {
  if (!canSpeak) return;
  const vs = speechSynthesis.getVoices().filter(v => v.lang?.startsWith('en'));
  voice = vs.find(v => /samantha|google us english|aaron|daniel|alex/i.test(v.name)) || vs[0] || null;
}
if (canSpeak) { chooseVoice(); speechSynthesis.onvoiceschanged = chooseVoice; }

function say(text) {
  return new Promise(resolve => {
    if (!canSpeak || !settings.readAloud) return resolve();
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    if (voice) u.voice = voice;
    u.rate = 1.03;
    let done = false;
    const finish = () => { if (!done) { done = true; resolve(); } };
    u.onend = finish; u.onerror = finish;
    // Safety net: some browsers never fire onend.
    setTimeout(finish, 1500 + text.length * 75);
    speechSynthesis.speak(u);
  });
}
const hush = () => { if (canSpeak) speechSynthesis.cancel(); };

// ---------- speech in ----------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null;
function stopListening() { if (rec) { try { rec.abort(); } catch { /* ignore */ } rec = null; } setMicState(false); }
function setMicState(on) { const b = $('#mic'); if (b) { b.classList.toggle('on', on); b.querySelector('.lbl').textContent = on ? 'Listening…' : 'Tap to speak'; } }

function listen(onFinal) {
  if (!SR) { toast('Voice answers are not supported in this browser. Type instead, or use your keyboard\'s mic.'); return; }
  hush();
  stopListening();
  const r = new SR();
  rec = r;
  r.lang = 'en-US'; r.interimResults = true; r.maxAlternatives = 5; r.continuous = false;
  let alts = [];
  r.onresult = e => {
    const res = e.results[e.results.length - 1];
    const text = [...e.results].map(x => x[0].transcript).join(' ');
    const input = $('#answer'); if (input) input.value = text;
    if (res.isFinal) alts = [...res].map(a => a.transcript);
  };
  r.onerror = e => {
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed') toast('Microphone blocked. Allow mic access in your browser settings, or type your answer.');
    else if (e.error === 'no-speech') toast('Didn\'t hear anything. Tap the mic and try again.');
  };
  r.onend = () => {
    if (rec !== r) return;
    rec = null; setMicState(false);
    const typed = $('#answer')?.value.trim();
    if (alts.length) onFinal(alts);
    else if (typed) onFinal([typed]);
  };
  try { r.start(); setMicState(true); } catch { setMicState(false); }
}

// ---------- timer ----------
let timer = null;
function startTimer(seconds, onDone) {
  stopTimer();
  const bar = $('#timebar'); if (!bar) return;
  const end = Date.now() + seconds * 1000;
  timer = setInterval(() => {
    const left = end - Date.now();
    const b = $('#timebar');
    if (b) b.style.width = `${Math.max(0, left / (seconds * 1000)) * 100}%`;
    if (left <= 0) { stopTimer(); onDone(); }
  }, 100);
}
function stopTimer() { clearInterval(timer); timer = null; }

// ---------- game setup ----------
function buildBoard(round) {
  const base = round === 1 ? 200 : 400;
  const cats = shuffle(CATEGORIES.filter(c => !game.usedCats.includes(c.name))).slice(0, 6);
  const board = cats.map(cat => {
    game.usedCats.push(cat.name);
    const clues = [1, 2, 3, 4, 5].map(l => {
      const atLevel = cat.clues.filter(c => c.l === l);
      const fresh = atLevel.filter(c => !game.usedAnswers.includes(c.a));
      const c = pick(fresh.length ? fresh : atLevel);
      game.usedAnswers.push(c.a);
      return { q: c.q, a: c.a, acc: c.acc, last: c.last, value: base * l, done: false, dd: false };
    });
    return { name: cat.name, clues };
  });
  // Daily Doubles hide in the harder rows.
  const spots = shuffle(board.flatMap((c, ci) => c.clues.map((_, ri) => [ci, ri]).filter(([, ri]) => ri >= 2)));
  const ddCount = round === 1 ? 1 : 2;
  const usedCols = new Set();
  for (const [ci, ri] of spots) {
    if (usedCols.size >= ddCount) break;
    if (usedCols.has(ci)) continue;
    usedCols.add(ci); board[ci].clues[ri].dd = true;
  }
  return board;
}

function newGame() {
  const names = settings.players.map((n, i) => n.trim() || `Player ${i + 1}`);
  game = { players: names.map(name => ({ name, score: 0 })), round: 1, picker: 0, usedCats: [], usedAnswers: [], board: null, final: null, length: settings.length };
  game.board = buildBoard(1);
  saveGame();
  go('board');
}

function roundDone() { return game.board.every(c => c.clues.every(x => x.done)); }

function nextRound() {
  if (game.round === 1 && game.length === 'full') {
    game.round = 2;
    // Lowest score picks first in round 2, like the TV show.
    game.picker = game.players.reduce((lo, p, i, a) => (p.score < a[lo].score ? i : lo), 0);
    game.board = buildBoard(2);
    saveGame();
    go('roundIntro');
  } else startFinal();
}

function startFinal() {
  const fresh = FINALS.filter(x => !game.usedAnswers.includes(x.a));
  const f = pick(fresh.length ? fresh : FINALS);
  game.round = 'final';
  game.final = { ...f, step: 'intro', turn: 0, wagers: [], answers: [], correct: [] };
  saveGame();
  go('final');
}

// ---------- navigation ----------
function go(screen) {
  stopTimer(); stopListening(); hush();
  ui.screen = screen;
  render();
  window.scrollTo(0, 0);
}

function render() {
  const view = $('#app');
  const screens = { home, board, roundIntro, dd, clue, final, end };
  view.innerHTML = screens[ui.screen]();
  view.dataset.screen = ui.screen;
  after[ui.screen]?.();
}

// ---------- screens ----------
const scoreBar = () => game.players.length < 1 ? '' : `
  <div class="scores">
    ${game.players.map((p, i) => `
      <div class="score ${i === game.picker && ui.screen === 'board' ? 'picking' : ''}" style="--c:${COLORS[i]}">
        <span class="pname">${esc(p.name)}</span>
        <span class="pscore ${p.score < 0 ? 'neg' : ''}">${money(p.score)}</span>
      </div>`).join('')}
  </div>`;

function home() {
  const multi = settings.players.length > 1;
  return `
  <div class="home">
    <div class="logo" aria-hidden="true"><span>🏆</span></div>
    <h1 class="title">Buzzer Beater</h1>
    <p class="tag">Sports trivia with today's stars. Answer out loud.</p>

    ${game && game.round ? `<button class="btn big gold" data-act="resume">Resume game (${game.round === 'final' ? 'Final' : `Round ${game.round}`})</button>` : ''}

    <section class="card">
      <h2>Players</h2>
      <div class="players">
        ${settings.players.map((n, i) => `
          <div class="prow" style="--c:${COLORS[i]}">
            <span class="dot"></span>
            <input class="pinput" data-i="${i}" value="${esc(n)}" maxlength="14" aria-label="Player ${i + 1} name">
            ${settings.players.length > 1 ? `<button class="icon" data-act="rmPlayer" data-i="${i}" aria-label="Remove player">✕</button>` : ''}
          </div>`).join('')}
      </div>
      ${settings.players.length < 4 ? '<button class="btn ghost" data-act="addPlayer">+ Add player</button>' : ''}
      ${multi ? '<p class="hint">Pass-and-play: when a clue is read, tap your name to buzz in.</p>' : ''}
    </section>

    <section class="card">
      <h2>Options</h2>
      <div class="seg" role="group" aria-label="Game length">
        <button class="${settings.length === 'quick' ? 'sel' : ''}" data-act="len" data-v="quick">Quick<small>1 round + Final</small></button>
        <button class="${settings.length === 'full' ? 'sel' : ''}" data-act="len" data-v="full">Full<small>2 rounds + Final</small></button>
      </div>
      <label class="tog"><input type="checkbox" data-set="readAloud" ${settings.readAloud ? 'checked' : ''}><span>Read clues out loud</span></label>
      <label class="tog"><input type="checkbox" data-set="autoListen" ${settings.autoListen ? 'checked' : ''} ${SR ? '' : 'disabled'}><span>Start listening automatically</span></label>
      <label class="tog"><input type="checkbox" data-set="questionForm" ${settings.questionForm ? 'checked' : ''}><span>Must answer as a question ("Who is…?")</span></label>
      <label class="row">Answer time
        <select data-set="seconds">${[10, 15, 20, 30, 45].map(s => `<option ${settings.seconds === s ? 'selected' : ''} value="${s}">${s} sec</option>`).join('')}</select>
      </label>
      ${SR ? '' : '<p class="hint warn">This browser can\'t take voice answers directly. You can still type, or tap the mic on your keyboard to dictate.</p>'}
    </section>

    <button class="btn big gold" data-act="start">${game ? 'New game' : 'Play'}</button>

    <div class="foot">
      <button class="btn ghost" data-act="share">📤 Share with friends</button>
      <button class="btn ghost" data-act="install" id="installBtn" hidden>📲 Install app</button>
    </div>
    ${best ? `<p class="hint center">Best solo score: <b>${money(best)}</b></p>` : ''}
    <p class="hint center" id="iosHint" hidden>On iPhone: tap <b>Share</b> → <b>Add to Home Screen</b> to install.</p>
  </div>`;
}

function board() {
  const r = game.round;
  return `
  ${scoreBar()}
  <div class="roundbar"><span>${r === 2 ? 'Double Round' : 'Round 1'}</span>
    <span class="who">${esc(game.players[game.picker].name)} picks</span>
    <button class="link" data-act="menu">Menu</button></div>
  <div class="board">
    ${game.board.map(c => `<div class="cat">${esc(c.name)}</div>`).join('')}
    ${[0, 1, 2, 3, 4].map(ri => game.board.map((c, ci) => {
      const x = c.clues[ri];
      return x.done ? '<div class="tile done"></div>'
        : `<button class="tile" data-act="open" data-c="${ci}" data-r="${ri}">$${x.value}</button>`;
    }).join('')).join('')}
  </div>
  <div class="boardfoot"><button class="link" data-act="skipRound">${game.round === 1 && game.length === 'full' ? 'Skip to Double Round' : 'Skip to Final'}</button></div>`;
}

function roundIntro() {
  return `
  ${scoreBar()}
  <div class="center-screen">
    <div class="splash">DOUBLE<br>ROUND</div>
    <p class="tag">Values are doubled and there are two Daily Doubles hidden on the board.</p>
    <p class="tag">${esc(game.players[game.picker].name)} picks first.</p>
    <button class="btn big gold" data-act="toBoard">Let's go</button>
  </div>`;
}

function maxWager() {
  const p = game.players[game.picker];
  return Math.max(p.score, game.round === 2 ? 2000 : 1000);
}

function dd() {
  const c = curClue();
  const p = game.players[game.picker];
  return `
  ${scoreBar()}
  <div class="center-screen">
    <div class="splash dd">DAILY<br>DOUBLE!</div>
    <p class="cathead">${esc(game.board[ui.clue.c].name)}</p>
    <p class="tag">${esc(p.name)}, how much do you want to wager?<br><small>$5 up to ${money(maxWager())}</small></p>
    <div class="wager">
      <input id="wager" type="number" inputmode="numeric" min="5" max="${maxWager()}" value="${Math.min(maxWager(), Math.max(5, c.value))}">
      <button class="btn ghost" data-act="trueDD">All in</button>
    </div>
    <button class="btn big gold" data-act="ddGo">Show the clue</button>
  </div>`;
}

function curClue() { return game.board[ui.clue.c].clues[ui.clue.r]; }
function clueValue() { return ui.wager ?? curClue().value; }

function answerBox(placeholder = 'Type or speak your answer') {
  return `
  <div class="answerbox">
    <button id="mic" class="mic" data-act="mic" ${SR ? '' : 'data-nosr="1"'}>
      <span class="micicon" aria-hidden="true">🎤</span><span class="lbl">Tap to speak</span>
    </button>
    <form id="ansform" class="ansrow">
      <input id="answer" autocomplete="off" autocapitalize="words" enterkeyhint="send" placeholder="${placeholder}">
      <button class="btn gold" type="submit">Answer</button>
    </form>
  </div>`;
}

function clue() {
  const c = curClue();
  const cat = game.board[ui.clue.c].name;
  const multi = game.players.length > 1;
  let bottom = '';
  if (ui.phase === 'reading') {
    bottom = `<div class="phase-note">Reading the clue…</div><button class="link" data-act="skipRead">Skip</button>`;
  } else if (ui.phase === 'buzz') {
    bottom = `
      ${ui.msg ? `<div class="msg bad">${esc(ui.msg)}</div>` : ''}
      <div class="phase-note">Buzz in!</div>
      <div class="buzzers">
        ${game.players.map((p, i) => `<button class="buzz" style="--c:${COLORS[i]}" data-act="buzz" data-i="${i}" ${ui.locked.includes(i) ? 'disabled' : ''}>${esc(p.name)}</button>`).join('')}
      </div>
      <button class="btn ghost" data-act="noone">Nobody knows</button>`;
  } else if (ui.phase === 'answer') {
    bottom = `
      ${ui.msg ? `<div class="msg bad">${esc(ui.msg)}</div>` : ''}
      ${multi ? `<div class="phase-note" style="color:${COLORS[ui.answerer]}">${esc(game.players[ui.answerer].name)}, your answer?</div>` : ''}
      ${answerBox()}
      <button class="link" data-act="pass">I don't know</button>`;
  } else if (ui.phase === 'result') {
    const r = ui.result;
    bottom = `
      ${r.text ? `<div class="msg ${r.correct ? 'good' : 'bad'}">${r.correct ? '✓' : '✗'} ${esc(r.who ? r.who + ': ' : '')}“${esc(r.text)}”</div>` : ''}
      <div class="reveal"><small>Correct response</small>Who is ${esc(c.a)}?</div>
      ${r.correct ? `<div class="msg good">+${money(r.value)} to ${esc(r.who)}</div>` : ''}
      ${r.canOverride ? `<button class="btn ghost" data-act="override">I was right, count it</button>` : ''}
      <button class="btn big gold" data-act="continue">Continue</button>`;
  }
  return `
  ${scoreBar()}
  <div class="cluescreen">
    <div class="cluetop"><span>${esc(cat)}</span><span>${ui.wager != null ? 'Wager ' : ''}${money(clueValue())}</span></div>
    <div class="cluetext">${esc(c.q)}</div>
    <div class="timer"><div id="timebar"></div></div>
    <div class="bottom">${bottom}</div>
  </div>`;
}

function final() {
  const f = game.final;
  const ps = game.players;
  const solo = ps.length === 1;
  const p = ps[f.turn];
  const head = `${scoreBar()}<div class="center-screen">`;
  if (f.step === 'intro') return `${head}
    <div class="splash">FINAL<br>ROUND</div>
    <p class="tag">The category is</p>
    <p class="cathead big">${esc(f.cat)}</p>
    <p class="tag">Everyone makes a secret wager, then answers one clue.</p>
    <button class="btn big gold" data-act="fNext">Make wagers</button></div>`;
  if (f.step === 'wagerPass') return `${head}
    <p class="tag">Pass the phone to</p><p class="cathead big" style="color:${COLORS[f.turn]}">${esc(p.name)}</p>
    <button class="btn big gold" data-act="fNext">I'm ${esc(p.name)}, show my wager</button></div>`;
  if (f.step === 'wager') return `${head}
    <p class="cathead">${esc(f.cat)}</p>
    <p class="tag">${esc(p.name)}, you have ${money(p.score)}. Wager $0 to ${money(Math.max(0, p.score))}.</p>
    <div class="wager"><input id="wager" type="number" inputmode="numeric" min="0" max="${Math.max(0, p.score)}" value="0">
    <button class="btn ghost" data-act="trueDD">All in</button></div>
    <button class="btn big gold" data-act="fWager">Lock it in</button></div>`;
  if (f.step === 'answerPass') return `${head}
    <p class="tag">Pass the phone to</p><p class="cathead big" style="color:${COLORS[f.turn]}">${esc(p.name)}</p>
    <button class="btn big gold" data-act="fNext">I'm ready, show the clue</button></div>`;
  if (f.step === 'answer') return `${scoreBar()}
    <div class="cluescreen">
      <div class="cluetop"><span>${esc(f.cat)}</span><span>${solo ? '' : esc(p.name)}</span></div>
      <div class="cluetext">${esc(f.q)}</div>
      <div class="timer"><div id="timebar"></div></div>
      <div class="bottom">${answerBox()}<button class="link" data-act="fPass">I don't know</button></div>
    </div>`;
  if (f.step === 'reveal') return `${head}
    <p class="cathead">${esc(f.cat)}</p>
    <div class="reveal"><small>Correct response</small>Who is ${esc(f.a)}?</div>
    <div class="results">
      ${ps.map((pl, i) => `
        <div class="res" style="--c:${COLORS[i]}">
          <div><b>${esc(pl.name)}</b><span class="said">“${esc(f.answers[i] || '—')}”</span></div>
          <div class="${f.correct[i] ? 'good' : 'bad'}">${f.correct[i] ? '+' : '−'}${money(f.wagers[i] || 0)}</div>
          <button class="link small" data-act="fFlip" data-i="${i}">${f.correct[i] ? 'Mark wrong' : 'Mark right'}</button>
        </div>`).join('')}
    </div>
    <button class="btn big gold" data-act="fFinish">Final scores</button></div>`;
  return '';
}

function end() {
  const ranked = game.players.map((p, i) => ({ ...p, i })).sort((a, b) => b.score - a.score);
  const solo = game.players.length === 1;
  const top = ranked[0];
  return `
  <div class="center-screen">
    <div class="splash">${solo ? 'GAME<br>OVER' : `${esc(top.name)}<br>WINS!`}</div>
    <div class="results">
      ${ranked.map((p, n) => `<div class="res" style="--c:${COLORS[p.i]}"><div><b>${n + 1}. ${esc(p.name)}</b></div><div class="${p.score < 0 ? 'bad' : ''}">${money(p.score)}</div></div>`).join('')}
    </div>
    ${solo && top.score >= best && top.score > 0 ? '<p class="msg good">New personal best!</p>' : ''}
    <button class="btn big gold" data-act="again">Play again</button>
    <button class="btn ghost" data-act="share">📤 Challenge a friend</button>
    <button class="link" data-act="home">Home</button>
  </div>`;
}

// ---------- after-render hooks ----------
const after = {
  clue() {
    if (ui.phase === 'reading') {
      const c = curClue();
      say(c.q).then(() => { if (ui.screen === 'clue' && ui.phase === 'reading') afterReading(); });
    } else if (ui.phase === 'answer') {
      bindAnswer(submitAnswer);
      startTimer(settings.seconds, () => submitAnswer([''], true));
      if (settings.autoListen && SR) listen(submitAnswer);
    } else if (ui.phase === 'buzz') {
      startTimer(Math.max(8, Math.round(settings.seconds * 0.6)), () => { if (ui.phase === 'buzz') reveal(null); });
    }
  },
  final() {
    const f = game.final;
    if (f.step === 'answer') {
      bindAnswer(finalAnswer);
      const begin = () => {
        if (ui.screen !== 'final' || game.final.step !== 'answer') return;
        startTimer(Math.max(30, settings.seconds), () => finalAnswer([$('#answer')?.value || ''], true));
        if (settings.autoListen && SR) listen(finalAnswer);
      };
      say(f.q).then(begin);
    } else if (f.step === 'wager') $('#wager')?.focus();
  },
  home() {
    if (deferredInstall) $('#installBtn').hidden = false;
    const ios = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.navigator.standalone;
    if (ios) $('#iosHint').hidden = false;
  },
  dd() { $('#wager')?.select(); },
};

function bindAnswer(handler) {
  $('#ansform')?.addEventListener('submit', e => {
    e.preventDefault();
    stopListening();
    handler([$('#answer').value]);
  });
}

function afterReading() {
  if (ui.wager != null) { ui.answerer = game.picker; ui.phase = 'answer'; }
  else if (game.players.length === 1) { ui.answerer = 0; ui.phase = 'answer'; }
  else ui.phase = 'buzz';
  render();
}

// ---------- clue logic ----------
function openClue(ci, ri) {
  ui.clue = { c: ci, r: ri }; ui.locked = []; ui.msg = ''; ui.result = null; ui.wager = null; ui.answerer = null; ui.lastWrong = null;
  if (curClue().dd) { go('dd'); return; }
  ui.phase = 'reading';
  go('clue');
}

function submitAnswer(responses, timedOut = false) {
  if (ui.screen !== 'clue' || ui.phase !== 'answer') return;
  stopTimer(); stopListening();
  const raw = (responses.find(r => r && r.trim()) || '').trim();
  const value = clueValue();
  const p = game.players[ui.answerer];
  let correct = !!raw && isCorrect(responses, curClue());
  let note = '';
  if (correct && settings.questionForm && !responses.some(isQuestionForm)) { correct = false; note = 'Remember to phrase it as a question!'; }
  if (correct) {
    p.score += value;
    game.picker = ui.answerer;
    ui.result = { correct: true, text: raw, who: p.name, value };
    ui.phase = 'result';
    saveGame(); render();
    say(pick(['Correct!', 'That\'s right!', 'Yes!', 'You got it!']));
    return;
  }
  p.score -= value;
  saveGame();
  ui.lastWrong = { i: ui.answerer, value, text: raw || (timedOut ? '(time ran out)' : '(no answer)') };
  ui.locked.push(ui.answerer);
  const others = game.players.map((_, i) => i).filter(i => !ui.locked.includes(i));
  if (ui.wager == null && game.players.length > 1 && others.length) {
    ui.msg = note || `${p.name}: “${raw || (timedOut ? 'time\'s up' : 'no answer')}” is wrong. −${money(value)}`;
    ui.phase = 'buzz';
    render();
    say(note || 'No.');
    return;
  }
  reveal({ text: ui.lastWrong.text, who: p.name, note });
}

function reveal(wrong) {
  stopTimer(); stopListening();
  const w = wrong || (ui.lastWrong ? { text: ui.lastWrong.text, who: game.players[ui.lastWrong.i].name } : null);
  ui.result = { correct: false, text: w?.text || '', who: w?.who || '', canOverride: !!ui.lastWrong };
  if (w?.note) ui.result.text = `${w.text} (${w.note})`;
  ui.phase = 'result';
  render();
  say(`The answer is ${curClue().a}.`);
}

function passClue() {
  stopTimer(); stopListening();
  if (ui.wager != null) { // passing a Daily Double still costs the wager
    game.players[ui.answerer].score -= ui.wager; saveGame();
    ui.lastWrong = { i: ui.answerer, value: ui.wager, text: '(passed)' };
    return reveal({ text: '(passed)', who: game.players[ui.answerer].name });
  }
  ui.locked.push(ui.answerer);
  const others = game.players.map((_, i) => i).filter(i => !ui.locked.includes(i));
  if (game.players.length > 1 && others.length) { ui.msg = `${game.players[ui.answerer].name} passed.`; ui.phase = 'buzz'; render(); return; }
  reveal(null);
}

function override() {
  const w = ui.lastWrong; if (!w) return;
  const p = game.players[w.i];
  p.score += w.value * 2;
  game.picker = w.i;
  ui.lastWrong = null;
  ui.result = { correct: true, text: w.text, who: p.name, value: w.value };
  saveGame(); render();
}

function finishClue() {
  curClue().done = true;
  ui.wager = null; ui.lastWrong = null;
  saveGame();
  if (roundDone()) nextRound(); else go('board');
}

// ---------- final logic ----------
function eligibleFrom(i) {
  // Everyone plays the final, even with $0 (they can only wager 0).
  return i < game.players.length ? i : -1;
}

function finalAnswer(responses, timedOut = false) {
  const f = game.final;
  if (ui.screen !== 'final' || f.step !== 'answer') return;
  stopTimer(); stopListening();
  const raw = (responses.find(r => r && r.trim()) || '').trim();
  let ok = !!raw && isCorrect(responses, f);
  if (ok && settings.questionForm && !responses.some(isQuestionForm)) ok = false;
  f.answers[f.turn] = raw || (timedOut ? '(time ran out)' : '(no answer)');
  f.correct[f.turn] = ok;
  const next = eligibleFrom(f.turn + 1);
  if (next >= 0) { f.turn = next; f.step = 'answerPass'; } else f.step = 'reveal';
  saveGame(); render();
  if (f.step === 'reveal') say(`The answer is ${f.a}.`);
}

function finalFinish() {
  const f = game.final;
  game.players.forEach((p, i) => { p.score += (f.correct[i] ? 1 : -1) * (f.wagers[i] || 0); });
  if (game.players.length === 1) { best = Math.max(best, game.players[0].score); save('st.best', best); }
  game.round = null; // game over, nothing to resume
  saveGame();
  go('end');
}

// ---------- share / install ----------
let deferredInstall = null;
window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); deferredInstall = e; const b = $('#installBtn'); if (b) b.hidden = false; });

async function share() {
  const url = location.href.split('#')[0];
  const lastScore = ui.screen === 'end' && game ? Math.max(...game.players.map(p => p.score)) : null;
  const text = lastScore != null ? `I just scored ${money(lastScore)} on Buzzer Beater sports trivia. Can you beat it?` : 'Play Buzzer Beater: sports trivia with today\'s stars. Answer out loud!';
  try {
    if (navigator.share) { await navigator.share({ title: 'Buzzer Beater', text, url }); return; }
    await navigator.clipboard.writeText(`${text} ${url}`);
    toast('Link copied. Paste it in a text to share.');
  } catch (e) { if (e?.name !== 'AbortError') toast(url); }
}

// ---------- events ----------
document.addEventListener('click', e => {
  const el = e.target.closest('[data-act]'); if (!el) return;
  const act = el.dataset.act;
  const i = Number(el.dataset.i);
  switch (act) {
    case 'addPlayer': settings.players.push(`Player ${settings.players.length + 1}`); saveSettings(); render(); break;
    case 'rmPlayer': settings.players.splice(i, 1); saveSettings(); render(); break;
    case 'len': settings.length = el.dataset.v; saveSettings(); render(); break;
    case 'start': case 'again':
      if (act === 'start' && game?.round && !confirm('Start a new game? Your current game will be lost.')) return;
      newGame(); break;
    case 'resume':
      if (game.round === 'final') go('final'); else go('board'); break;
    case 'home': go('home'); break;
    case 'menu': go('home'); break;
    case 'share': share(); break;
    case 'install': deferredInstall?.prompt(); deferredInstall = null; el.hidden = true; break;
    case 'open': openClue(Number(el.dataset.c), Number(el.dataset.r)); break;
    case 'toBoard': go('board'); break;
    case 'skipRound': if (confirm('Skip the rest of this round?')) nextRound(); break;
    case 'trueDD': { const w = $('#wager'); w.value = w.max; break; }
    case 'ddGo': {
      const v = Math.round(Number($('#wager').value));
      if (!(v >= 5 && v <= maxWager())) { toast(`Wager between $5 and ${money(maxWager())}`); return; }
      ui.wager = v; ui.answerer = game.picker; ui.phase = 'reading'; go('clue'); break;
    }
    case 'skipRead': hush(); afterReading(); break;
    case 'buzz': stopTimer(); ui.answerer = i; ui.msg = ''; ui.phase = 'answer'; hush(); render(); break;
    case 'noone': reveal(null); break;
    case 'mic': listen(ui.screen === 'final' ? finalAnswer : submitAnswer); break;
    case 'pass': passClue(); break;
    case 'override': override(); break;
    case 'continue': finishClue(); break;
    // final
    case 'fNext': {
      const f = game.final;
      if (f.step === 'intro') { f.turn = 0; f.step = game.players.length > 1 ? 'wagerPass' : 'wager'; }
      else if (f.step === 'wagerPass') f.step = 'wager';
      else if (f.step === 'answerPass') f.step = 'answer';
      saveGame(); render(); break;
    }
    case 'fWager': {
      const f = game.final; const p = game.players[f.turn];
      const v = Math.round(Number($('#wager').value));
      if (!(v >= 0 && v <= Math.max(0, p.score))) { toast(`Wager between $0 and ${money(Math.max(0, p.score))}`); return; }
      f.wagers[f.turn] = v;
      if (f.turn + 1 < game.players.length) { f.turn++; f.step = 'wagerPass'; }
      else { f.turn = 0; f.step = game.players.length > 1 ? 'answerPass' : 'answer'; }
      saveGame(); render(); break;
    }
    case 'fPass': finalAnswer([''], false); break;
    case 'fFlip': game.final.correct[i] = !game.final.correct[i]; saveGame(); render(); break;
    case 'fFinish': finalFinish(); break;
  }
});

document.addEventListener('input', e => {
  const t = e.target;
  if (t.classList.contains('pinput')) { settings.players[Number(t.dataset.i)] = t.value; saveSettings(); }
});
document.addEventListener('change', e => {
  const k = e.target.dataset.set; if (!k) return;
  settings[k] = e.target.type === 'checkbox' ? e.target.checked : Number(e.target.value);
  saveSettings();
});

// ---------- boot ----------
if (game && !game.players) game = null;
render();

if ('serviceWorker' in navigator && location.protocol === 'https:') {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}
