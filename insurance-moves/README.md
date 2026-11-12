# Insurance Moves

A phone app (installable web app) that tracks **management-level moves across the insurance
industry**: appointments, promotions, successions, departures and retirements at carriers,
reinsurers, brokers, MGAs, Lloyd's syndicates, health plans and insurtechs.

```
insurance-moves/
  index.html, app.js, styles.css   the app (plain HTML/JS, no build step)
  lib/classify.js                  turns a headline into person / company / role / level / move type
  collector/                       Node script that gathers moves (runs in GitHub Actions)
    sources.json                   the feeds and SEC settings: edit this to add sources
  data/moves.json                  collected moves (the workflow updates this)
  test/                            node --test insurance-moves/test/*.test.mjs
```

## What it does

- **Moves feed:** newest first, grouped by day. Filter by date range, level (C-suite,
  President/MD, EVP/SVP, VP, Head/Director, Board, Manager), move type, sector, function and
  source, or search by person, company or role. Export what you see as CSV.
- **Watchlist:** follow companies, people or keywords. Matches are highlighted and the tab shows
  a badge for new ones.
- **Insights:** moves over time, most active companies, and breakdowns by level, move type,
  function and sector.
- **Add:** log a move you heard about yourself. Paste a headline and it fills in the fields.
- **Edit / Hide / Star / Notes** on any move. Corrections and notes stay on your phone, and
  **More → Back up my data** saves them to a file.

## Where the data comes from

The collector runs every 6 hours in GitHub Actions (`.github/workflows/pages.yml`):

| Source | What it finds |
|---|---|
| Insurance trade press RSS (Insurance Journal, Carrier Management, Reinsurance News, Artemis) | People-move stories |
| Google News RSS searches | Appointments, promotions and exits at insurers, brokers, MGAs and Lloyd's |
| **SEC EDGAR 8-K, Item 5.02** | Officer and director changes filed by US-listed insurers and brokers (SIC 6311–6411) |

Each item is classified by `lib/classify.js`. Only items with **both** a move (appoints, joins,
promoted, steps down, retires…) **and** a management title are kept. The same person at the same
company reported by several outlets within 3 weeks is merged into one move with every source
linked. Up to 2 years of history is kept.

This is keyword matching, not a person reading each story, so some moves will be missed or
misread. Use **Edit** to correct one and **Hide** to drop false hits.

## Setup

1. Make sure GitHub Pages is on: **Settings → Pages → Source: GitHub Actions**.
2. Recommended: **Settings → Secrets and variables → Actions → Variables → New variable**
   `SEC_USER_AGENT` = `Your Name your-email@example.com`. The SEC asks automated clients to identify
   themselves and may block requests without contact details.
3. Run **Actions → Deploy apps to GitHub Pages → Run workflow** once to collect the first batch
   (it looks back 45 days in SEC filings on the first run).
4. Open `https://<your-username>.github.io/claude/insurance-moves/` on your phone and add it to
   your home screen (Safari: Share → Add to Home Screen; Chrome: ⋮ → Install app).

The 6-hourly schedule runs only from the repository's default branch (`main`), so collection and
history start once this is merged.

To add a source, add an RSS/Atom feed to `collector/sources.json`. Set `"requireInsurance": true`
for general business feeds so non-insurance stories are dropped.

To run the collector yourself: `node insurance-moves/collector/collect.mjs` (Node 18+, nothing to install).
