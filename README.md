# Acme Marketing Funnel Dashboard

A one-page leadership view of how marketing drives pipeline and revenue, built from an 18-month raw CRM export (`data/acme_marketing_funnel_data.csv`).

**Live:** https://runlayer-take-home.vercel.app

## What I found

1. **Referrals are 6.5% of leads but 31% of closed-won revenue.** 36% of referral leads become opportunities, against 9% of all other leads. The edge is in conversion, not win rate.
2. **Events convert leads to opportunities at twice the rate of other leads** (20.9% vs 10.2%, p < 0.001), consistently across all three event programs.
3. **Paid Social and Content Syndication are 27% of leads and produced one won deal.** They fall behind at every funnel stage.
4. **Leads grew 40% (H1 2025 → H1 2026) but opportunities from those leads grew 8%.** The MQL rate didn't move; the shortfall is after MQL. Borderline statistically (p = 0.059), so it's a signal to check with Sales, not a proven decline.

Marketing programs produced 73% of pipeline created and 60% of closed-won revenue; customer/partner referrals and SDR outbound account for the rest. There is no spend data, so nothing here is a cost or ROI claim.

## What I'd fix in the CRM first

Every data problem in the export is a process gap. The page closes with the control for each, with an owner:

1. **Deal amounts wrong or missing:** amount required at Closed Won; approval above $200K or 10× the size band's largest deal.
2. **No spend in the CRM:** monthly spend per campaign on the campaign record, so cost per opportunity is a standard report.
3. **MQLs with no outcome:** follow-up SLA, required rejection reason, auto-recycle after 30 days without progress.
4. **Expected close dates left to lapse:** weekly past-due close-date report by owner before each forecast call.
5. **Lead source typed by hand:** picklist set from campaign/UTM, locked edits, a UTM naming convention.
6. **Duplicates and no account key:** duplicate rules on forms and imports; lead-to-account matching by domain.

## How it works

```
data/acme_marketing_funnel_data.csv   raw export (never edited)
        │
        ▼
scripts/clean.py                      dedupes, maps channels, fixes 100× deal amounts,
        │                             computes every metric and significance flag,
        │                             and writes up every data-quality decision
        ▼
data.js  +  data/clean_leads.csv      numbers for the page / cleaned lead-level file
        │
        ▼
index.html + app.js                   static page; charts via Chart.js 4.5.1 (vendor/, MIT)
```

There is no build step and no framework. Vercel serves the folder as a static site. Every metric on the page comes from `data.js` (a few framing words such as "twice" are hand-written). Text and tables render before the charts, so the page still reads correctly if Chart.js fails to load.

## Run it locally

```bash
pip install -r scripts/requirements.txt   # Python 3.10+; numpy, pandas, scipy
python3 scripts/clean.py                  # regenerates data.js and data/clean_leads.csv
git diff --exit-code data.js data/clean_leads.csv && echo "reproduced exactly"
open index.html                           # or double-click it; no server needed
```

## Deploy

Import the repo in Vercel with framework preset **Other**, no build command, and the repo root as the output directory.

## Data decisions

| Issue | Handling |
|---|---|
| 15 exact duplicate rows (one carried opportunity OPP-7030) | Removed |
| `lead_source` has 18 spellings for 8 channels | Channel taken from `campaign`, which maps 1:1 to a channel |
| 6 closed-won amounts 100× too high ($16.5M of $18.5M raw revenue) | Divided by 100. Rule: ≥ $200K, an exact multiple of $10K (every other amount is rounded to $100), and > 10× the largest other deal in its company-size band. Excluding them instead gives $2.08M revenue vs $2.24M |
| 4 closed-won deals with no amount (3 are referrals) | Counted as wins; left out of revenue and average deal |
| Outcomes stop at Jun 30, 2026; funnel activity runs to Aug 17, 2026 | Won/lost/revenue reported as of Jun 30; nothing closed after it, though 24 open deals were due to. Flagged for the CRM owner |
| Recent cohorts haven't had time to close | Win rate uses closed deals only; win rate and revenue per lead aren't trended |
| 885 MQL/SQL leads stalled with no rejected/recycled status | Shown as "did not advance", flagged as a process gap |
| No spend data | No CAC, ROI or "efficiency" claims; yield per lead only |

## How the findings were tested

- **Above/below-average flags** in the tables are two-sided Fisher exact tests of each row against all other leads, Bonferroni-adjusted for the number of rows in the table. Cells are only colored (with ▲/▼) when the gap clears that bar.
- **Headline takeaways 1–3** hold up statistically and under the alternative treatments of the corrected deal amounts. **Takeaway 4** is borderline (p = 0.059) and is labelled on the page as a signal to check, not a proven decline. Things that look striking but don't hold up (the webinar win rate, Paid Search's rank, region/industry/rep differences) are listed on the page under "Looks striking, but isn't solid enough to act on", with the reason.
- **Source split:** Referral and SDR Outbound are shown separately from marketing programs. Counting referral separately is a judgment call; it is stated on the page.

## Repo layout

```
index.html, app.js          the page
data.js                     generated by scripts/clean.py (don't edit by hand)
data/                       raw export + cleaned lead-level CSV
scripts/clean.py            all cleaning and metric logic
scripts/requirements.txt    Python dependencies
vendor/chart.umd.js(.map)   Chart.js 4.5.1 from npm (MIT)
```
