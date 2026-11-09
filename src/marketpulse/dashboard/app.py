"""MarketPulse desk. Reads local artifacts only; it does not fetch sources."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import altair as alt
import streamlit as st

from marketpulse.dashboard.data import (
    ZONE_LABEL,
    demo_root,
    daily_sentiment,
    filter_items,
    filter_observations,
    format_local,
    group_observations,
    label_mix,
    load_workspace,
    mean_score,
    safe_href,
    saved_windows,
)
from marketpulse.storage import PROJECT_ROOT

BULLISH = "#1b7a46"
BEARISH = "#9c2f2f"
NEUTRAL = "#5c6770"
INK = "#1c2834"
PRICE = "#0e5f73"


def _demo_requested() -> bool:
    return "--demo" in sys.argv or os.environ.get("MARKETPULSE_DASHBOARD_DEMO") == "1"


def _root_argument() -> Path | None:
    if "--root" in sys.argv:
        index = sys.argv.index("--root")
        if index + 1 < len(sys.argv):
            return Path(sys.argv[index + 1]).expanduser()
    env = os.environ.get("MARKETPULSE_DASHBOARD_ROOT")
    return Path(env).expanduser() if env else None


def _escape(value) -> str:
    text = " ".join(str(value).split())
    for char in ("\\", "*", "_", "[", "]", "`", "|", "<", ">"):
        text = text.replace(char, "\\" + char)
    return text


def _load():
    mode = st.session_state.get("data_mode", "Synthetic demo" if _demo_requested() else "Saved folder")
    if mode == "Synthetic demo":
        return load_workspace(demo_root(), kind="synthetic_demo")
    root = Path(st.session_state.get("artifact_root") or _root_argument() or PROJECT_ROOT)
    return load_workspace(root)


def _price_chart(points):
    frame = [{"observed_at": row["observed_at"], "price": row["price"]} for row in points]
    chart = (
        alt.Chart(alt.Data(values=frame))
        .mark_line(point=True, color=PRICE)
        .encode(
            x=alt.X("observed_at:T", title=f"Observed at (UTC axis; labels below use {ZONE_LABEL})"),
            y=alt.Y("price:Q", title="Share price", scale=alt.Scale(domain=[0, 1])),
            tooltip=["observed_at:T", "price:Q"],
        )
        .properties(height=260)
    )
    return chart


def _sentiment_charts(items):
    mix = label_mix(items)
    bars = (
        alt.Chart(alt.Data(values=mix))
        .mark_bar()
        .encode(
            x=alt.X("label:N", title="Label", sort=["bullish", "bearish", "neutral"]),
            y=alt.Y("mentions:Q", title="Retained mentions"),
            color=alt.Color(
                "label:N",
                scale=alt.Scale(
                    domain=["bullish", "bearish", "neutral"],
                    range=[BULLISH, BEARISH, NEUTRAL],
                ),
                legend=None,
            ),
            tooltip=["label:N", "mentions:Q"],
        )
        .properties(height=260)
    )
    daily = daily_sentiment(items)
    if len(daily) < 2:
        line = None
    else:
        line = (
            alt.Chart(alt.Data(values=daily))
            .mark_line(point=True, color=INK)
            .encode(
                x=alt.X("local_date:T", title=f"Publication date ({ZONE_LABEL})"),
                y=alt.Y("mean_score:Q", title="Mean item score", scale=alt.Scale(domain=[-1, 1])),
                tooltip=["local_date:T", "mean_score:Q", "mentions:Q"],
            )
            .properties(height=260)
        )
    return bars, line, daily


def main():
    st.set_page_config(page_title="MarketPulse desk", layout="wide")
    if "data_mode" not in st.session_state:
        st.session_state.data_mode = "Synthetic demo" if _demo_requested() else "Saved folder"
    if "artifact_root" not in st.session_state:
        st.session_state.artifact_root = str(_root_argument() or PROJECT_ROOT)

    st.sidebar.header("Desk")
    st.sidebar.radio(
        "Data",
        ["Synthetic demo", "Saved folder"],
        key="data_mode",
        help="The demo is synthetic and offline. A saved folder is read from disk and is not refreshed.",
    )
    if st.session_state.data_mode == "Saved folder":
        st.sidebar.text_input(
            "Artifact folder",
            key="artifact_root",
            help="A directory that contains output/ and, if you exported Polymarket, data/polymarket/.",
        )
    if st.sidebar.button("Reload from disk"):
        st.rerun()
    workspace = _load()
    tickers = sorted({row["ticker"] for row in workspace.items} | {row["ticker"] for row in workspace.observations})
    mode_key = st.session_state.data_mode
    chosen_tickers = st.sidebar.multiselect("Tickers", tickers, default=tickers, key=f"tickers-{mode_key}")
    chosen_sources = st.sidebar.multiselect(
        "Mention sources",
        ["yahoo", "reddit", "x"],
        default=["yahoo", "reddit", "x"],
        key=f"sources-{mode_key}",
        help="Filters news and social mentions only. Polymarket is not a news source.",
    )
    span_dates = []
    for row in workspace.items:
        try:
            span_dates.append(row["timestamp"])
        except KeyError:
            pass
    for row in workspace.observations:
        span_dates.append(row["observed_at"])
    if span_dates:
        days = []
        for value in span_dates:
            try:
                from marketpulse.dashboard.data import local_date
                days.append(local_date(value))
            except (TypeError, ValueError, OverflowError, OSError):
                continue
        start_default, end_default = (min(days), max(days)) if days else (date(2026, 11, 7), date(2026, 11, 9))
    else:
        start_default, end_default = date(2026, 11, 7), date(2026, 11, 9)
    picked = st.sidebar.date_input(
        f"Dates ({ZONE_LABEL})",
        value=(start_default, end_default),
        key=f"dates-{mode_key}",
        help="Inclusive local dates. Mentions use publication time. Polymarket uses observed_at.",
    )
    if isinstance(picked, tuple) and len(picked) == 2:
        start, end = picked
    else:
        start = end = picked if not isinstance(picked, tuple) else start_default

    ticker_set = set(chosen_tickers)
    source_set = set(chosen_sources)
    mentions = filter_items(workspace.items, ticker_set, source_set, start, end)
    observations = filter_observations(workspace.observations, ticker_set, start, end)
    windows = saved_windows(workspace.summary, ticker_set)

    if workspace.synthetic:
        st.warning(
            f"**Synthetic demo.** {workspace.note} "
            "Dates in the files run from 7 Nov 2026 through 9 Nov 2026 UTC. "
            "Nothing on this screen is a live quote, a live feed, or an archived Polymarket print."
        )
    else:
        generated = None if not workspace.summary else workspace.summary.get("generated_at")
        stamp = format_local(generated) if generated else "no generated_at in summary.json"
        st.info(f"**Saved files, not a live refresh.** {workspace.note} Radar timestamp: {stamp}.")

    st.title("MarketPulse")
    st.caption(
        "Conversation evidence beside explicitly selected Polymarket outcome prices. "
        "Scores describe wording. Prices are share-price proxies in [0, 1]. Neither is a forecast or a recommendation."
    )
    for issue in workspace.issues:
        st.error(f"{Path(issue.path).name}: {issue.message}")
    if workspace.duplicate_mentions:
        st.caption(f"Removed {workspace.duplicate_mentions} duplicate mention row(s) from the loaded file.")
    if workspace.duplicate_observations:
        st.caption(f"Removed {workspace.duplicate_observations} duplicate Polymarket observation(s).")

    score = mean_score(mentions)
    mix = {row["label"]: row["mentions"] for row in label_mix(mentions)}
    attention = [
        row for row in workspace.source_status
        if row.get("ticker") in ticker_set and row.get("status") not in {None, "ok"}
    ]
    columns = st.columns(4)
    columns[0].metric("Mentions in filter", len(mentions), help="Retained sampled rows after filters. Not total platform volume.")
    columns[1].metric(
        "Mean item score",
        "n/a" if score is None else f"{score:+.3f}",
        help="Mean of filtered item scores in [-1, 1]. n/a means no rows, not a neutral zero.",
    )
    columns[2].metric("Bullish / bearish / neutral", f"{mix['bullish']} / {mix['bearish']} / {mix['neutral']}")
    columns[3].metric("Source rows needing attention", len(attention), help="Non-ok rows in source_status for the selected tickers.")

    st.subheader("Sentiment of retained mentions")
    st.caption(
        "The charts use publication timestamps and the filters above. "
        "They are not the radar's recent-versus-earlier window aggregates, and the mention counts are a bounded sample."
    )
    if not mentions:
        st.info("No mentions in this filter. The empty result is not stored as a neutral score.")
    else:
        bars, line, daily = _sentiment_charts(mentions)
        left, right = st.columns(2)
        with left:
            st.markdown("**Label mix**")
            st.altair_chart(bars, use_container_width=True)
        with right:
            st.markdown("**Mean score by Cordoba publication date**")
            if line is None:
                st.info("Fewer than two local dates in this filter, so no sentiment line is drawn.")
            else:
                st.altair_chart(line, use_container_width=True)
            if daily:
                st.caption("Each point is the mean of item scores published that local date, with the day's mention count in the tooltip.")

    st.subheader("Saved run windows")
    st.caption(
        "These figures come from summary.json for the selected tickers. "
        "Source and date filters do not recompute them, and saved snapshot volumes are not added together."
    )
    if not windows:
        st.info("No summary.json window block for the selected tickers.")
    else:
        st.dataframe(windows, use_container_width=True, hide_index=True)
    if workspace.runs:
        reported = sum(run["reported_volume"] or 0 for run in workspace.runs)
        st.caption(
            f"{len(workspace.runs)} snapshot file(s) are listed below. "
            f"Their reported volumes sum to {reported} only if someone adds them; this desk does not. "
            "Overlapping runs would double-count mentions."
        )
        st.dataframe(
            [{
                "file": run["path"],
                "generated": format_local(run["generated_at"]),
                "reported_volume_in_file": run["reported_volume"],
                "origin": run.get("data_origin") or "unspecified",
            } for run in workspace.runs],
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Polymarket outcome prices")
    st.caption(
        "Share prices stay on their own 0–1 scale. They are not plotted against sentiment, "
        "not converted into an edge, and not treated as news sources. "
        "The mention-source filter does not apply here."
    )
    links = []
    if isinstance(workspace.polymarket, dict) and isinstance(workspace.polymarket.get("links"), list):
        links = [row for row in workspace.polymarket["links"] if isinstance(row, dict)]
        if workspace.polymarket.get("data_origin") == "synthetic_demo" or workspace.polymarket.get("fixture_example"):
            st.warning(workspace.polymarket.get("price_meaning") or "Polymarket block is marked synthetic.")
    by_key = {}
    for link in links:
        if link.get("ticker") not in ticker_set:
            continue
        by_key[(link.get("ticker"), None if link.get("market_id") is None else str(link.get("market_id")))] = link
    series = [row for row in group_observations(observations) if row["ticker"] in ticker_set]
    if not series and not by_key:
        st.info("No Polymarket observations in this filter. Missing history is left blank rather than drawn as zero.")
    shown = set()
    for row in series:
        key = (row["ticker"], row["market_id"])
        shown.add(key)
        link = by_key.get(key, {})
        _render_market(row, link)
    for key, link in by_key.items():
        if key in shown:
            continue
        _render_market({"ticker": key[0], "market_id": key[1], "outcome": link.get("outcome"),
                        "points": [], "provenances": [], "synthetic": workspace.synthetic, "enough_history": False}, link)

    st.subheader("Evidence")
    st.caption("Headlines and posts are untrusted text. Only http and https links are clickable.")
    if not mentions:
        st.info("No evidence rows in this filter.")
    else:
        st.caption(f"Showing {min(len(mentions), 60)} of {len(mentions)} filtered mentions.")
        for item in mentions[:60]:
            with st.container(border=True):
                st.markdown(
                    f"**{_escape(item['ticker'])} · {_escape(item['source'])} · {_escape(item['label'])} "
                    f"({item['score']:+.3f})**"
                )
                st.caption(format_local(item["timestamp"]))
                st.text(item["text"][:1200])
                href = safe_href(item["url"])
                if href:
                    st.link_button("Open original source", href)
                else:
                    st.caption("No http(s) source link on this row.")
                if item["flags"]:
                    st.caption("Flags: " + ", ".join(str(flag) for flag in item["flags"]))

    with st.expander("Source status for the selected tickers"):
        rows = [
            row for row in workspace.source_status
            if row.get("ticker") in ticker_set
        ]
        if not rows:
            st.info("No source_status.json rows.")
        for row in rows:
            st.markdown(f"**{_escape(row.get('ticker'))} / {_escape(row.get('source'))} — {_escape(row.get('status'))}**")
            for message in list(row.get("errors") or []) + list(row.get("warnings") or []):
                st.text(str(message))


def _render_market(series, link):
    title = link.get("question") or series.get("market_id") or series["ticker"]
    synthetic = series.get("synthetic") or "synthetic" in str(link.get("price_basis") or "")
    with st.container(border=True):
        st.markdown(f"**{_escape(series['ticker'])} — {_escape(title)}**")
        if synthetic:
            st.caption("Synthetic Polymarket series. Not an archived or live provider print.")
        outcome = link.get("outcome") or series.get("outcome") or "n/a"
        st.markdown(f"Outcome `{_escape(outcome)}` · basis `{_escape(link.get('price_basis') or 'see observations')}`")
        if link.get("interpretation"):
            st.markdown("Interpretation: " + _escape(link["interpretation"]))
        meta = []
        if link.get("liquidity") is None:
            meta.append("liquidity: missing")
        else:
            meta.append(f"liquidity: {link.get('liquidity')} ({link.get('liquidity_unit') or 'provider units'})")
        if link.get("volume_24h") is None:
            meta.append("trailing 24h volume: missing")
        else:
            meta.append(f"trailing 24h volume: {link.get('volume_24h')}")
        meta.append(f"closed: {link.get('closed')}")
        st.caption(" · ".join(str(part) for part in meta))
        href = safe_href(link.get("source_url"))
        if href:
            st.link_button("Open market source", href)
        if link.get("rules_text"):
            with st.expander("Resolution rules text"):
                st.text(str(link["rules_text"])[:4000])
        if link.get("resolution_source"):
            st.caption("Rules source: " + _escape(link["resolution_source"]))
        reasons = link.get("suppressed_reasons") or []
        flag = link.get("divergence_flag") if isinstance(link.get("divergence_flag"), dict) else None
        if flag:
            st.warning(str(flag.get("message") or "Divergence flag present."))
        elif reasons:
            st.caption("Divergence suppressed: " + ", ".join(str(reason) for reason in reasons))
        for message in list(link.get("warnings") or []) + list(link.get("errors") or []):
            st.text(str(message))
        limitations = link.get("limitations") or []
        if limitations:
            st.caption("Limitations: " + ", ".join(str(item) for item in limitations))
        points = series.get("points") or []
        if series.get("enough_history"):
            st.altair_chart(_price_chart(points), use_container_width=True)
            st.caption(
                "Provenance: " + ", ".join(series.get("provenances") or ["unspecified"])
                + ". A line connects observed points only."
            )
        else:
            st.info(
                f"Insufficient history ({len(points)} point). "
                "No trend line is drawn, and no earlier price is invented."
            )


main()
