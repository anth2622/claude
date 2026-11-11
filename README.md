# Cart Compare

A phone app (installable web app) that takes an ingredient list, checks prices across grocery
stores, shows you the cheapest **single store** and the cheapest **mix-and-match** split
(including per-store delivery fees), and then builds the carts for you.

**It never places an order.** Carts are created in each store, and you review and check out yourself.

```
app/      the phone app (plain HTML/JS, no build step)
worker/   small backend (Cloudflare Worker, free tier) that holds your API keys
```

## What each store can do

| Store | Prices compared? | Builds a cart? | Needs |
|---|---|---|---|
| Demo stores | ✅ made-up prices | shows what would be added | nothing, on by default |
| **Kroger family** (Kroger, Ralphs, Fred Meyer, King Soopers, Fry's, Smith's, Harris Teeter…) | ✅ real prices at your store | ✅ adds to your real Kroger cart | free Kroger developer account + backend |
| **Shopify** grocery stores | ✅ | ✅ cart with a checkout link | that store's Storefront API token |
| **Instacart** | ❌ Instacart doesn't share prices with outside apps | ✅ opens your list in Instacart, where you pick the store | Instacart Developer Platform key + backend |
| Walmart, Amazon Fresh, others | ❌ | ❌ | no public cart API exists |

## 1. Put the app on your phone

1. In this GitHub repo: **Settings → Pages → Source: GitHub Actions**.
   (GitHub Pages is free for public repos. Private repos need a paid GitHub plan.)
2. Push to this branch (or run the *Deploy app to GitHub Pages* workflow). Your app will be at
   `https://<your-username>.github.io/claude/`.
3. Open that link on your phone:
   - **iPhone (Safari):** Share button → **Add to Home Screen**
   - **Android (Chrome):** ⋮ menu → **Install app** / **Add to Home screen**

It works right away with demo stores, so you can try the whole flow.

## 2. Set up the backend (for Kroger and Instacart)

You need a free Cloudflare account and Node.js on a computer.

```bash
cd worker
# edit wrangler.toml: set APP_URL and ALLOWED_ORIGINS to your Pages URL
npx wrangler login
npx wrangler secret put KROGER_CLIENT_ID
npx wrangler secret put KROGER_CLIENT_SECRET
npx wrangler secret put INSTACART_API_KEY
npx wrangler secret put APP_KEY          # optional: any password; enter the same one in the app
npx wrangler deploy
```

It prints a URL like `https://cart-compare-api.<you>.workers.dev`. In the app, open **Stores** and
paste it into **Backend URL**.

### Kroger keys
1. Sign up at <https://developer.kroger.com> and create an app (Production environment).
2. Scopes: **product.compact** and **cart.basic:write**.
3. Redirect URI: `https://cart-compare-api.<you>.workers.dev/kroger/callback`
4. Use the client ID and secret for the secrets above.
5. In the app, open **Stores**, turn on Kroger, enter your ZIP, pick your store, and tap **Connect Kroger account**.

### Instacart key
1. Apply at <https://www.instacart.com/company/business/developers> (Instacart Developer Platform).
2. You get a development key first. Keep `INSTACART_ENV = "development"` until Instacart approves you for production.
3. In the app, turn on Instacart. You get a **Send list to Instacart** button.

### Shopify stores
In **Stores → Add a Shopify store**, enter the store's domain (`name.myshopify.com`) and a
**Storefront API access token**. The store owner creates the token (Shopify admin → Apps →
Develop apps, or the Headless channel). This works for stores you run or partner with.
Shopify doesn't give out tokens for other people's stores.

## How the comparison works

- Each ingredient is searched at every connected store. Results that don't mention the
  ingredient are filtered out, and the cheapest match is chosen. Tap any item to swap in a
  different product (brand, size, organic), and all totals update.
- **All at one store:** total per store, plus its fee. Stores missing items are flagged.
- **Mix & match:** checks every combination of stores and picks the cheapest overall, counting
  each store's fee once. Splitting isn't worth it if the fees eat the savings, and the app accounts for that.
- Quantities: "3 cans beans" buys 3. "2 lb chicken" buys 1 package. Adjust with the +/− buttons.

Your settings and tokens are stored only on your phone (browser local storage).
