# Acme Marketing Funnel Dashboard

A one-page leadership view of how marketing drives pipeline and revenue, built from an 18-month raw CRM export (`data/acme_marketing_funnel_data.csv`).

**Live:** _add your Vercel URL here_

## How it works

```
data/acme_marketing_funnel_data.csv   raw export (never edited)
        │
        ▼
scripts/clean.py                      dedupes, normalizes channels, fixes 100× deal amounts,
        │                             computes every metric, logs every data-quality decision
        ▼
data.js  +  data/clean_leads.csv      pre-computed numbers for the page / cleaned lead-level file
        │
        ▼
index.html + app.js                   static page; charts via Chart.js (vendored in /vendor)
```

There is no build step and no framework. Vercel serves the folder as a static site.

## Run it locally

```bash
pip install pandas
python3 scripts/clean.py      # regenerates data.js and data/clean_leads.csv
open index.html               # or just double-click it
```

## Data decisions (summary)

| Issue | Handling |
|---|---|
| 15 exact duplicate rows (incl. one duplicated opportunity) | Removed |
| `lead_source` has 18 spellings for 8 channels | Channel derived from `campaign` (clean, 1:1 mapping) |
| 6 closed-won amounts ~100× their size band ($16.5M of $18.5M raw revenue) | Divided by 100, flagged and disclosed |
| 4 closed-won deals with no amount | Counted as wins, $0 revenue |
| Recent cohorts haven't had time to close | Win rate/revenue use closed deals only; open deals shown as pipeline |
| No spend data | CAC/ROI not calculated; called out |

The full list is shown on the page under **About the data**.
