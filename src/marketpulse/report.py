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
    if run.get("polymarket"):
        lines += polymarket_section(run["polymarket"])
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
    if run.get("polymarket"):
        lines += [
            "- Polymarket outcome prices are share-price proxies in [0, 1], not calibrated probabilities. "
            "A context-divergence flag fires only for an explicit user mapping with aligned observations, "
            "adequate recent mentions, fresh quotes, and reported liquidity. It is not causality, an edge, or a forecast.",
            "",
        ]
    return "\n".join(lines)


def polymarket_section(payload):
    lines = ["", "## Polymarket context (opt-in)", "",
             escape(payload.get("price_meaning") or ""),
             escape(payload.get("divergence_meaning") or ""),
             ""]
    if payload.get("offline_fixture"):
        lines.append("This section was rendered from an **offline fixture**, not a live Polymarket capture.")
        lines.append("")
    lines += ["| Ticker | Question | Outcome | Price | Window change | 24h volume | Liquidity | Limitations |",
              "|---|---|---|---:|---:|---:|---:|---|"]
    for link in payload.get("links") or []:
        window = link.get("window") or {}
        price = "n/a" if link.get("price") is None else f"{link['price']:.3f}"
        delta = "n/a" if window.get("delta") is None else f"{window['delta']:+.3f}"
        volume = "n/a" if link.get("volume_24h") is None else f"{link['volume_24h']:.0f}"
        liquidity = "n/a" if link.get("liquidity") is None else f"{link['liquidity']:.0f}"
        limits = ", ".join(link.get("limitations") or []) or "none"
        lines.append(
            f"| {escape(link.get('ticker'))} | {escape(link.get('question') or 'n/a')} | "
            f"{escape(link.get('outcome') or 'n/a')} | {price} | {delta} | {volume} | {liquidity} | {escape(limits)} |"
        )
    lines.append("")
    for link in payload.get("links") or []:
        lines.append(f"### {escape(link.get('ticker'))} — {escape(link.get('question') or 'market unavailable')}")
        lines.append(f"- Outcome `{escape(link.get('outcome') or 'n/a')}` · price basis: {escape(link.get('price_basis') or 'n/a')}")
        lines.append(f"- User interpretation: {escape(link.get('interpretation') or 'n/a')}")
        lines.append(f"- Mapping relation: `{escape(link.get('relation') or 'n/a')}` (this is the user's comparison choice, not an inferred equity beta)")
        if link.get("source_url"):
            lines.append(f"- Source URL: {link['source_url']}")
        if link.get("api_url"):
            lines.append(f"- API URL: {link['api_url']}")
        lines.append(
            f"- Status: closed={link.get('closed')}, resolution={escape(link.get('resolution_status') or 'n/a')}, "
            f"rules source: {escape(link.get('resolution_source') or 'n/a')}"
        )
        rules = link.get("rules_text") or ""
        if rules:
            excerpt = rules if len(rules) <= 400 else rules[:400] + "…"
            lines.append(f"- Rules excerpt: {escape(excerpt)}")
        window = link.get("window") or {}
        if window:
            lines.append(
                f"- Earlier price: {window.get('earlier_price')} at {escape(window.get('earlier_observed_at') or 'n/a')} "
                f"({escape(window.get('earlier_provenance') or 'none')}). {escape(window.get('note') or '')}"
            )
        history = link.get("history") or {}
        if history:
            lines.append(
                f"- History read: {escape(history.get('provenance') or 'n/a')}; "
                f"points kept {history.get('points_kept')}; sampling: {escape(history.get('sampling') or 'n/a')}"
            )
        flag = link.get("divergence_flag")
        if flag:
            lines.append(f"- **Divergence (heuristic):** {escape(flag.get('message'))}")
        else:
            reasons = ", ".join(link.get("suppressed_reasons") or []) or "not evaluated"
            lines.append(f"- Divergence suppressed: {escape(reasons)}")
        for message in (link.get("errors") or []) + (link.get("warnings") or []):
            lines.append(f"  - {escape(message)}")
        lines.append("")
    for warning in payload.get("warnings") or []:
        lines.append(f"- Run warning: {escape(warning)}")
    lines.append(
        "Volume is Gamma `volume24hr` (trailing 24h, provider units). Liquidity is Gamma `liquidity` "
        f"(missing stays null; divergence wants at least {1000:g} reported units). "
        "Neither figure is converted to a currency."
    )
    return lines
