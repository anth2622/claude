// Magic Photos: pick a photo, type what to change, get it back. That's all it can do.
// The app only talks to your own backend (photo-worker/), which holds the Gemini key
// and runs the kid-safety checks.

const $ = id => document.getElementById(id);
const store = {
  get(k) { try { return localStorage.getItem(k) || ''; } catch { return ''; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

const IDEAS = [
  'Turn it into a cartoon', 'Make it look like a painting', 'Add a friendly dragon',
  'Make it snow', 'Put us in outer space', 'Add a rainbow', 'Give everyone superhero capes',
  'Put a crown on everyone', 'Make it underwater with fish', 'Add a baby dinosaur',
  'Turn it into a comic book', 'Make it a sparkly fairy tale', 'Add balloons everywhere',
  'Make it look like LEGO', 'Put a party hat on the pet', 'Make it a candy land',
  'Add a unicorn', 'Make it a watercolor picture', 'Turn day into a starry night',
  'Add a friendly robot', 'Make it look like a video game', 'Put us in a jungle',
];
const WORKING = [
  'Mixing the magic…', 'Waving the wand…', 'Adding sparkles…', 'Asking the wizards…',
  'Almost there…', 'Painting with pixels…', 'Sprinkling fairy dust…',
];
const MAX_SIDE = 1536;

let history = [];      // stack of { url, b64, mime } — history[0] is the original photo
let busy = false;

// ---------- screens ----------
function show(id) {
  for (const s of document.querySelectorAll('.screen')) s.hidden = s.id !== id;
}

function configured() { return store.get('backend') && store.get('password'); }

function start() {
  if (!configured()) return openSettings(true);
  show(history.length ? 'editor' : 'pick');
}

// ---------- picking a photo ----------
$('file').addEventListener('change', async e => {
  const file = e.target.files?.[0];
  e.target.value = '';
  if (!file) return;
  try {
    const url = URL.createObjectURL(file);
    const prepared = await prepare(url);
    URL.revokeObjectURL(url);
    history = [prepared];
    $('prompt').value = '';
    shuffleIdeas();
    render();
    say('');
    show('editor');
  } catch {
    alert("That photo couldn't be opened. Try a different one.");
  }
});

$('newphoto').addEventListener('click', () => {
  if (busy) return;
  $('file').click();
});

// Shrinks the photo and turns it into JPEG so it uploads fast.
function prepare(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
      const w = Math.round(img.naturalWidth * scale), h = Math.round(img.naturalHeight * scale);
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      const ctx = c.getContext('2d');
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, w, h);
      ctx.drawImage(img, 0, 0, w, h);
      const url = c.toDataURL('image/jpeg', 0.9);
      resolve({ url, b64: url.split(',')[1], mime: 'image/jpeg' });
    };
    img.onerror = reject;
    img.src = src;
  });
}

// ---------- ideas ----------
function shuffleIdeas() {
  const picks = [...IDEAS].sort(() => Math.random() - 0.5).slice(0, 6);
  const box = $('ideas');
  box.replaceChildren(...picks.map(text => {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = text;
    b.addEventListener('click', () => { $('prompt').value = text; });
    return b;
  }));
}

// ---------- making magic ----------
$('go').addEventListener('click', makeMagic);
$('prompt').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); makeMagic(); }
});

async function makeMagic() {
  if (busy || !history.length) return;
  const prompt = $('prompt').value.trim();
  if (!prompt) { say('Type what you want the magic to do, or tap an idea!'); $('prompt').focus(); return; }
  $('prompt').blur();

  setBusy(true);
  say('');
  const current = history[history.length - 1];
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), 120_000);
  try {
    const r = await fetch(`${store.get('backend').replace(/\/+$/, '')}/edit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-App-Key': store.get('password') },
      body: JSON.stringify({ prompt, image: current.b64, mimeType: current.mime }),
      signal: ctrl.signal,
    });
    const data = await r.json().catch(() => ({}));
    if (r.status === 401) { say('A grown-up needs to fix the settings (⚙︎ at the top).'); return; }
    if (data.blocked) { say(`🙈 ${data.message}`); return; }
    if (!r.ok || !data.image) { say(`😕 ${data.error || "The magic didn't work that time. Try again!"}`); return; }

    const next = await prepare(`data:${data.mimeType};base64,${data.image}`);
    history.push(next);
    $('prompt').value = '';
    render();
    say('🎉 Ta-da! Type another idea to keep changing it, or tap Save.', true);
  } catch (e) {
    say(e.name === 'AbortError' ? '⏰ That took too long. Try again!' : '📡 Could not reach the magic. Check the internet and try again.');
  } finally {
    clearTimeout(timeout);
    setBusy(false);
  }
}

let workingTimer = null;
function setBusy(on) {
  busy = on;
  $('working').hidden = !on;
  for (const id of ['go', 'undo', 'save', 'newphoto', 'compare']) $(id).disabled = on;
  $('prompt').disabled = on;
  clearInterval(workingTimer);
  if (on) {
    let i = 0;
    $('working-msg').textContent = WORKING[0];
    workingTimer = setInterval(() => { $('working-msg').textContent = WORKING[++i % WORKING.length]; }, 2500);
  }
}

function say(text, good = false) {
  const m = $('message');
  m.textContent = text;
  m.hidden = !text;
  m.classList.toggle('good', good);
}

function render() {
  const current = history[history.length - 1];
  $('photo').src = current.url;
  const edited = history.length > 1;
  $('undo').hidden = !edited;
  $('save').hidden = !edited;
  $('compare').hidden = !edited;
}

// ---------- before/after, undo, save ----------
const peek = on => {
  if (history.length < 2) return;
  $('photo').src = on ? history[0].url : history[history.length - 1].url;
};
$('compare').addEventListener('pointerdown', e => { e.preventDefault(); peek(true); });
for (const ev of ['pointerup', 'pointerleave', 'pointercancel']) $('compare').addEventListener(ev, () => peek(false));
$('compare').addEventListener('contextmenu', e => e.preventDefault());

$('undo').addEventListener('click', () => {
  if (busy || history.length < 2) return;
  history.pop();
  render();
  say('');
});

$('save').addEventListener('click', async () => {
  const current = history[history.length - 1];
  const blob = await (await fetch(current.url)).blob();
  const file = new File([blob], `magic-photo-${Date.now()}.jpg`, { type: 'image/jpeg' });
  if (navigator.canShare?.({ files: [file] })) {
    try { await navigator.share({ files: [file] }); return; } catch (e) { if (e.name === 'AbortError') return; }
  }
  say('Press and hold the picture, then tap "Save to Photos".', true);
});

// ---------- grown-up settings ----------
$('grownups').addEventListener('click', () => {
  if (busy) return;
  if (configured()) {
    const pw = prompt('Grown-ups only! Enter the family password:');
    if (pw === null) return;
    if (pw !== store.get('password')) { alert('That password is not right.'); return; }
  }
  openSettings(false);
});

function openSettings(firstRun) {
  $('backend').value = store.get('backend');
  $('password').value = store.get('password');
  $('check-out').hidden = true;
  $('close-settings').hidden = firstRun;
  show('settings');
}

$('save-settings').addEventListener('click', () => {
  const backend = $('backend').value.trim();
  const password = $('password').value;
  if (!/^https:\/\//.test(backend) || !password) { alert('Enter the backend URL (starting with https://) and the family password.'); return; }
  store.set('backend', backend);
  store.set('password', password);
  start();
});

$('close-settings').addEventListener('click', start);

$('check').addEventListener('click', async () => {
  const out = $('check-out');
  out.hidden = false;
  out.textContent = 'Checking…';
  const backend = $('backend').value.trim().replace(/\/+$/, '');
  try {
    const r = await fetch(`${backend}/models`, { headers: { 'X-App-Key': $('password').value } });
    const data = await r.json();
    if (!r.ok) { out.textContent = `❌ ${data.error || r.status}`; return; }
    const line = (label, m) => `${m.found ? '✅' : '❌'} ${label}: ${m.configured}${m.found ? '' : ' (not found for your key)'}`;
    out.textContent = [
      '✅ Backend reached, password correct, Gemini key works.',
      line('Image model', data.imageModel),
      line('Safety-check model', data.checkModel),
      '',
      (!data.imageModel.found || !data.checkModel.found)
        ? `Fix: set IMAGE_MODEL / CHECK_MODEL in photo-worker/wrangler.toml and run "npx wrangler deploy".\nImage models your key can use:\n  ${data.imageModelsAvailable.join('\n  ') || '(none)'}`
        : 'All set! 🎉',
    ].join('\n');
  } catch {
    out.textContent = '❌ Could not reach the backend. Check the URL, and that ALLOWED_ORIGINS in wrangler.toml matches this site.';
  }
});

// ---------- go ----------
if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
start();
