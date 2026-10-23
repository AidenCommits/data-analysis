"""
waste_pipeline.py - extract, validate, clean and summarize the waste incident log.

Put this file in your project's src/ folder. Typical use in a notebook:

    from waste_pipeline import prepare, assert_no_errors, weekly_summary, headline, pareto

    df, jobs, issues = prepare("incident_log.db", start="2026-09-01")
    issues                      # review everything the checks found
    assert_no_errors(issues)    # stop here if anything is truly broken
    weekly = weekly_summary(df, jobs)

Command line (the early-week refresh step):

    python waste_pipeline.py --db incident_log.db --snapshot-dir data/raw --out data/processed

Requires: pandas, numpy. Phase 2 note: only the load_* functions need to change when the data
moves to the company database; everything after them works on DataFrames.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_DB = os.environ.get("WASTE_DB", "incident_log.db")
KINDS = ["Reclean", "Repeat Trip", "Lost Item", "Breakage"]

TEXT_COLS = ["kind", "job_id", "job_type", "tech", "reason", "item", "last_location",
             "resolution", "notes", "reported_by"]
NUM_COLS = ["hours", "miles", "supplies_cost", "other_cost", "downtime_days",
            "labor_rate", "mileage_rate", "est_cost"]

# Fields that must be filled in for each incident type (mirrors the entry app).
REQUIRED_BY_KIND = {
    "Reclean": ["job_id", "tech", "reason", "hours"],
    "Repeat Trip": ["job_id", "tech", "item", "hours", "miles"],
    "Lost Item": ["item", "last_location"],
    "Breakage": ["item", "tech", "reason", "resolution", "other_cost"],
}

# Thresholds for the "that looks wrong" warnings. Adjust to your operation.
MAX_HOURS = 24
MAX_MILES = 500
MAX_COST = 5000
STALE_DAYS = 10


# --------------------------------------------------------------------------- extract
def snapshot_db(db_path: str = DEFAULT_DB, out_dir: str = "data/raw") -> Path:
    """Safe, dated copy of the database (uses SQLite's backup API, fine while the app is open)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"{date.today().isoformat()}_incident_log.db"
    src, dst = sqlite3.connect(db_path), sqlite3.connect(dest)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
    return dest


def _connect_readonly(db_path: str) -> sqlite3.Connection:
    p = Path(db_path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Database not found: {p}")
    return sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True)


def load_incidents(db_path: str = DEFAULT_DB, start: str | None = None, end: str | None = None,
                   include_void: bool = False) -> pd.DataFrame:
    """Raw incident rows. Opens the database read-only so a notebook can never change it."""
    sql, params = "SELECT * FROM incidents WHERE 1=1", []
    if not include_void:
        sql += " AND voided = 0"
    if start:
        sql += " AND incident_date >= ?"
        params.append(str(start))
    if end:
        sql += " AND incident_date <= ?"
        params.append(str(end))
    con = _connect_readonly(db_path)
    try:
        return pd.read_sql_query(sql + " ORDER BY incident_date, id", con, params=params)
    finally:
        con.close()


def load_weekly_jobs(db_path: str = DEFAULT_DB) -> pd.DataFrame:
    """Total jobs per week, with week_start normalized to the Monday of that week."""
    con = _connect_readonly(db_path)
    try:
        jobs = pd.read_sql_query("SELECT week_start, total_jobs FROM weekly_jobs", con)
    finally:
        con.close()
    jobs["week_start"] = _to_monday(pd.to_datetime(jobs["week_start"], format="%Y-%m-%d", errors="coerce"))
    return (jobs.dropna(subset=["week_start"]).drop_duplicates("week_start", keep="last")
            .sort_values("week_start").reset_index(drop=True))


# --------------------------------------------------------------------------- clean
def _to_monday(dates: pd.Series) -> pd.Series:
    return (dates - pd.to_timedelta(dates.dt.weekday, unit="D")).dt.normalize()


def clean_incidents(raw: pd.DataFrame) -> pd.DataFrame:
    """Standardize types and text, rebuild every cost from its parts, add reporting columns."""
    df = raw.copy()

    for c in TEXT_COLS:
        s = df[c].astype("string").str.strip().str.replace(r"\s+", " ", regex=True)
        df[c] = s.replace("", pd.NA)
    df["job_id"] = df["job_id"].str.upper()                # J-1042 and j-1042 are the same job
    df["tech_key"] = df["tech"].str.lower()                # case-insensitive join key; display name kept as typed

    for c in NUM_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["repurchased"] = pd.to_numeric(df["repurchased"], errors="coerce").fillna(0).astype(int)

    df["incident_date"] = pd.to_datetime(df["incident_date"], format="%Y-%m-%d", errors="coerce")
    df["week_start"] = _to_monday(df["incident_date"])
    # Future-dated rows are a typo, not a real week. Keep them (so checks flag them) but out of trends.
    df.loc[df["incident_date"] > pd.Timestamp(date.today()), "week_start"] = pd.NaT
    df["month"] = df["incident_date"].dt.to_period("M").astype("string")

    # Rebuild cost from components so every event is costed the same way (playbook formula).
    hours, miles = df["hours"].fillna(0), df["miles"].fillna(0)
    labor_rate, mile_rate = df["labor_rate"].fillna(0), df["mileage_rate"].fillna(0)
    labor_kinds = df["kind"].isin(["Reclean", "Repeat Trip", "Lost Item", "Breakage"])
    mile_kinds = df["kind"].isin(["Reclean", "Repeat Trip"])
    replace_cost = df["other_cost"].fillna(0)
    replace_mask = (df["kind"] == "Breakage") | ((df["kind"] == "Lost Item") & (df["repurchased"] == 1))

    df["labor_cost"] = np.where(labor_kinds, hours * labor_rate, 0.0)
    df["mileage_cost"] = np.where(mile_kinds, miles * mile_rate, 0.0)
    df["supplies_cost_used"] = np.where(df["kind"] == "Reclean", df["supplies_cost"].fillna(0), 0.0)
    df["replacement_cost"] = np.where(replace_mask, replace_cost, 0.0)
    df["total_cost"] = (df["labor_cost"] + df["mileage_cost"]
                        + df["supplies_cost_used"] + df["replacement_cost"]).round(2)

    # One "what caused it" column so a single Pareto can mix incident types.
    df["driver"] = np.where(df["kind"].isin(["Reclean", "Breakage"]), df["reason"], df["item"])
    df["driver"] = df["driver"].astype("string").fillna("Unspecified")

    return df.sort_values(["incident_date", "id"]).reset_index(drop=True)


# --------------------------------------------------------------------------- validate
def run_checks(df: pd.DataFrame, weekly_jobs: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return a table of problems: severity (ERROR / WARN / INFO), check, incident_id, detail.
    ERROR = numbers would be wrong. WARN = probably wrong, look at it. INFO = context."""
    rows: list[dict] = []

    def add(sev, check, ids, detail):
        ids = list(ids) if not isinstance(ids, (str, int)) else [ids]
        for i in ids:                      # an empty list adds nothing; week-level checks pass [None]
            rows.append({"severity": sev, "check": check, "incident_id": i, "detail": detail})

    if df.empty:
        add("INFO", "no_data", [None], "No incidents in this date range.")
        return _issues_frame(rows)

    today = pd.Timestamp(date.today())

    add("ERROR", "bad_date", df.loc[df["incident_date"].isna(), "id"], "Incident date missing or unreadable")
    add("WARN", "future_date", df.loc[df["incident_date"] > today, "id"], "Incident date is in the future")

    unknown = df.loc[~df["kind"].isin(KINDS), "id"]
    add("ERROR", "unknown_kind", unknown, "Incident type not recognized")

    for kind, cols in REQUIRED_BY_KIND.items():
        sub = df[df["kind"] == kind]
        for col in cols:
            add("ERROR", "missing_required", sub.loc[sub[col].isna(), "id"], f"{kind}: '{col}' is empty")

    for col in NUM_COLS:
        add("ERROR", "negative_value", df.loc[df[col] < 0, "id"], f"'{col}' is negative")
    add("WARN", "implausible_hours", df.loc[df["hours"] > MAX_HOURS, "id"], f"More than {MAX_HOURS} hours")
    add("WARN", "implausible_miles", df.loc[df["miles"] > MAX_MILES, "id"], f"More than {MAX_MILES} miles")
    add("WARN", "implausible_cost", df.loc[df["total_cost"] > MAX_COST, "id"], f"Cost over ${MAX_COST:,}")
    add("WARN", "zero_cost", df.loc[df["total_cost"] == 0, "id"], "Event has $0 cost; are hours or cost missing?")

    mismatch = df["est_cost"].notna() & ((df["est_cost"] - df["total_cost"]).abs() > 0.01)
    add("WARN", "cost_mismatch", df.loc[mismatch, "id"], "Stored estimate differs from recalculated cost")

    key = ["incident_date", "kind", "job_id", "tech", "reason", "item"]
    dup = df.duplicated(subset=key, keep=False)
    add("WARN", "possible_duplicate", df.loc[dup, "id"], "Same date, type, job, tech and reason as another entry")

    # Context checks that protect the rates and trends.
    valid = df.dropna(subset=["week_start"])
    if weekly_jobs is not None:
        have = set(weekly_jobs["week_start"])
        missing = sorted(set(valid["week_start"]) - have)
        for w in missing:
            add("WARN", "no_job_count", [None], f"No total job count for week of {w.date()}; per-100 rates unavailable")
    if not valid.empty:
        weeks = pd.date_range(valid["week_start"].min(), valid["week_start"].max(), freq="7D")
        quiet = sorted(set(weeks) - set(valid["week_start"]))
        for w in quiet:
            add("INFO", "quiet_week", [None], f"No incidents logged for week of {w.date()} (real zero, or under-reporting?)")
        last = valid["incident_date"].max()
        if (today - last).days > STALE_DAYS:
            add("INFO", "stale_log", [None], f"Last entry was {last.date()}; confirm nothing is waiting to be entered")

    return _issues_frame(rows)


def _issues_frame(rows: list[dict]) -> pd.DataFrame:
    cols = ["severity", "check", "incident_id", "detail"]
    out = pd.DataFrame(rows, columns=cols)
    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    return out.sort_values("severity", key=lambda s: s.map(order), kind="stable").reset_index(drop=True)


def assert_no_errors(issues: pd.DataFrame) -> None:
    """Call before building a report so bad data cannot silently produce a wrong number."""
    errs = issues[issues["severity"] == "ERROR"]
    if len(errs):
        sample = errs.head(10).to_string(index=False)
        raise ValueError(f"{len(errs)} data error(s) must be fixed in the entry app first:\n{sample}")


# --------------------------------------------------------------------------- summarize
def weekly_summary(df: pd.DataFrame, jobs: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per week (zero-filled), cost by type, per-100-jobs rates, and the prior 4-week average."""
    d = df.dropna(subset=["week_start"])
    if d.empty:
        return pd.DataFrame()
    weeks = pd.date_range(d["week_start"].min(), d["week_start"].max(), freq="7D")
    cost = (d.pivot_table(index="week_start", columns="kind", values="total_cost", aggfunc="sum")
            .reindex(index=weeks, columns=KINDS).fillna(0.0))
    counts = (d.pivot_table(index="week_start", columns="kind", values="id", aggfunc="count")
              .reindex(index=weeks, columns=KINDS).fillna(0).astype(int))

    out = cost.add_suffix(" cost").join(counts.add_suffix(" events"))
    out["total_cost"] = cost.sum(axis=1)
    out["events"] = counts.sum(axis=1)
    out.index.name = "week_start"

    if jobs is not None and not jobs.empty:
        out = out.join(jobs.set_index("week_start")["total_jobs"])
        j = out["total_jobs"].where(out["total_jobs"] > 0)
        out["recleans_per_100_jobs"] = out["Reclean events"] / j * 100
        out["events_per_100_jobs"] = out["events"] / j * 100
        out["cost_per_100_jobs"] = out["total_cost"] / j * 100

    out["avg_prior_4wk"] = out["total_cost"].shift(1).rolling(4, min_periods=1).mean()
    out["vs_avg_pct"] = (out["total_cost"] / out["avg_prior_4wk"].replace(0, np.nan) - 1) * 100
    return out.reset_index()


def headline(weekly: pd.DataFrame, week_start: str | None = None) -> dict:
    """The top-of-report numbers for one week (default: the latest week in the data)."""
    row = weekly.iloc[-1] if week_start is None else weekly[weekly["week_start"] == pd.Timestamp(week_start)].iloc[0]
    keys = ["week_start", "total_cost", "avg_prior_4wk", "vs_avg_pct", "events",
            "recleans_per_100_jobs", "cost_per_100_jobs"]
    return {k: row[k] for k in keys if k in row.index}


def pareto(df: pd.DataFrame, top: int = 10, by_kind: bool = True) -> pd.DataFrame:
    """Where the money goes: cost and cumulative share by cause (largest first)."""
    keys = ["kind", "driver"] if by_kind else ["driver"]
    g = (df.groupby(keys).agg(events=("id", "count"), total_cost=("total_cost", "sum"))
         .sort_values("total_cost", ascending=False).reset_index())
    g["share_pct"] = g["total_cost"] / g["total_cost"].sum() * 100
    g["cum_share_pct"] = g["share_pct"].cumsum()
    g["label"] = g["kind"] + ": " + g["driver"] if by_kind else g["driver"]
    return g.head(top)


def by_tech(df: pd.DataFrame, jobs_by_tech: pd.DataFrame | None = None) -> pd.DataFrame:
    """Waste per tech. Pass jobs_by_tech (columns: tech, jobs) from the CT report to add per-100-job rates.
    Always read these alongside the event counts: small numbers are noisy."""
    d = df.dropna(subset=["tech_key"])
    g = d.groupby("tech_key").agg(tech=("tech", "first"), events=("id", "count"),
                                  total_cost=("total_cost", "sum"), hours=("hours", "sum"))
    counts = (d.pivot_table(index="tech_key", columns="kind", values="id", aggfunc="count")
              .reindex(columns=KINDS).fillna(0).astype(int).add_suffix(" events"))
    g = g.join(counts)
    if jobs_by_tech is not None:
        jb = jobs_by_tech.assign(tech_key=jobs_by_tech["tech"].astype("string").str.strip().str.lower())
        g = g.join(jb.groupby("tech_key")["jobs"].sum())
        j = g["jobs"].where(g["jobs"] > 0)
        g["events_per_100_jobs"] = g["events"] / j * 100
        g["cost_per_100_jobs"] = g["total_cost"] / j * 100
    return g.sort_values("total_cost", ascending=False).reset_index(drop=True)


def repeat_offenders(df: pd.DataFrame, by: tuple = ("kind", "item"), min_count: int = 2) -> pd.DataFrame:
    """Same forgotten item, same breakable equipment, same job type: things worth a process fix."""
    g = (df.groupby(list(by), dropna=True).agg(events=("id", "count"), total_cost=("total_cost", "sum"))
         .reset_index())
    return g[g["events"] >= min_count].sort_values(["events", "total_cost"], ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- one-call helper + CLI
def prepare(db_path: str = DEFAULT_DB, start: str | None = None, end: str | None = None,
            snapshot: bool = False, snapshot_dir: str = "data/raw"):
    """Load, clean and check in one call. Returns (clean_df, weekly_jobs, issues)."""
    if snapshot:
        snapshot_db(db_path, snapshot_dir)
    df = clean_incidents(load_incidents(db_path, start, end))
    jobs = load_weekly_jobs(db_path)
    return df, jobs, run_checks(df, jobs)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Refresh: snapshot, clean and check the waste log.")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--snapshot-dir", default="data/raw")
    ap.add_argument("--out", default="data/processed")
    ap.add_argument("--no-snapshot", action="store_true")
    a = ap.parse_args(argv)

    df, jobs, issues = prepare(a.db, a.start, a.end, snapshot=not a.no_snapshot, snapshot_dir=a.snapshot_dir)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "incidents_clean.csv", index=False)
    issues.to_csv(out / "data_issues.csv", index=False)

    print(f"{len(df)} incidents loaded, {len(jobs)} weekly job counts.")
    print(issues["severity"].value_counts().reindex(["ERROR", "WARN", "INFO"]).fillna(0).astype(int).to_string())
    if len(issues):
        print("\n" + issues.head(25).to_string(index=False))
    print(f"\nWrote {out / 'incidents_clean.csv'} and {out / 'data_issues.csv'}")
    return 1 if (issues["severity"] == "ERROR").any() else 0


if __name__ == "__main__":
    sys.exit(main())
