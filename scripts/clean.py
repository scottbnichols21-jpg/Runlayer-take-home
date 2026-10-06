#!/usr/bin/env python3
"""Clean the raw CRM export and compute every number the dashboard shows.

    python3 scripts/clean.py

Reads   data/acme_marketing_funnel_data.csv   raw export, never edited
Writes  data.js                               window.DATA, read by index.html / app.js
        data/clean_leads.csv                  one row per lead, with every fix applied

Each cleaning decision is made here, in code, and described in DATA.quality so the
page can show it. Re-running the script regenerates data.js byte for byte.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency, fisher_exact

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "acme_marketing_funnel_data.csv"
OUT_JS = ROOT / "data.js"
OUT_CSV = ROOT / "data" / "clean_leads.csv"

DATES = ["created_date", "mql_date", "sql_date", "opportunity_created_date", "close_date"]
OPEN_STAGES = ["Discovery", "Demo", "Proposal", "Negotiation"]
SIZE_ORDER = ["1-50", "51-200", "201-1000", "1001-5000", "5000+"]
ALPHA = 0.05  # significance level for the above/below-average flags, split across the rows of each table

# Channel comes from campaign. Every campaign belongs to exactly one channel;
# lead_source is free text with 18 spellings of these 8 values.
CAMPAIGN_CHANNEL = {
    "Brand Search Q": "Paid Search",
    "Competitor Keywords": "Paid Search",
    "Generic - Analytics": "Paid Search",
    "LinkedIn Lead Gen": "Paid Social",
    "LinkedIn Thought Leadership": "Paid Social",
    "Meta Retargeting": "Paid Social",
    "SEO - Blog": "Organic Search",
    "SEO - Comparison Pages": "Organic Search",
    "Webinar: 2026 Benchmarks": "Webinar",
    "Webinar: Pipeline Masterclass": "Webinar",
    "Webinar: RevOps 101": "Webinar",
    "Ebook: State of Marketing": "Content Syndication",
    "Whitepaper Syndication": "Content Syndication",
    "Customer Dinner Series": "Events",
    "Regional Roadshow": "Events",
    "SaaStr Booth": "Events",
    "Customer Referral": "Referral",
    "Partner Referral": "Referral",
    "SDR Outbound - Enterprise": "Outbound",
    "SDR Outbound - Mid-Market": "Outbound",
}
# Who generated the lead. Treating Referral as its own source is a judgment call:
# referral programs are often run by marketing, but the lead comes from a customer or partner.
CHANNEL_SOURCE = {"Referral": "Customer & partner referral", "Outbound": "SDR outbound"}
MARKETING = "Marketing programs"
SOURCE_ORDER = [MARKETING, "Customer & partner referral", "SDR outbound"]


def fmt_day(ts):
    return f"{ts:%b} {ts.day}, {ts.year}"


def money(x):
    """Compact dollar label for prose generated here (the page formats its own numbers)."""
    if abs(x) >= 1e6:
        return f"${x / 1e6:.2f}M"
    if abs(x) >= 1e4:
        k = x / 1e3
        return f"${k:.0f}K" if k == int(k) else f"${k:.1f}K"
    return f"${x:,.0f}"


def pct(a, b, d=4):
    """Percent, stored unrounded (4 dp) so the page rounds exactly once."""
    return round(a / b * 100, d) if b else None


def words(n):
    return ["no", "one", "two", "three", "four", "five", "six"][n] if 0 <= n <= 6 else f"{n:,}"


def sig(p):
    """p-value to two significant figures."""
    return float(f"{p:.2g}")


def fisher_p(a_yes, a_n, b_yes, b_n):
    return float(fisher_exact([[a_yes, a_n - a_yes], [b_yes, b_n - b_yes]])[1])


# ---------------------------------------------------------------- load + clean
raw = pd.read_csv(RAW)
quality = []

dup_mask = raw.duplicated(keep="first")
dup_opps = sorted(raw.loc[dup_mask, "opportunity_id"].dropna().unique())
df = raw.loc[~dup_mask].copy()
assert df.lead_id.is_unique, "lead_id should be unique once exact duplicates are removed"
for c in DATES:
    df[c] = pd.to_datetime(df[c])

assert set(df.campaign) == set(CAMPAIGN_CHANNEL), "unmapped campaign in the export"
df["channel"] = df.campaign.map(CAMPAIGN_CHANNEL)
df["source"] = df.channel.map(CHANNEL_SOURCE).fillna(MARKETING)
n_label_fixes = int((df.lead_source != df.channel).sum())

# Deal amounts entered 100x too high. Rule: at least $200K, a round multiple of $10K
# (every other amount is rounded to $100), and more than 10x the largest other deal in
# the same company-size band. The rule catches exactly six deals.
opp_rows = df.opportunity_id.notna()
cand = opp_rows & (df.deal_amount_usd >= 200_000) & (df.deal_amount_usd % 10_000 == 0)
band_max = df[opp_rows & ~cand].groupby("company_size").deal_amount_usd.max()
inflated = cand & (df.deal_amount_usd > 10 * df.company_size.map(band_max))
assert inflated.sum() == 6, f"expected 6 inflated amounts, found {inflated.sum()}"
df["amount_raw"] = df.deal_amount_usd
df["amount_corrected"] = inflated
df["amount"] = np.where(inflated, df.deal_amount_usd / 100, df.deal_amount_usd)
df["industry"] = df.industry.fillna("Unknown")

is_won = df.deal_stage == "Closed Won"
is_lost = df.deal_stage == "Closed Lost"
is_open = df.deal_stage.isin(OPEN_STAGES)
closed = is_won | is_lost

# Export timing: deal outcomes stop at one date, funnel activity runs past it.
last_close = df.loc[closed, "close_date"].max()
last_lead = df.created_date.max()
last_activity = df[["mql_date", "sql_date", "opportunity_created_date"]].max().max()
assert df.loc[is_open, "close_date"].min() > last_close, "an open deal has a close date before the last outcome"


# ---------------------------------------------------------------- metrics
def metrics(g, total_revenue):
    o = g[g.opportunity_id.notna()]
    w, l, op = o[o.deal_stage == "Closed Won"], o[o.deal_stage == "Closed Lost"], o[o.deal_stage.isin(OPEN_STAGES)]
    leads, won, lost = len(g), len(w), len(l)
    rev = float(w.amount.sum())
    return {
        "leads": leads,
        "mql": int(g.mql_date.notna().sum()),
        "sql": int(g.sql_date.notna().sum()),
        "opps": len(o),
        "won": won,
        "lost": lost,
        "open": len(op),
        "revenue": rev,
        "open_pipeline": float(op.amount.sum()),
        "pipeline_created": float(o.amount.sum()),
        "mql_rate": pct(g.mql_date.notna().sum(), leads),
        "lead_to_opp": pct(len(o), leads),
        "win_rate": pct(won, won + lost),
        "won_per_100": pct(won, leads),
        "rev_per_lead": round(rev / leads),
        "avg_deal": int(round(w.amount.mean())) if w.amount.notna().any() else None,
        "share_leads": pct(leads, len(df)),
        "share_revenue": pct(rev, total_revenue),
    }


# Rates tested against everyone outside the group (two-sided Fisher exact test).
FLAG_TESTS = {
    "mql_rate": lambda g: (int(g.mql_date.notna().sum()), len(g)),
    "lead_to_opp": lambda g: (int(g.opportunity_id.notna().sum()), len(g)),
    "won_per_100": lambda g: (int((g.deal_stage == "Closed Won").sum()), len(g)),
    "win_rate": lambda g: (int((g.deal_stage == "Closed Won").sum()), int(g.deal_stage.isin(["Closed Won", "Closed Lost"]).sum())),
}


def flags(g, rest, n_rows):
    """'hi' / 'lo' only where the gap to everyone else is statistically significant.

    Bonferroni: a table of n rows makes n comparisons per column, so each one must clear ALPHA / n.
    """
    out = {}
    for key, f in FLAG_TESTS.items():
        (ay, an), (by, bn) = f(g), f(rest)
        if an == 0 or bn == 0:
            continue
        if fisher_p(ay, an, by, bn) < ALPHA / n_rows:
            out[key] = "hi" if ay / an > by / bn else "lo"
    return out


def table(col, order=None, extra=None):
    rows = []
    n_rows = df[col].nunique()
    for name, g in df.groupby(col):
        r = metrics(g, total_rev)
        r["name"] = name
        r["flags"] = flags(g, df[df[col] != name], n_rows)
        if extra:
            r.update(extra(g))
        rows.append(r)
    if order:
        rows.sort(key=lambda r: order.index(r["name"]))
    else:
        rows.sort(key=lambda r: (-r["revenue"], r["name"]))
    return rows


total_rev = float(df.loc[is_won, "amount"].sum())
K = metrics(df, total_rev)
won_df, closed_df = df[is_won], df[closed]

channels = table("channel", extra=lambda g: {"source": g.source.iloc[0]})
campaigns = table("campaign", extra=lambda g: {"channel": g.channel.iloc[0]})
segments = {
    "Company size": table("company_size", order=SIZE_ORDER),
    "Region": table("region"),
    "Industry": table("industry"),
}
sources = []
for s in SOURCE_ORDER:
    g = df[df.source == s]
    r = metrics(g, total_rev)
    r["name"] = s
    r["share_pipeline"] = pct(r["pipeline_created"], K["pipeline_created"])
    r["channels"] = sorted(g.channel.unique())
    sources.append(r)

# ---------------------------------------------------------------- time
df["lead_q"] = df.created_date.dt.to_period("Q").astype(str)
df["lead_half"] = df.created_date.dt.year.astype(str) + np.where(df.created_date.dt.month <= 6, " H1", " H2")

quarters = []
for q, g in df.groupby("lead_q"):
    quarters.append({
        "q": q,
        "leads": len(g),
        "opps": int(g.opportunity_id.notna().sum()),
        "lead_to_opp": pct(g.opportunity_id.notna().sum(), len(g)),
    })

halves = {}
for h, g in df.groupby("lead_half"):
    halves[h] = {
        "leads": len(g),
        "opps": int(g.opportunity_id.notna().sum()),
        "lead_to_opp": pct(g.opportunity_id.notna().sum(), len(g)),
        "mql_rate": pct(g.mql_date.notna().sum(), len(g)),
        "mql_to_sql": pct(g.sql_date.notna().sum(), g.mql_date.notna().sum()),
        "sql_to_opp": pct(g.opportunity_id.notna().sum(), g.sql_date.notna().sum()),
    }

# Revenue by the quarter the deal closed. The export starts with leads created in
# Jan 2025, so the first two close quarters only contain deals from a few months of leads.
first_lead = df.created_date.min()
won_df = won_df.assign(close_q=won_df.close_date.dt.to_period("Q").astype(str))
revenue_by_q = []
for q, g in won_df.groupby("close_q"):
    revenue_by_q.append({"q": q, "revenue": float(g.amount.sum()), "deals": len(g)})
lag_p90 = float(((closed_df.close_date - closed_df.created_date).dt.days).quantile(0.9))
ramp_end = first_lead + pd.Timedelta(days=lag_p90)
for r in revenue_by_q:
    r["partial"] = bool(pd.Period(r["q"]).start_time < ramp_end)

# Open pipeline by expected close month
open_df = df[is_open]
pipeline_by_month = []
for m, g in open_df.groupby(open_df.close_date.dt.to_period("M")):
    due = g.close_date <= last_activity
    pipeline_by_month.append({
        "month": f"{m.to_timestamp():%b %Y}",
        "amount": float(g.amount.sum()),
        "deals": len(g),
        "overdue_amount": float(g.loc[due, "amount"].sum()),
        "overdue_deals": int(due.sum()),
    })
open_at_cutoff = open_df[open_df.opportunity_created_date <= last_close]
open_after_cutoff = open_df[open_df.opportunity_created_date > last_close]
past_due = open_df[open_df.close_date <= last_activity]
monthly_closes = closed_df.groupby(closed_df.close_date.dt.to_period("M")).size()
pipeline = {
    "open_deals": len(open_df),
    "open_amount": float(open_df.amount.sum()),
    "open_at_cutoff_deals": len(open_at_cutoff),
    "open_at_cutoff_amount": float(open_at_cutoff.amount.sum()),
    "created_after_cutoff_deals": len(open_after_cutoff),
    "created_after_cutoff_amount": float(open_after_cutoff.amount.sum()),
    "past_due_at_last_activity_deals": len(past_due),
    "past_due_at_last_activity_amount": float(past_due.amount.sum()),
    "typical_closes_per_month": round(float(monthly_closes.loc["2025-07":].median())),
    "by_month": pipeline_by_month,
}

# ---------------------------------------------------------------- takeaways
# 1. Referral
ref = next(c for c in channels if c["name"] == "Referral")
rest = df[df.channel != "Referral"]
ref_rest_l2o = pct(rest.opportunity_id.notna().sum(), len(rest))
ref_win_p = fisher_p(ref["won"], ref["won"] + ref["lost"], int((rest.deal_stage == "Closed Won").sum()),
                     int(rest.deal_stage.isin(["Closed Won", "Closed Lost"]).sum()))
unpriced = df[is_won & df.amount.isna()]
insight_referral = {
    "lead_to_opp_rest": ref_rest_l2o,
    "rev_per_lead_multiple": round(ref["rev_per_lead"] / K["rev_per_lead"], 1),
    "win_rate_rest": pct((rest.deal_stage == "Closed Won").sum(), rest.deal_stage.isin(["Closed Won", "Closed Lost"]).sum()),
    "win_rate_p": sig(ref_win_p),
    "unpriced_wins": int((unpriced.channel == "Referral").sum()),
    "unpriced_wins_total": len(unpriced),
}

# 2. Paid Social + Content Syndication
weak = ["Paid Social", "Content Syndication"]
g, rest = df[df.channel.isin(weak)], df[~df.channel.isin(weak)]
other_mkt = df[(df.source == MARKETING) & ~df.channel.isin(weak)]
og = g[g.opportunity_id.notna()]
open_weak = og[og.deal_stage.isin(OPEN_STAGES)].sort_values("amount", ascending=False)


def stage_rates(frame):
    mql, sql, opp = frame.mql_date.notna().sum(), frame.sql_date.notna().sum(), frame.opportunity_id.notna().sum()
    return {"mql_rate": pct(mql, len(frame)), "mql_to_sql": pct(sql, mql), "sql_to_opp": pct(opp, sql)}


insight_weak = {
    "channels": weak,
    "leads": len(g),
    "share_leads": pct(len(g), len(df)),
    "mql_rate": pct(g.mql_date.notna().sum(), len(g)),
    "mql_rate_rest": pct(rest.mql_date.notna().sum(), len(rest)),
    "opps": len(og),
    "lead_to_opp": pct(len(og), len(g)),
    "lead_to_opp_rest": pct(rest.opportunity_id.notna().sum(), len(rest)),
    "won": int((og.deal_stage == "Closed Won").sum()),
    "closed": int(og.deal_stage.isin(["Closed Won", "Closed Lost"]).sum()),
    "revenue": float(og.loc[og.deal_stage == "Closed Won", "amount"].sum()),
    "open_pipeline": float(open_weak.amount.sum()),
    "largest_open": float(open_weak.amount.iloc[0]),
    "largest_open_campaign": open_weak.campaign.iloc[0],
    "largest_open_stage": open_weak.deal_stage.iloc[0],
    "largest_open_age_days": int((last_activity - open_weak.opportunity_created_date.iloc[0]).days),
    "stages": stage_rates(g),
    "stages_other_marketing": stage_rates(other_mkt),
}

# 3. Events: the strongest marketing-run source on conversion
def yield_vs_rest(mask):
    g, rest = df[mask], df[~mask]
    gw, rw = int((g.deal_stage == "Closed Won").sum()), int((rest.deal_stage == "Closed Won").sum())
    gc = int(g.deal_stage.isin(["Closed Won", "Closed Lost"]).sum())
    rc = int(rest.deal_stage.isin(["Closed Won", "Closed Lost"]).sum())
    go, ro = int(g.opportunity_id.notna().sum()), int(rest.opportunity_id.notna().sum())
    return {
        "leads": len(g),
        "share_leads": pct(len(g), len(df)),
        "lead_to_opp": pct(go, len(g)),
        "lead_to_opp_rest": pct(ro, len(rest)),
        "lead_to_opp_p": sig(fisher_p(go, len(g), ro, len(rest))),
        "won_per_100": pct(gw, len(g)),
        "won_per_100_rest": pct(rw, len(rest)),
        "won_per_100_p": sig(fisher_p(gw, len(g), rw, len(rest))),
        "win_rate": pct(gw, gc),
        "win_rate_rest": pct(rw, rc),
        "win_rate_p": sig(fisher_p(gw, gc, rw, rc)),
    }


ev = next(c for c in channels if c["name"] == "Events")
ev_campaigns = [c for c in campaigns if c["channel"] == "Events"]
insight_events = yield_vs_rest(df.channel == "Events")
insight_events.update({
    "share_revenue": ev["share_revenue"],
    "programs": len(ev_campaigns),
    "program_lead_to_opp_range": [min(c["lead_to_opp"] for c in ev_campaigns), max(c["lead_to_opp"] for c in ev_campaigns)],
})

# Paid Search: the largest lead source, and the one whose rank depends most on the amount fix
ps = df[df.channel == "Paid Search"]
ps_corrected = ps[(ps.deal_stage == "Closed Won") & ps.amount_corrected]
rev_excl = df[is_won & ~df.amount_corrected].groupby("channel").amount.sum().sort_values(ascending=False)
close_call = ["Paid Search", "Organic Search", "Events", "Webinar"]
close_rev = [c["revenue"] for c in channels if c["name"] in close_call]
insight_paid_search = yield_vs_rest(df.channel == "Paid Search")
insight_paid_search.update({
    "revenue_from_corrected": float(ps_corrected.amount.sum()),
    "corrected_deals": len(ps_corrected),
    "revenue_rank": [c["name"] for c in channels].index("Paid Search") + 1,
    "revenue_rank_if_excluded": list(rev_excl.index).index("Paid Search") + 1,
    "close_call_channels": close_call,
    "close_call_spread": max(close_rev) - min(close_rev),
})

# 4. Lead growth vs opportunity growth (by the quarter/half the lead was created)
h0, h2 = halves["2025 H1"], halves["2026 H1"]
a, b = df[df.lead_half == "2025 H1"], df[df.lead_half == "2026 H1"]
# Shift-share: H1 2026's leads converting at H1 2025's per-channel rates
base_rates = a.groupby("channel").opportunity_id.apply(lambda s: s.notna().mean())
mix_expected = float((b.channel.map(base_rates)).mean() * 100)
# Where H1 2026 fell short of H1 2025's rates, stage by stage (the three parts add up to the gap)
r0 = {k: h0[k] / 100 for k in ("mql_rate", "mql_to_sql", "sql_to_opp")}
n_mql, n_sql = int(b.mql_date.notna().sum()), int(b.sql_date.notna().sum())
shortfall = {
    "at_mql": h2["leads"] * (r0["mql_rate"] - h2["mql_rate"] / 100) * r0["mql_to_sql"] * r0["sql_to_opp"],
    "at_mql_to_sql": n_mql * (r0["mql_to_sql"] - h2["mql_to_sql"] / 100) * r0["sql_to_opp"],
    "at_sql_to_opp": n_sql * (r0["sql_to_opp"] - h2["sql_to_opp"] / 100),
}
n_mql0, n_sql0 = int(a.mql_date.notna().sum()), int(a.sql_date.notna().sum())
# Quarter-by-quarter: is any single pair of quarters different by more than chance?
pair_ps = [
    fisher_p(x["opps"], x["leads"], y["opps"], y["leads"])
    for i, x in enumerate(quarters) for y in quarters[i + 1:]
]
later = df[df.lead_half != "2025 H1"]
# Completeness: how many opportunities could the newest leads still produce?
lags = (df.opportunity_created_date - df.created_date).dt.days.dropna()
runway = int((last_activity - last_lead).days)
late = df[df.created_date > last_activity - pd.Timedelta(days=int(lags.max()))]
seen = np.array([(lags <= (last_activity - t).days).mean() for t in late.created_date])
insight_growth = {
    "lead_growth": round((h2["leads"] / h0["leads"] - 1) * 100),
    "opp_growth": round((h2["opps"] / h0["opps"] - 1) * 100),
    "p": sig(fisher_p(h0["opps"], h0["leads"], h2["opps"], h2["leads"])),
    "mix_expected": round(mix_expected, 1),
    "later_quarters_range": [min(q["lead_to_opp"] for q in quarters[2:]), max(q["lead_to_opp"] for q in quarters[2:])],
    "later_rate": pct(later.opportunity_id.notna().sum(), len(later)),
    "min_pair_p": sig(min(pair_ps)),
    "shortfall_total": round(sum(shortfall.values())),
    "shortfall": {k: round(v) for k, v in shortfall.items()},
    "mql_to_sql_p": sig(fisher_p(n_sql0, n_mql0, n_sql, n_mql)),
    "sql_to_opp_p": sig(fisher_p(h0["opps"], n_sql0, h2["opps"], n_sql)),
    "runway_days": runway,
    "share_opps_within_runway": pct((lags <= runway).sum(), len(lags)),
    "expected_missing_opps": round(float((K["lead_to_opp"] / 100 * (1 - seen)).sum()), 1),
}

# Striking but not solid: kept off the headline list.
# The webinar window was picked after looking at the data; the page says so.
WEBINAR_WINDOW = ["2025Q3", "2025Q4"]
in_window = df.loc[closed_df.index, "lead_q"].isin(WEBINAR_WINDOW)
web_closed = closed_df[closed_df.channel == "Webinar"]
web_h2 = web_closed[in_window[web_closed.index]]
web_other = web_closed[~in_window[web_closed.index]]
rest_other = closed_df[(closed_df.channel != "Webinar") & ~in_window]
insight_not_solid = {
    "webinar_won": int((web_closed.deal_stage == "Closed Won").sum()),
    "webinar_closed": len(web_closed),
    "webinar_h2_won": int((web_h2.deal_stage == "Closed Won").sum()),
    "webinar_h2_closed": len(web_h2),
    "webinar_other_won": int((web_other.deal_stage == "Closed Won").sum()),
    "webinar_other_closed": len(web_other),
    "webinar_other_win_rate": pct((web_other.deal_stage == "Closed Won").sum(), len(web_other)),
    "rest_other_win_rate": pct((rest_other.deal_stage == "Closed Won").sum(), len(rest_other)),
    "rep_win_rates": [
        pct((g.deal_stage == "Closed Won").sum(), len(g), 0) for _, g in closed_df.groupby("owner")
    ],
}
insight_not_solid["rep_win_rate_range"] = [min(insight_not_solid["rep_win_rates"]), max(insight_not_solid["rep_win_rates"])]
del insight_not_solid["rep_win_rates"]

# Do groups differ by more than chance? (chi-square test across all groups at once)
def chi2_p(frame, col, rate):
    if rate == "win_rate":
        frame = frame[frame.deal_stage.isin(["Closed Won", "Closed Lost"])]
        hit = frame.deal_stage == "Closed Won"
    else:
        hit = frame.opportunity_id.notna()
    return sig(chi2_contingency(pd.crosstab(frame[col], hit))[1])


heterogeneity = {
    f"{col}_{rate}": chi2_p(df, col, rate)
    for col in ["company_size", "region", "industry", "owner"]
    for rate in ["lead_to_opp", "win_rate"]
    if not (col == "owner" and rate == "lead_to_opp")  # owner is only assigned at SQL
}
insight_not_solid["tests"] = heterogeneity
insight_not_solid["reps"] = int(df.owner.nunique())
rep_closed = closed_df.groupby("owner").size()
insight_not_solid["rep_closed_range"] = [int(rep_closed.min()), int(rep_closed.max())]
# Within each channel, do its campaigns differ? (smallest p across channels)
within = [
    chi2_p(df[df.channel == c], "campaign", rate)
    for c in df.channel.unique() if df.loc[df.channel == c, "campaign"].nunique() > 1
    for rate in ["lead_to_opp", "win_rate"]
]
insight_not_solid["campaign_within_channel_min_p"] = min(within)

# Company size: the one segment cut with a significant conversion gap
size_flagged = []
for r in segments["Company size"]:
    if "lead_to_opp" in r["flags"]:
        g, rest = df[df.company_size == r["name"]], df[df.company_size != r["name"]]
        size_flagged.append({
            "name": r["name"],
            "lead_to_opp": r["lead_to_opp"],
            "lead_to_opp_rest": pct(rest.opportunity_id.notna().sum(), len(rest)),
            "p": sig(fisher_p(int(g.opportunity_id.notna().sum()), len(g), int(rest.opportunity_id.notna().sum()), len(rest))),
        })
insight_size = {"flagged": size_flagged}

# ---------------------------------------------------------------- data notes
won_band_median = df[is_won & ~df.amount_corrected].groupby("company_size").amount.median()
corrected = df[df.amount_corrected].sort_values("amount_raw", ascending=False)
corrected_amounts = [
    {
        "opportunity_id": r.opportunity_id,
        "company_size": r.company_size,
        "amount_raw": float(r.amount_raw),
        "deal_amount_usd": float(r.amount),
        "won_median": float(won_band_median[r.company_size]),
    }
    for r in corrected.itertuples()
]
raw_closed_won = float(df.loc[is_won, "amount_raw"].sum())
revenue_if_excluded = float(df.loc[is_won & ~df.amount_corrected, "amount"].sum())
next_largest = float(df.loc[opp_rows & ~df.amount_corrected, "amount"].max())
other_round = df[opp_rows & ~df.amount_corrected & (df.deal_amount_usd % 10_000 == 0)]
unpriced_imputed = float(unpriced.company_size.map(won_band_median).sum())

stalled_mql = df[df.mql_date.notna() & df.sql_date.isna()]
stalled_sql = df[df.sql_date.notna() & df.opportunity_id.isna()]
max_mql_to_sql = int((df.sql_date - df.mql_date).dt.days.max())
max_sql_to_opp = int((df.opportunity_created_date - df.sql_date).dt.days.max())
stale_mql, stale_sql = len(stalled_mql), len(stalled_sql)
stale_old = int(((last_activity - stalled_mql.mql_date).dt.days > max_mql_to_sql).sum()
                + ((last_activity - stalled_sql.sql_date).dt.days > max_sql_to_opp).sum())
med_l2c = closed_df.close_date.sub(closed_df.created_date).dt.days

quality = [
    {
        "issue": "Duplicate rows",
        "count": int(dup_mask.sum()),
        "detail": f"{len(raw):,} rows but only {len(df):,} unique leads. One duplicate carried an opportunity ({', '.join(dup_opps)}), which would have double-counted pipeline.",
        "action": "Removed exact duplicates. No other lead or opportunity IDs repeat.",
    },
    {
        "issue": "Inconsistent lead source labels",
        "count": n_label_fixes,
        "detail": f"{raw.lead_source.nunique()} spellings for {df.channel.nunique()} channels (e.g. Event, PPC, Paid search, SEO, events). 'LinkedIn' even appears on Meta Retargeting leads.",
        "action": "Took channel from the campaign field instead. Every campaign maps to exactly one channel.",
    },
    {
        "issue": "Deal amounts 100× too high",
        "count": int(inflated.sum()),
        "detail": (
            f"Six closed-won deals between {money(corrected.amount_raw.min())} and {money(corrected.amount_raw.max())} made up "
            f"{money(corrected.amount_raw.sum())} of the {money(raw_closed_won)} raw closed-won total "
            f"({corrected.amount_raw.sum() / raw_closed_won:.0%}). All six are exact multiples of $10,000; only "
            f"{words(len(other_round))} other amount in the file is (a {money(other_round.amount.iloc[0])} {other_round.deal_stage.iloc[0].lower()} deal), "
            f"and every amount is rounded to $100. "
            f"The next-largest deal in the file is {money(next_largest)}. Divided by 100, each one falls inside the range of deals "
            f"for its company size, though all six sit below the median won deal for that size."
        ),
        "action": (
            f"Divided by 100 and flagged. If these six amounts were excluded instead, closed-won revenue would be "
            f"{money(revenue_if_excluded)} rather than {money(total_rev)}."
        ),
    },
    {
        "issue": "Closed-won deals with no amount",
        "count": len(unpriced),
        "detail": f"{insight_referral['unpriced_wins']} of the {len(unpriced)} are referral deals.",
        "action": (
            f"Counted as wins, left out of revenue and average deal size. Valued at the typical won deal for their company size, "
            f"they would add about {money(round(unpriced_imputed, -3))}."
        ),
    },
    {
        "issue": "Two different cut-off dates",
        "count": None,
        "detail": (
            f"Every closed deal closed on or before {fmt_day(last_close)}, and the last lead was created {fmt_day(last_lead)}, "
            f"but pipeline activity keeps being recorded after that (last MQL {fmt_day(df.mql_date.max())}, last SQL "
            f"{fmt_day(df.sql_date.max())}, last opportunity {fmt_day(df.opportunity_created_date.max())}). Nothing closed in between, against about "
            f"{pipeline['typical_closes_per_month']} closes a month before, even though {pipeline['past_due_at_last_activity_deals']} "
            f"open deals had expected close dates in that window."
        ),
        "action": (
            f"Won, lost and revenue are reported as of {fmt_day(last_close)}. Open pipeline includes the "
            f"{pipeline['created_after_cutoff_deals']} opportunities created after that date. Worth asking the CRM owner whether "
            f"later closes were left out of the export."
        ),
    },
    {
        "issue": "Recent leads haven't had time to close",
        "count": None,
        "detail": (
            f"A closed deal takes a median {int(med_l2c.median())} days from lead to close, so many 2026 leads are still in "
            f"open pipeline. Every opportunity is created within {int((df.opportunity_created_date - df.created_date).dt.days.max())} "
            f"days of its lead."
        ),
        "action": (
            "Win rate uses closed deals only. Lead-to-opportunity rate is safe to compare by quarter; win rate and revenue "
            "per lead are not, so they aren't trended. Revenue per lead for all leads is understated for the same reason."
        ),
    },
    {
        "issue": "No outcome for leads that stall",
        "count": stale_mql + stale_sql,
        "detail": (
            f"{stale_mql:,} MQLs never became SQLs and {stale_sql} SQLs never became opportunities. {stale_old:,} of these "
            f"{stale_mql + stale_sql:,} have waited longer than any lead ever took to advance ({max_mql_to_sql} days MQL to SQL, "
            f"{max_sql_to_opp} days SQL to opportunity), yet the export has no rejected or recycled status for them. "
            f"'Disqualified' is only ever used before MQL ({int((df.lead_status == 'Disqualified').sum())} leads)."
        ),
        "action": "Shown as 'did not advance', not as active pipeline. Worth checking whether Sales records a disposition anywhere.",
    },
    {
        "issue": "Missing industry and UTM medium",
        "count": int((df.industry == "Unknown").sum() + df.utm_medium.isna().sum() - ((df.industry == "Unknown") & df.utm_medium.isna()).sum()),
        "detail": (
            f"{int((df.industry == 'Unknown').sum())} leads have no industry and {int(df.utm_medium.isna().sum())} have no UTM medium "
            f"({words(int(((df.industry == 'Unknown') & df.utm_medium.isna()).sum()))} lead is missing both). The gaps show no pattern."
        ),
        "action": "Industry shown as 'Unknown'. UTM medium isn't used: channel comes from campaign, and webinars are tagged 'email'.",
    },
    {
        "issue": "Things I left alone",
        "count": None,
        "detail": (
            f"Canada has more leads than the US ({int((df.country == 'Canada').sum())} vs {int((df.country == 'United States').sum())}). "
            "The campaign 'Brand Search Q' looks truncated. The same company name appears in different industries and regions, "
            "so company can't be used as an account key. There is no spend data, so cost per lead, CAC and ROI can't be calculated."
        ),
        "action": "Reported as-is. These are questions for the CRM owner, not things to guess at.",
    },
]

# ---------------------------------------------------------------- CRM fixes
# Every data problem above is a process gap. These are the controls that would stop each one
# recurring, in rough order of how much it matters to the numbers leadership sees.
companies = df.groupby("company").agg(industries=("industry", "nunique"), leads=("lead_id", "size"))
crm_fixes = [
    {
        "title": "Deal amounts wrong or missing",
        "owner": "Sales Ops",
        "problem": (
            f"{words(int(inflated.sum())).capitalize()} closed-won amounts were 100× too high, so raw closed-won revenue reads "
            f"{money(raw_closed_won)} instead of {money(total_rev)}: off by {raw_closed_won / total_rev:.0f}× for anyone reporting "
            f"straight from the CRM. {words(len(unpriced)).capitalize()} more wins have no amount at all "
            f"({words(insight_referral['unpriced_wins'])} of them referrals), about {money(round(unpriced_imputed, -3))} off the books."
        ),
        "fix": (
            "Validation rules on Amount: required before a deal can move to Closed Won, and anything over $200K, or more than "
            "10× the largest deal for that company size, needs manager approval. A weekly exception report catches what slips through."
        ),
    },
    {
        "title": "No spend in the CRM",
        "owner": "Marketing Ops + Finance",
        "problem": (
            "Nothing here can say cost per opportunity, CAC or ROI by channel, which is the next question leadership will ask "
            "about every takeaway on this page."
        ),
        "fix": (
            "Load monthly spend per campaign (ad platforms, event invoices, syndication contracts) onto the campaign record, "
            "so cost per opportunity is a standard report by next quarter."
        ),
    },
    {
        "title": "MQLs with no outcome",
        "owner": "Marketing Ops + SDR lead",
        "problem": (
            f"{stale_mql:,} MQLs never became SQLs and {stale_sql} SQLs never became opportunities, and none has a rejected or "
            f"recycled status. There's no way to tell whether Sales turned them down or never followed up."
        ),
        "fix": (
            f"An MQL follow-up SLA, a required reason whenever Sales rejects a lead, and automatic recycling to nurture after "
            f"{max_mql_to_sql} days without progress (no MQL in this data advanced later than that)."
        ),
    },
    {
        "title": "Expected close dates left to lapse",
        "owner": "Sales Ops",
        "problem": (
            f"{pipeline['past_due_at_last_activity_deals']} open deals ({money(round(pipeline['past_due_at_last_activity_amount'], -3))}) "
            f"passed their expected close date without closing, and the export has no closes at all after {fmt_day(last_close)}."
        ),
        "fix": (
            "A weekly past-due close-date report by owner, cleared before each forecast call. Before building it, confirm "
            "whether later closes are just missing from the export."
        ),
    },
    {
        "title": "Lead source typed by hand",
        "owner": "Marketing Ops",
        "problem": (
            f"lead_source has {raw.lead_source.nunique()} spellings of {df.channel.nunique()} channels, "
            f"{int(df.utm_medium.isna().sum())} leads have no UTM medium, and webinar leads are tagged 'email'."
        ),
        "fix": (
            "Set lead source automatically from campaign and UTM through a picklist, lock manual edits, and publish a UTM "
            "naming convention that gives webinars their own medium."
        ),
    },
    {
        "title": "Duplicates and no account key",
        "owner": "Marketing Ops",
        "problem": (
            f"{int(dup_mask.sum())} duplicate rows got through (one carried an opportunity), and every one of the "
            f"{len(companies)} company names appears under more than one industry, so leads can't be rolled up to accounts."
            if (companies.industries > 1).all() else
            f"{int(dup_mask.sum())} duplicate rows got through (one carried an opportunity), and "
            f"{int((companies.industries > 1).sum())} company names appear under more than one industry."
        ),
        "fix": (
            "Duplicate rules on form fills and imports, plus lead-to-account matching by email domain, so account-level and "
            "multi-touch reporting become possible."
        ),
    },
]

# ---------------------------------------------------------------- write
DATA = {
    "meta": {
        "rows_raw": len(raw),
        "rows_clean": len(df),
        "lead_start": f"{df.created_date.min():%b %Y}",
        "lead_end": f"{df.created_date.max():%b %Y}",
        "last_close": fmt_day(last_close),
        "last_activity": fmt_day(last_activity),
        "lead_to_close_median_days": int(med_l2c.median()),
        "sales_cycle_median_days": int(closed_df.days_to_close.median()),
        "sales_cycle_median_days_won": int(won_df.days_to_close.median()),
        "priced_wins": int(won_df.amount.notna().sum()),
        "max_lead_to_opp_days": int((df.opportunity_created_date - df.created_date).dt.days.max()),
    },
    "kpis": K,
    "funnel": [
        {"stage": "Leads", "n": K["leads"]},
        {"stage": "MQL", "n": K["mql"]},
        {"stage": "SQL", "n": K["sql"]},
        {"stage": "Opportunity", "n": K["opps"]},
        {"stage": "Closed won", "n": K["won"]},
    ],
    "sources": sources,
    "channels": channels,
    "campaigns": campaigns,
    "quarters": quarters,
    "revenue_by_q": revenue_by_q,
    "halves": halves,
    "pipeline": pipeline,
    "segments": segments,
    "insights": {
        "referral": insight_referral,
        "weak": insight_weak,
        "events": insight_events,
        "paid_search": insight_paid_search,
        "growth": insight_growth,
        "not_solid": insight_not_solid,
        "size": insight_size,
    },
    "quality": quality,
    "crm_fixes": crm_fixes,
    "corrected_amounts": corrected_amounts,
    "raw_closed_won": raw_closed_won,
    "revenue_if_excluded": revenue_if_excluded,
}

OUT_JS.write_bytes((
    "// Generated by scripts/clean.py from data/acme_marketing_funnel_data.csv. Do not edit by hand.\n"
    "window.DATA = " + json.dumps(DATA, indent=1, ensure_ascii=False) + ";\n"
).encode("utf-8"))

keep = ["lead_id", "created_date", "company", "industry", "company_size", "region", "country", "channel", "source",
        "campaign", "lead_source", "utm_medium", "lead_status", "mql_date", "sql_date", "opportunity_id",
        "opportunity_created_date", "deal_stage", "amount", "amount_raw", "amount_corrected", "close_date", "owner",
        "days_to_close"]
out = df[keep].rename(columns={"lead_source": "lead_source_raw", "amount": "deal_amount_usd", "amount_raw": "deal_amount_usd_raw",
                               "days_to_close": "days_opp_to_close"})
out.to_csv(OUT_CSV, index=False, date_format="%Y-%m-%d", lineterminator="\n", encoding="utf-8")
print(f"{len(raw):,} raw rows -> {len(df):,} leads. Closed-won revenue {money(total_rev)} "
      f"(raw {money(raw_closed_won)}). Wrote {OUT_JS.name} and {OUT_CSV.relative_to(ROOT)}.")
