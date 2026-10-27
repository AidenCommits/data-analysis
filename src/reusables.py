import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, StrMethodFormatter

###DATA###

def cost_by(df, column='kind'):
    """
    Event count and total cost by column, largest first.
    
    """
    
    return (
        df.groupby(column)
        .agg(events=('id', 'count'), total=('total_cost', 'sum'))
        .round(2)
        .sort_values('total', ascending=False)
    )


def per_100_jobs(df, total_jobs, column="kind"):
    """
    cost_by() plus rates per 100 jobs for a single week.
    
    """
    
    out = cost_by(df, column)
    out["events_per_100"] = (out["events"] / total_jobs * 100).round(1)
    out["cost_per_100"] = (out["total"] / total_jobs * 100).round(2)
    return out


def annualize(df, total_jobs, weeks=52, column="kind", data_weeks=1):
    """
    Per-100-job rates plus a projection if this week's pace continued for `weeks` weeks.
    total_jobs = jobs completed in the week the data covers.
    
    """
    
    out = cost_by(df, column)
    out["events_per_100"] = (out["events"] / total_jobs * 100).round(1)
    out["cost_per_100"] = (out["total"] / total_jobs * 100).round(2)

    annual_jobs = total_jobs / data_weeks * weeks
    # Calculated from the unrounded numbers so rounding doesn't creep into the projection
    out["annual_events"] = (out["events"] / total_jobs * annual_jobs).round(0)
    out["annual_cost"] = (out["total"] / total_jobs * annual_jobs).round(2)

    out.loc["Total"] = out.sum()
    return out


def annualize_weeks(weeks, weeks_per_year=52):
    """
    weeks = {"label": (dataframe, jobs_that_week), ...}
    
    """
    
    parts = []
    for label, (frame, jobs) in weeks.items():
        table = annualize(frame, jobs, weeks_per_year)
        table.insert(0, "week", label)
        parts.append(table)
    return pd.concat(parts)


def split_weeks(df, date_col="incident_date", label_fmt="Week of %b %d"):
    """
    Split one DataFrame into Monday-Sunday weeks, oldest first.
    Returns {"Week of Oct 12": dataframe, ...}, the shape compare_weeks()
    and weekly_cost_chart() take.

    """

    dates = pd.to_datetime(df[date_col])
    starts = dates.dt.to_period("W-SUN").dt.start_time
    return {start.strftime(label_fmt): group for start, group in df.groupby(starts)}


_METRIC_NAMES = {"events": "Events", "cost": "Cost ($)", "per_100": "Per 100 jobs ($)"}


def _summary(df, total_jobs, n_weeks=1, column="kind"):
    """
    One period by waste type. Events and cost are per-week averages when the
    period covers several weeks. per_100 is pooled (total cost / total jobs),
    so a busy week counts for more than a quiet one.

    """

    s = cost_by(df, column)
    return pd.DataFrame({
        "events": s["events"] / n_weeks,
        "cost": s["total"] / n_weeks,
        "per_100": s["total"] / total_jobs * 100,
    })


def running_average(weeks, jobs, column="kind"):
    """
    The average week across every week given, by waste type.
    weeks = {"label": dataframe, ...}   jobs = {"label": jobs_that_week, ...}

    """

    _check_jobs(weeks, jobs)
    combined = pd.concat(weeks.values())
    out = _summary(combined, sum(jobs[w] for w in weeks), n_weeks=len(weeks), column=column)
    return out.rename(columns=_METRIC_NAMES).round(2)


def _check_jobs(weeks, jobs):
    if not weeks:
        raise ValueError("No weeks given.")
    bad = [w for w in weeks if not jobs.get(w)]
    if bad:
        raise ValueError(f"Missing or zero job count for: {bad}")


def compare_weeks(weeks, jobs, running_avg=False, avg_label="Running avg",
                  avg_includes_current=True, column="kind"):
    """
    Compare weeks side by side by waste type. The last week given is the
    reporting week.

    weeks: {"label": dataframe, ...}, oldest first. Use split_weeks() to build it.
    jobs:  {"label": jobs_that_week, ...}, same labels.

    One week      -> that week alone.
    Two weeks     -> previous week vs reporting week, with the change.
    running_avg   -> adds a column for the average week and the change vs it.
                     avg_includes_current=False averages only the weeks before
                     the reporting week.

    """

    _check_jobs(weeks, jobs)
    labels = list(weeks)
    cur = labels[-1]

    periods = {w: _summary(df, jobs[w], column=column) for w, df in weeks.items()}
    if running_avg:
        pool = labels if avg_includes_current else labels[:-1]
        if not pool:
            raise ValueError("avg_includes_current=False needs at least two weeks.")
        periods[avg_label] = _summary(pd.concat([weeks[w] for w in pool]),
                                      sum(jobs[w] for w in pool),
                                      n_weeks=len(pool), column=column)

    table = pd.concat(periods, axis=1).fillna(0)
    table = table.sort_values((cur, "cost"), ascending=False)
    table.loc["Total"] = table.sum()                    # totals row, always last

    changes = {}
    versus = ([labels[-2]] if len(labels) > 1 else []) + ([avg_label] if running_avg else [])
    for other in versus:
        base = table[(other, "per_100")]
        changes[f"Change vs {other}"] = pd.DataFrame({
            "$ per 100": table[(cur, "per_100")] - base,
            "%": (table[(cur, "per_100")] / base.where(base != 0) - 1) * 100,   # no base -> blank
        })

    if changes:
        table = pd.concat([table, pd.concat(changes, axis=1)], axis=1)
    table.columns = pd.MultiIndex.from_tuples([(a, _METRIC_NAMES.get(b, b)) for a, b in table.columns])

    pct_cols = [c for c in table.columns if c[1] == "%"]
    table = table.round(2)
    table[pct_cols] = table[pct_cols].round(0)
    return table


def with_running_average(weeks, jobs, label="Running average"):
    """
    Returns (weeks, jobs) with the running average added in front of the real
    weeks, ready for weekly_cost_chart(). The average is the combined cost of
    all the weeks over their combined jobs, so it sits on the same cost-per-100-jobs
    scale as the weeks and matches the average compare_weeks() reports.

    """

    _check_jobs(weeks, jobs)
    out_weeks = {label: pd.concat(weeks.values()), **weeks}
    out_jobs = {label: sum(jobs[w] for w in weeks), **{w: jobs[w] for w in weeks}}
    return out_weeks, out_jobs


_UP, _DOWN, _FLAT = "#b42318", "#067647", "#667085"      # cost up = red, cost down = green


def _signed(value, fmt):
    if pd.isna(value):
        return "—"
    sign = "+" if value > 0 else "−" if value < 0 else ""
    return sign + fmt(abs(value))


def _change_color(value):
    if pd.isna(value) or value == 0:
        return f"color: {_FLAT}"
    return f"color: {_UP if value > 0 else _DOWN}; font-weight: 600"


_INK, _HEADER_BG, _ZEBRA_BG = "#101828", "#f2f4f7", "#f9fafb"
_FONT = "'Segoe UI', -apple-system, Helvetica, Arial, sans-serif"
_TOTAL_PROPS = [("font-weight", "700"), ("background-color", _HEADER_BG), ("border-top", "2px solid #475467")]


def _table_css(extra=()):
    """Shared table-level CSS. Colours are set explicitly so the table looks the same in any theme."""
    return [
        {"selector": "", "props": [("border-collapse", "collapse"), ("font-size", "14px"), ("font-family", _FONT)]},
        {"selector": "th", "props": [("padding", "8px 14px"), ("text-align", "center"),
                                     ("background-color", _HEADER_BG), ("color", _INK),
                                     ("font-weight", "600"), ("border-bottom", "1px solid #d0d5dd")]},
        {"selector": "td", "props": [("padding", "8px 14px"), ("border-bottom", "1px solid #eaecf0"),
                                     ("vertical-align", "top")]},
    ] + list(extra)


def _css(props):
    return "; ".join(f"{k}: {v}" for k, v in props)


def style_comparison(table, index_name=None):
    """
    Presentation version of a compare_weeks() table: dollar formats, signed and
    coloured changes (red = cost went up, green = down), a bold Total row,
    and a divider between each week's columns. Returns a pandas Styler.

    """

    t = table.copy()
    t.index.name = index_name

    formats, change_cols, divider_positions = {}, [], []
    previous_top = None
    for pos, col in enumerate(t.columns):
        top, sub = col
        if top != previous_top and pos > 0:
            divider_positions.append(pos)
        previous_top = top
        if sub == "Events":
            whole = (t[col].dropna() % 1 == 0).all()
            formats[col] = "{:,.0f}" if whole else "{:,.1f}"
        elif sub == "Cost ($)":
            formats[col] = "${:,.2f}"
        elif sub.startswith("Per 100"):
            formats[col] = "${:,.0f}"
        elif sub.startswith("$ per 100"):
            formats[col] = lambda v: _signed(v, lambda a: f"${a:,.0f}")
            change_cols.append(col)
        elif sub == "%":
            formats[col] = lambda v: _signed(v, lambda a: f"{a:,.0f}%")
            change_cols.append(col)

    extra = [{"selector": f"th.col_heading.col{p}, td.col{p}", "props": [("border-left", "2px solid #d0d5dd")]}
             for p in divider_positions]
    extra.append({"selector": "th.row_heading", "props": [("text-align", "left"), ("background-color", "#ffffff"),
                                                          ("color", _INK), ("border-bottom", "1px solid #eaecf0")]})
    if len(t) and t.index[-1] == "Total":
        extra.append({"selector": f"th.row_heading.row{len(t) - 1}", "props": _TOTAL_PROPS})

    styler = t.style.format(formats, na_rep="—").set_table_styles(_table_css(extra))

    # cell styles, applied in this order: base look, change colours, Total row
    styler = styler.set_properties(**{"color": _INK, "background-color": "#ffffff", "text-align": "right"})
    if change_cols:
        colour = styler.map if hasattr(styler, "map") else styler.applymap
        styler = colour(_change_color, subset=pd.IndexSlice[:, change_cols])
    styler = styler.apply(lambda row: [_css(_TOTAL_PROPS) if row.name == "Total" else ""] * len(row), axis=1)
    return styler.set_uuid("comparison")


def style_details(df, wrap_columns=("What happened", "Job")):
    """
    Presentation version of the event-detail table, styled to match
    style_comparison(): shaded header, light row dividers with alternate-row
    shading, money and hours formats, text left-aligned, and long text wrapped
    (everything else stays on one line). Returns a pandas Styler.

    """

    t = df.reset_index(drop=True).copy()
    if "Date" in t.columns:
        t["Date"] = pd.to_datetime(t["Date"]).dt.strftime("%b %d")

    formats = {}
    for col in t.columns:
        if str(col).endswith("($)"):
            formats[col] = "${:,.2f}"
        elif col == "Hours":
            formats[col] = "{:,.1f}"
    text_cols = [c for c in t.columns if c not in formats]
    wrap = [c for c in wrap_columns if c in t.columns]
    no_wrap = [c for c in t.columns if c not in wrap]

    extra = [{"selector": "th", "props": [("text-align", "left")]}]
    styler = t.style.hide(axis="index").format(na_rep="").format(formats, na_rep="").set_table_styles(_table_css(extra))

    styler = styler.set_properties(**{"color": _INK, "background-color": "#ffffff", "text-align": "right"})
    styler = styler.set_properties(subset=text_cols, **{"text-align": "left"})
    styler = styler.set_properties(subset=no_wrap, **{"white-space": "nowrap"})
    if wrap:
        styler = styler.set_properties(subset=wrap, **{"white-space": "normal", "min-width": "300px"})
    styler = styler.apply(lambda row: [f"background-color: {_ZEBRA_BG}" if row.name % 2 else ""] * len(row), axis=1)
    return styler.set_uuid("details")


_PRINT_CSS = """
<style>
@page { size: landscape; margin: 0.5in; }
@media print {
  #T_comparison, #T_details { width: 100% !important; font-size: 11px !important; }
  #T_comparison *, #T_details * { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  #T_comparison th, #T_comparison td, #T_details th, #T_details td { padding: 4px 7px !important; }
  #T_details td { white-space: normal !important; min-width: 0 !important; }
  #T_details td.col0, #T_details td.col1, #T_details td.col2,
  #T_details td.col6, #T_details td.col7, #T_details td.col8 { white-space: nowrap !important; }
  #T_comparison tr, #T_details tr, img { break-inside: avoid; }
  h2 { break-after: avoid; }
}
</style>
"""


def print_setup():
    """
    Call once near the top of the report. Makes the printed (or saved-as-PDF)
    report landscape by default and shrinks the two tables so they fit the page.
    Has no visible output.

    """

    from IPython.display import HTML, display
    display(HTML(_PRINT_CSS))


def split_baseline(df, first_id=3, last_id=16):
    """Legacy: separates the imported baseline rows by id. Not needed once the baseline is retired."""
    baseline = df[df["id"].between(first_id, last_id)]
    return baseline, df.drop(baseline.index)


def run_checks(df, checks):
    """checks = {"label": boolean Series, ...} -> one row per check."""
    out = pd.DataFrame({
        "rows_flagged": {k: int(v.fillna(False).sum()) for k, v in checks.items()},
    })
    out["of_total"] = len(df)
    out["pct"] = (out["rows_flagged"] / len(df) * 100).round(1)
    return out.sort_values("rows_flagged", ascending=False)


def reconcile(label, db_value, source_value, tolerance_pct=2):
    diff = db_value - source_value
    pct = diff / source_value * 100 if source_value else float("nan")
    return pd.Series({"item": label, "database": round(db_value, 2), "source": source_value,
                      "difference": round(diff, 2), "pct": round(pct, 1), "ok": abs(pct) <= tolerance_pct})

###CHARTS###

def cost_events_chart(summary, title=None, filename=None):
    """
    Side-by-side horizontal bars from a cost_by() result:
    total cost on the left, number of events on the right.
    
    """
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4), sharey=True)

    summary["total"].plot(kind="barh", ax=ax1, color="#2b6cb0", width=0.7)
    ax1.set_title("Total cost ($)")
    ax1.bar_label(ax1.containers[0], fmt="$%.2f", padding=3)
    ax1.set_xlim(0, summary["total"].max() * 1.4)
    ax1.spines["right"].set_visible(False)

    summary["events"].plot(kind="barh", ax=ax2, color="#dd6b20", width=0.7)
    ax2.set_title("Number of events")
    ax2.bar_label(ax2.containers[0], padding=3)
    ax2.set_xlim(0, summary["events"].max() * 1.12)
    ax2.xaxis.set_major_locator(MaxNLocator(integer=True))

    ax1.invert_yaxis()
    ax1.set_ylabel("")

    if title:
        fig.suptitle(title, fontsize=13)
    plt.tight_layout()
    fig.subplots_adjust(wspace=0.12)

    if filename:
        plt.savefig(filename, dpi=150, bbox_inches="tight")
    plt.show()

def weekly_cost_chart(weeks, jobs, title="Waste cost per 100 jobs", filename=None):
    """
    Line chart of cost per 100 jobs by waste type across any number of weeks.

    weeks: {"label": dataframe, ...} in the order they should appear
    jobs:  {"label": total_jobs_that_week, ...}, same labels as weeks
    
    """
    
    missing = set(weeks) - set(jobs)
    if missing:
        raise ValueError(f"No job count given for: {sorted(missing)}")

    table = pd.DataFrame(
        {label: frame.groupby("kind")["total_cost"].sum() for label, frame in weeks.items()}
    ).fillna(0).T
    table = table.div(pd.Series(jobs).reindex(table.index), axis=0) * 100
    table["Total"] = table.sum(axis=1)

    ax = table.plot(kind="line", marker="o", figsize=(9, 4.5), linewidth=2)
    colors = {line.get_label(): line.get_color() for line in ax.lines}

    n = len(table)
    ax.set_xticks(range(n))
    ax.set_xticklabels(table.index)
    ax.set_xlim(-0.4, n - 0.6)
    ax.set_ylim(-table.values.max() * 0.04, table.values.max() * 1.2)   # a little room so $0 points don't sit on the axis

    # Labels: left of the first week, right of the last, above the ones between.
    # Labels at the same week are nudged apart so they never overlap.
    min_gap = table.values.max() * 0.06
    for i in range(n):
        last_y = None
        for col, y in table.iloc[i].sort_values().items():
            ty = y if last_y is None else max(y, last_y + min_gap)
            last_y = ty
            if i == n - 1:
                offset, ha, va = (7, 0), "left", "center"
            elif i == 0:
                offset, ha, va = (-7, 0), "right", "center"
            else:
                offset, ha, va = (0, 7), "center", "bottom"
            ax.annotate(f"${y:,.0f}", (i, ty), textcoords="offset points", xytext=offset,
                        ha=ha, va=va, color=colors[col], fontsize=9, fontweight="bold")

    ax.set_title(title)
    ax.set_ylabel("Cost per 100 jobs ($)")
    ax.set_xlabel("")
    ax.yaxis.set_major_formatter(StrMethodFormatter("${x:,.0f}"))
    ax.legend(title="", loc="center left", bbox_to_anchor=(1.02, 0.5))
    plt.tight_layout()
    if filename:
        plt.savefig(filename, dpi=150, bbox_inches="tight")
    plt.show()