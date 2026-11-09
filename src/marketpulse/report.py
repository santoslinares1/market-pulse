"""Readable Markdown brief with every source failure visible and links to evidence."""
from datetime import timezone, timedelta
from .normalize import parse_time, clean

# Argentina uses UTC-3 without DST for the modern dates this radar handles.
ZONE = timezone(timedelta(hours=-3), "America/Cordoba")


def local_time(value):
    return parse_time(value).astimezone(ZONE).strftime("%Y-%m-%d %H:%M Argentina (America/Cordoba, UTC-3)")


def escape(value):
    text = clean(value).replace("\\", "\\\\")
    for char in ("*", "_", "[", "]", "`", "|", "<", ">"):
        text = text.replace(char, "\\" + char)
    return text


def fmt(value):
    return "n/a" if value is None else f"{value:+.3f}"


def render(run, items):
    lines = ["# MarketPulse — Sentiment radar", "", f"Generated: {local_time(run['generated_at'])}",
             f"Lookback: {run['hours']:g}h · recent window: {run['recent_hours']:g}h", "",
             "**This flags conversation shifts; it does not forecast prices.** Scores describe language, not expected returns. "
             "Social posts are unverified claims, including possible spam or rumors; quotations are not endorsements.", "",
             "## Watchlist overview", "", "| Ticker | Mentions | Yahoo / Reddit / X | Bullish | Bearish | Neutral | Net score | Tone |",
             "|---|---:|---|---:|---:|---:|---:|---|"]
    for ticker, data in run["tickers"].items():
        s = data["overall"]
        counts = " / ".join(str(s["by_source"].get(k, 0)) for k in ("yahoo", "reddit", "x"))
        lines.append(f"| {ticker} | {s['volume']} | {counts} | {s['bullish']} | {s['bearish']} | {s['neutral']} | {fmt(s['net_score'])} | {data['tone']} |")
    lines += ["", "Net score = arithmetic mean of bounded per-item scores [-1, +1]; n/a means no evidence, not neutral sentiment.",
              "Mention counts include neutralized promotional posts. Cross-ticker posts can count once for each relevant ticker.",
              "", "## Alerts", ""]
    alerts = [a for data in run["tickers"].values() for a in data["alerts"]]
    lines += [f"- **{a['ticker']} — {a['kind']}**: {escape(a['message'])}" for a in alerts] or ["No alerts fired. A missing or thin baseline is not evidence of normal market conditions."]
    lines += ["", "## Window comparisons and observed prices", ""]
    for ticker, data in run["tickers"].items():
        c, price = data["comparison"], data["price"]
        lines += [f"### {ticker}", f"- Recent / earlier mentions: {data['recent']['volume']} / {data['earlier']['volume']}",
                  f"- Comparable sources: {', '.join(c['sources']) or 'none'}; baseline: {c['baseline']}; delta: {fmt(c['delta'])}",
                  f"- {c['note']}"]
        if price.get("reference_pct") is not None:
            lines.append(f"- Yahoo reference change: {price['reference_pct']:+.2f}% versus chart-range previous close (not necessarily last session); quote: {local_time(price['quote_time'])}")
        lines.append(f"- Window-aligned price change: {('n/a' if price.get('percent_change') is None else str(price['percent_change']) + '%')}. {price['note']}")
        if price.get("usable_for_divergence"):
            lines.append(f"- Price interval: {local_time(price['window_start'])} → {local_time(price['window_end'])}")
    lines += ["", "## Evidence sample", "", "Exact text excerpts below may be truncated; full retained text and matched signals are in items.json. "
              "Examples favor larger absolute sentiment scores; they are not a representative random sample.", ""]
    for ticker in run["tickers"]:
        lines.append(f"### {ticker}")
        own = sorted((i for i in items if i.ticker == ticker), key=lambda i: abs(i.score), reverse=True)[:3]
        if not own:
            lines.append("No relevant, dated items available in this window.")
        for item in own:
            excerpt = clean(item.text)
            if len(excerpt) > 500:
                excerpt = excerpt[:500] + "…"
            lines += [f"- **{item.label} ({item.score:+.4f}) · {item.source}** · {local_time(item.timestamp)}",
                      f"  > {escape(excerpt)}", f"  [Source]({item.url.replace(')', '%29').replace('(', '%28')})",
                      f"  Signals: {escape(', '.join(s['signal'] + (' [negated]' if s['negated'] else '') for s in item.signals)) or 'none'}; "
                      f"flags: {escape(', '.join(item.flags)) or 'none'}"]
        lines.append("")
    lines += ["## Source status", "", "Source failures do not become zero-sentiment observations. All feeds/searches are bounded samples.", ""]
    for status in run["source_status"]:
        lines.append(f"- **{status['ticker']} / {status['source']} — {status['status']}**: {status['accepted']} accepted items; {status['fetched']} retrieved rows")
        for message in status["errors"] + status["warnings"]:
            lines.append(f"  - {escape(message)}")
    for warning in run.get("warnings", []):
        lines.append(f"- Run warning: {escape(warning)}")
    lines += ["", "## Method and guardrails", "",
              "- English finance lexicon; phrase matching, three-token negation scope, neutral dead zone, ticker-line context and promotional-noise suppression.",
              "- Shift: >=3 comparable mentions in each window, mean-score delta >=0.35 or bullish/bearish direction flip (direction boundary ±0.15). "
              "Sources must occur in both windows and have successful status.",
              "- Price-unconfirmed alert additionally requires fresh, approximately window-aligned Yahoo bars and an absolute move <=0.5% or opposite direction. "
              "Missing/stale/weekend prices suppress this alert; there is no claim of forecasting or proven price lead.",
              "- Unusual observed volume: >=6 recent and >=3 baseline mentions, >=3x hourly mention rate on comparable sources. "
              "Search caps and changing coverage can create artifacts; inspect evidence before interpreting an alert.",
              "- First run compares recent vs earlier lookback. Later runs can fall back to a non-overlapping snapshot from the preceding seven days "
              "with matching settings and available sources. Sparse evidence stays insufficient.",
              "- No trading orders, notifications, model accuracy claims or backtested predictive performance. Not investment advice.", ""]
    return "\n".join(lines)
