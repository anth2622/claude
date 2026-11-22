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

---

# Buzzer Beater: sports trivia (`app/sports-trivia/`)

A game-show style sports trivia board with today's stars (NFL, NBA, MLB, NHL, soccer, tennis,
golf, WNBA, Olympics). Answer out loud: the app reads each clue and listens for your answer.

- **Play solo or pass-and-play with up to 4 people.** With 2+ players, tap your name to buzz in.
- **Board:** 6 random categories × 5 clues, a Double round, hidden Daily Doubles, and a Final round with wagers.
  Categories and clues are reshuffled every game.
- **Voice:** tap the mic (or leave "Start listening automatically" on) and say "Who is Patrick Mahomes?" or
  just "Mahomes". The checker accepts last names and common mishearings. If it gets one wrong, tap **I was right, count it**.
- **Typing works too**, for browsers without voice support.

**Install it:** once GitHub Pages is on (see step 1 above) it's at
`https://<your-username>.github.io/claude/sports-trivia/`. Open it on your phone and use
**Add to Home Screen** (iPhone, Safari) or **Install app** (Android, Chrome). Send that same link to friends.

**Voice support:** Chrome on Android and Safari on iPhone support voice answers. On iPhone, voice works
best in Safari itself; if it doesn't work from the home-screen icon, tap the mic on your keyboard to dictate.

**Add or update clues:** edit `app/sports-trivia/clues.js`. Each category needs at least one clue at each level 1-5.
Facts are current through the 2025 seasons.

---

# Magic Photos: AI photo editing for kids (`app/magic-photos/` + `photo-worker/`)

An iPad app that does exactly one thing: your kid picks a photo, types what to change
("put a crown on the dog", "make it snow"), and Gemini edits it. There is no chat and no other
features. It can only edit photos.

**Kid safety.** Every edit has to pass four checks, and if any check fails or errors, the edit is blocked:
1. A word list catches obvious no-gos (weapons, gore, nudity, drugs, swearing, insults).
2. Gemini checks the typed request **and the starting photo** against strict rules for ages 5-12. It
   blocks anything mature, scary or violent, mean edits that make a real person look bad, and
   attempts to trick the checker.
3. The edit runs with Gemini's own safety filters at their strictest setting.
4. Gemini checks the **finished picture** again before your kid sees it.

When something is blocked, your kid sees a kind message with a fun alternative idea.
No filter is perfect, so do a few test edits yourself first.

**Privacy.** Photos go from the iPad to your backend, then to Google's Gemini API, and are not stored by this app.
On Gemini's free tier, Google may use what you send to improve its products. On a paid
(billing-enabled) key it doesn't. See <https://ai.google.dev/gemini-api/terms>.

## Setup (about 20 minutes, needs a computer with Node.js)

1. **App hosting:** turn on GitHub Pages (Settings → Pages → Source: GitHub Actions, see step 1 above).
   The app will be at `https://<your-username>.github.io/claude/magic-photos/`.
2. **Gemini key:** create one at <https://aistudio.google.com/apikey>. In Google Cloud billing, set a
   **budget alert** so you're never surprised. Each edit costs a few cents.
3. **Backend** (free Cloudflare account):
   ```bash
   cd photo-worker
   # edit wrangler.toml: set ALLOWED_ORIGINS to https://<your-username>.github.io
   npx wrangler login
   npx wrangler secret put GEMINI_API_KEY
   npx wrangler secret put APP_KEY        # make up a family password; kids shouldn't know it
   npx wrangler deploy
   ```
   It prints a URL like `https://magic-photos-api.<you>.workers.dev`.
4. **On each iPad:** open the app link in Safari → Share → **Add to Home Screen**. Open it from the
   home screen, enter the backend URL and family password, and tap **Check setup**. If a model
   shows ❌, put one of the listed names in `wrangler.toml` and run `npx wrangler deploy` again.
   Google renames its models now and then.
5. **Lock it down (optional):** Settings → Accessibility → **Guided Access** keeps the iPad in this one
   app until you triple-click and enter your passcode.

The grown-ups settings sit behind the faint ⚙︎ in the corner and need the family password.

**Using it:** tap **Pick a photo** (from Photos or the camera), type an idea or tap a suggestion, then tap
**Make magic!** Keep typing ideas to keep changing the same picture. **Undo** steps back, holding
**Hold to see before** shows the original, and **Save** opens the share sheet → **Save Image**.
