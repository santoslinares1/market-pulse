"""Comparable-window summaries, transparent shift rules and cautious alerts."""
from collections import Counter
from datetime import timedelta
from .normalize import parse_time

MIN_MENTIONS = 3
DELTA_THRESHOLD = .35
DIRECTION_THRESHOLD = .15
PRICE_FLAT_PCT = .5
VOLUME_RATIO = 3.0
MIN_VOLUME_RECENT = 6


def summarize(items):
    counts = Counter(item.label for item in items)
    by_source = Counter(item.source for item in items)
    total = sum(item.score for item in items)
    return {"volume": len(items), "by_source": dict(sorted(by_source.items())),
            "bullish": counts["bullish"], "bearish": counts["bearish"], "neutral": counts["neutral"],
            "score_sum": round(total, 4), "net_score": round(total / len(items), 4) if items else None,
            "sources": {source: source_summary([i for i in items if i.source == source]) for source in by_source}}


def source_summary(items):
    return {"volume": len(items), "score_sum": round(sum(i.score for i in items), 4)}


def combined(stats, sources):
    n = sum(stats[s]["volume"] for s in sources)
    total = sum(stats[s]["score_sum"] for s in sources)
    return n, total / n if n else None


def direction(score):
    if score is None:
        return "unavailable"
    return "bullish" if score >= DIRECTION_THRESHOLD else "bearish" if score <= -DIRECTION_THRESHOLD else "neutral"


def analyze(ticker, items, now, hours, recent_hours, price, statuses, history=None):
    own = [i for i in items if i.ticker == ticker]
    cutoff = now - timedelta(hours=recent_hours)
    recent = summarize([i for i in own if parse_time(i.timestamp) > cutoff])
    earlier = summarize([i for i in own if parse_time(i.timestamp) <= cutoff])
    total = summarize(own)
    successful = {s["source"] for s in statuses if s["ticker"] == ticker and s["status"] == "ok" and s["source"] != "yahoo_price"}
    common = set(recent["sources"]) & set(earlier["sources"]) & successful
    baseline, baseline_hours, baseline_kind = earlier["sources"], hours - recent_hours, "earlier lookback"
    nr, sr = combined(recent["sources"], common)
    nb, sb = combined(baseline, common)
    # Fall back to historical *non-overlapping* recent windows only if current baseline is too thin.
    if (nr < MIN_MENTIONS or nb < MIN_MENTIONS) and history:
        previous = history.get("tickers", {}).get(ticker, {})
        if history.get("hours") == hours and previous.get("successful_sources") == sorted(successful):
            old_stats = previous.get("recent", {}).get("sources", {})
            candidates = set(recent["sources"]) & set(old_stats) & successful
            new_nr, new_sr = combined(recent["sources"], candidates)
            new_nb, new_sb = combined(old_stats, candidates)
            if new_nr >= MIN_MENTIONS and new_nb >= MIN_MENTIONS:
                common, nr, sr, nb, sb = candidates, new_nr, new_sr, new_nb, new_sb
                baseline, baseline_hours, baseline_kind = old_stats, recent_hours, "non-overlapping historical snapshot"
    adequate = nr >= MIN_MENTIONS and nb >= MIN_MENTIONS
    delta = round(sr - sb, 4) if adequate else None
    flip = adequate and direction(sr) in {"bullish", "bearish"} and direction(sb) in {"bullish", "bearish"} and direction(sr) != direction(sb)
    changed = adequate and direction(sr) in {"bullish", "bearish"} and (flip or abs(delta) >= DELTA_THRESHOLD)
    alerts = []
    shift_direction = "bullish" if delta is not None and delta > 0 else "bearish"
    if changed:
        alerts.append({"ticker": ticker, "kind": "sentiment_shift", "direction": shift_direction,
                       "delta": delta, "message": f"Observed conversation shifted {shift_direction} (recent tone: {direction(sr)}); "
                       f"{'direction flip' if flip else 'score delta threshold'} on comparable sources. Not a price forecast."})
        pct = price.get("percent_change")
        if price.get("usable_for_divergence") and pct is not None:
            unconfirmed = abs(pct) <= PRICE_FLAT_PCT or (delta > 0 and pct < 0) or (delta < 0 and pct > 0)
            if unconfirmed:
                alerts.append({"ticker": ticker, "kind": "price_unconfirmed_shift", "direction": shift_direction,
                               "price_pct": pct, "message": f"Sentiment shift is not confirmed by the aligned observed price change ({pct:+.2f}%). "
                               "This does not establish predictive lead time."})
    ratio = (nr / recent_hours) / (nb / baseline_hours) if adequate and nb else None
    if ratio is not None and ratio >= VOLUME_RATIO and nr >= MIN_VOLUME_RECENT:
        alerts.append({"ticker": ticker, "kind": "unusual_observed_volume", "ratio": round(ratio, 3),
                       "message": f"Observed mention rate is {ratio:.1f}x baseline on comparable sources; "
                       "bounded samples, not total market conversation volume."})
    comparison = {"baseline": baseline_kind, "sources": sorted(common), "recent_count": nr, "baseline_count": nb,
                  "recent_score": round(sr, 4) if sr is not None else None,
                  "baseline_score": round(sb, 4) if sb is not None else None,
                  "delta": delta, "direction_flip": bool(flip),
                  "volume_ratio": round(ratio, 3) if ratio is not None else None,
                  "adequate": adequate,
                  "note": "Comparable observed sources only; samples can be truncated" if adequate else
                  "Insufficient comparable baseline (need >=3 mentions in each window); no shift/volume inference"}
    return {"ticker": ticker, "overall": total, "tone": direction(total["net_score"]), "recent": recent,
            "earlier": earlier, "successful_sources": sorted(successful), "comparison": comparison,
            "price": price, "alerts": alerts}
