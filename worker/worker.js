// Cart Compare backend: a Cloudflare Worker that holds your API keys and talks to
// Kroger and Instacart for the phone app. It never places orders.
//
// Secrets (set with `npx wrangler secret put NAME`):
//   KROGER_CLIENT_ID, KROGER_CLIENT_SECRET   from developer.kroger.com
//   INSTACART_API_KEY                        from Instacart Developer Platform
//   APP_KEY (optional)                       shared key the app must send as X-App-Key
// Vars (wrangler.toml):
//   APP_URL            where the app is hosted (Kroger login sends you back here)
//   ALLOWED_ORIGINS    comma-separated origins allowed to call this API
//   INSTACART_ENV      "development" or "production"

const KROGER = 'https://api.kroger.com/v1';
let krogerAppToken = null; // { token, expires }

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const cors = corsHeaders(req, env);
    if (req.method === 'OPTIONS') return new Response(null, { headers: cors });
    const json = (data, status = 200) => new Response(JSON.stringify(data), {
      status, headers: { ...cors, 'Content-Type': 'application/json' },
    });

    try {
      // Browser redirects (no app key; the user's browser follows these).
      if (url.pathname === '/kroger/login') return krogerLogin(url, env);
      if (url.pathname === '/kroger/callback') return krogerCallback(url, env);

      if (env.APP_KEY && req.headers.get('X-App-Key') !== env.APP_KEY) return json({ error: 'Wrong app key' }, 401);

      switch (`${req.method} ${url.pathname}`) {
        case 'GET /':
          return json({ ok: true, kroger: !!env.KROGER_CLIENT_ID, instacart: !!env.INSTACART_API_KEY });

        case 'GET /kroger/locations': {
          const zip = url.searchParams.get('zip') || '';
          if (!/^\d{5}$/.test(zip)) return json({ error: 'Enter a 5-digit ZIP code' }, 400);
          const q = new URLSearchParams({ 'filter.zipCode.near': zip, 'filter.limit': '15' });
          return json(await krogerGet(`/locations?${q}`, env));
        }

        case 'GET /kroger/products': {
          const term = (url.searchParams.get('term') || '').slice(0, 100);
          const locationId = url.searchParams.get('locationId') || '';
          const limit = Math.min(Number(url.searchParams.get('limit')) || 10, 50);
          const q = new URLSearchParams({ 'filter.term': term, 'filter.locationId': locationId, 'filter.limit': String(limit) });
          return json(await krogerGet(`/products?${q}`, env));
        }

        case 'POST /kroger/cart': {
          const auth = req.headers.get('Authorization') || '';
          const { items } = await req.json();
          if (!auth.startsWith('Bearer ') || !Array.isArray(items)) return json({ error: 'Bad request' }, 400);
          const r = await fetch(`${KROGER}/cart/add`, {
            method: 'PUT',
            headers: { Authorization: auth, 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify({ items }),
          });
          if (!r.ok) return json({ error: `Kroger cart error ${r.status}: ${await r.text()}` }, r.status === 401 ? 401 : 502);
          return json({ ok: true });
        }

        case 'POST /kroger/refresh': {
          const { refresh_token } = await req.json();
          return json(await krogerToken(env, { grant_type: 'refresh_token', refresh_token }));
        }

        case 'POST /instacart/list':
          return json(await instacartList(await req.json(), env));

        default:
          return json({ error: 'Not found' }, 404);
      }
    } catch (e) {
      return json({ error: e.message || String(e) }, 500);
    }
  },
};

function corsHeaders(req, env) {
  const origin = req.headers.get('Origin') || '';
  const allowed = (env.ALLOWED_ORIGINS || '*').split(',').map(s => s.trim()).filter(Boolean);
  const allow = allowed.includes('*') ? '*' : allowed.includes(origin) ? origin : allowed[0] || '';
  return {
    'Access-Control-Allow-Origin': allow,
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-App-Key',
    'Access-Control-Max-Age': '86400',
    Vary: 'Origin',
  };
}

// ---------- Kroger ----------

async function krogerToken(env, params) {
  if (!env.KROGER_CLIENT_ID || !env.KROGER_CLIENT_SECRET) throw new Error('Kroger keys are not set on the backend');
  const r = await fetch(`${KROGER}/connect/oauth2/token`, {
    method: 'POST',
    headers: {
      Authorization: `Basic ${btoa(`${env.KROGER_CLIENT_ID}:${env.KROGER_CLIENT_SECRET}`)}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: new URLSearchParams(params),
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`Kroger auth failed: ${body.error_description || body.error || r.status}`);
  return body;
}

async function krogerGet(path, env) {
  if (!krogerAppToken || Date.now() > krogerAppToken.expires) {
    const t = await krogerToken(env, { grant_type: 'client_credentials', scope: 'product.compact' });
    krogerAppToken = { token: t.access_token, expires: Date.now() + (t.expires_in - 60) * 1000 };
  }
  const r = await fetch(KROGER + path, { headers: { Authorization: `Bearer ${krogerAppToken.token}`, Accept: 'application/json' } });
  if (r.status === 401) krogerAppToken = null;
  if (!r.ok) throw new Error(`Kroger error ${r.status}`);
  return r.json();
}

function krogerLogin(url, env) {
  if (!env.APP_URL) return new Response('Set APP_URL in wrangler.toml first', { status: 500 });
  const q = new URLSearchParams({
    scope: 'cart.basic:write',
    response_type: 'code',
    client_id: env.KROGER_CLIENT_ID,
    redirect_uri: `${url.origin}/kroger/callback`,
  });
  return Response.redirect(`${KROGER}/connect/oauth2/authorize?${q}`, 302);
}

async function krogerCallback(url, env) {
  const code = url.searchParams.get('code');
  if (!code) return new Response(`Kroger login was cancelled: ${url.searchParams.get('error') || ''}`, { status: 400 });
  const t = await krogerToken(env, {
    grant_type: 'authorization_code', code, redirect_uri: `${url.origin}/kroger/callback`,
  });
  // Tokens go back only to your own app (APP_URL), in the URL fragment so they never hit a server log.
  const frag = new URLSearchParams({
    kroger_token: t.access_token, kroger_refresh: t.refresh_token || '', expires_in: String(t.expires_in || 1800),
  });
  return Response.redirect(`${env.APP_URL}#${frag}`, 302);
}

// ---------- Instacart Developer Platform ----------

async function instacartList(body, env) {
  if (!env.INSTACART_API_KEY) throw new Error('Instacart API key is not set on the backend');
  const base = env.INSTACART_ENV === 'production' ? 'https://connect.instacart.com' : 'https://connect.dev.instacart.tools';
  const lineItems = (body.line_items || []).slice(0, 200);
  if (!lineItems.length) throw new Error('The list is empty');
  const r = await fetch(`${base}/idp/v1/products/products_link`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${env.INSTACART_API_KEY}`, 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({
      title: String(body.title || 'Grocery list').slice(0, 100),
      link_type: 'shopping_list',
      line_items: lineItems,
      ...(env.APP_URL ? { landing_page_configuration: { partner_linkback_url: env.APP_URL } } : {}),
    }),
  });
  const res = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`Instacart error ${r.status}: ${res.error?.message || JSON.stringify(res).slice(0, 200)}`);
  return { url: res.products_link_url };
}
