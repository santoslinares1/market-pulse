"""Small auditable English finance lexicon; scores measure language, not returns."""
import math
import re
import unicodedata
from .normalize import ALIASES

# Phrase-level rules precede single words to avoid double-counting 'buy the dip'.
LEXICON = {
    "beats estimates": 2, "beat estimates": 2, "earnings beat": 2, "record revenue": 2,
    "raises guidance": 2, "raised guidance": 2, "guidance raised": 2,
    "buy the dip": 1.5, "buy zone": 1.5, "strong buy": 2, "go up": 1,
    "giga green": 1.5, "margin expansion": 1.5, "dividend increase": 1.5,
    "bullish": 2, "outperform": 1.5, "upgrade": 1.5, "upgrades": 1.5,
    "upgraded": 1.5, "buyback": 1, "repurchase": 1, "rally": 1.5, "surge": 1.5,
    "surges": 1.5, "growth": .75, "profit": .75, "profits": .75,
    "buy": 1, "long": .75, "upside": 1.5, "breakout": 1.5,
    "misses estimates": -2, "missed estimates": -2, "earnings miss": -2,
    "cuts guidance": -2, "cut guidance": -2, "guidance cut": -2,
    "straight to zero": -2, "easy short": -2, "go down": -1,
    "bearish": -2, "underperform": -1.5, "downgrade": -1.5,
    "downgrades": -1.5, "downgraded": -1.5, "sell": -1, "short": -.75,
    "downside": -1.5, "plunge": -1.5, "plunges": -1.5, "crash": -2,
    "bankruptcy": -2, "default": -1.5, "fraud": -2, "lawsuit": -1,
    "investigation": -1, "board probe": -1, "loss": -.75, "losses": -.75,
    "uncertainty": -.75, "panic": -1, "decline": -1, "declines": -1,
    "dilution": -1, "layoffs": -.5,
}
NEGATIONS = {"not", "no", "never", "without", "neither", "dont", "isnt", "wont", "cant", "cannot"}
CONTRAST = {"but", "however", "yet", "although"}
PATTERN = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in sorted(LEXICON, key=len, reverse=True)) + r")(?!\w)")
PROMO = re.compile(r"whatsapp|follow (?:him|this blogger)|made \$?80,000|monthly income|retired from goldman|one membership", re.I)


def context_for(text, ticker):
    """Restrict multi-cashtag lists to ticker lines; broad prose remains ambiguous."""
    lines = text.splitlines()
    tags = set(re.findall(r"\$([A-Za-z][A-Za-z0-9.]*)", text))
    if len(tags) > 1 and len(lines) > 1:
        own = [line for line in lines if re.search(r"\$" + re.escape(ticker) + r"\b", line, re.I)]
        if own and all(len(re.findall(r"\$([A-Za-z][A-Za-z0-9.]*)", line)) == 1 for line in own):
            # Include an immediately preceding LONG/SHORT section header, not other tickers.
            selected = []
            for i, line in enumerate(lines):
                if line in own:
                    header = next((s for s in reversed(lines[:i]) if s.strip()), "")
                    if re.match(r"^[^\w$]*(?:long|short)\b", header, re.I):
                        selected.append(header)
                    selected.append(line)
            return "\n".join(selected), ["ticker_line_context"]
    return text, (["multi_ticker_ambiguous"] if len(tags) > 1 else [])


def classify(text, ticker=""):
    context, flags = context_for(text, ticker)
    if PROMO.search(text):
        return "neutral", 0.0, [], flags + ["promotional_noise_suppressed"]
    text = unicodedata.normalize("NFKC", context).lower().replace("’", "'")
    text = re.sub(r"https?://\S+|@\w+|\$[a-z][a-z0-9.]*", " ", text)
    text = re.sub(r"\b(don't|doesn't|didn't)\b", "dont", text)
    text = re.sub(r"\b(isn't|wasn't|aren't)\b", "isnt", text)
    text = text.replace("won't", "wont").replace("can't", "cant")
    # A ticker or company name is never itself a sentiment signal (e.g. LONG).
    names = [ticker.lower()] + list(ALIASES.get(ticker, ())) if ticker else []
    for name in names:
        text = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", " ", text)
    signals, total = [], 0.0
    for match in PATTERN.finditer(text):
        prefix = re.split(r"[.!?;,:\n]", text[:match.start()])[-1]
        tokens = re.findall(r"\b[a-z]+\b", prefix)[-3:]
        if any(word in CONTRAST for word in tokens):
            cut = max(i for i, word in enumerate(tokens) if word in CONTRAST)
            tokens = tokens[cut + 1:]
        negated = sum(word in NEGATIONS for word in tokens) % 2 == 1
        weight = LEXICON[match.group()] * (-1 if negated else 1)
        total += weight
        signals.append({"signal": match.group(), "weight": weight, "negated": negated})
    # Bounded, monotonically increasing score; neutral dead zone prevents weak claims.
    score = round(math.tanh(total / 3), 4)
    label = "bullish" if score >= .2 else "bearish" if score <= -.2 else "neutral"
    return label, score, signals, flags


def classify_item(item):
    item.label, item.score, item.signals, extra_flags = classify(item.text, item.ticker)
    item.flags = list(dict.fromkeys(item.flags + extra_flags))
    return item
