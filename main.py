import os, json, hashlib, requests, numpy as np, pandas as pd
from datetime import datetime, timezone

V = "7.0"
BASE = "https://api.upbit.com/v1"
STATE = "tracked_coins.json"
TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
MIN_VALUE = 1_000_000_000
MAX_SCAN = 120
MAX_POSITIONS = 3
MIN_SCORE = 75
WEAK_SCORE = 82
CRASH_SCORE = 88
COOLDOWN = 4
MIN_VOL = 130
STRONG_VOL = 180
MAX_EMA = 5.0
MIN_RSI = 35
MAX_RSI = 78
MIN_SL = 1.0
MAX_SL = 6.0
S = requests.Session()
NAMES = {}

def now(): return datetime.now(timezone.utc)

def load(p, d):
    try:
        with open(p, encoding="utf-8") as f: return json.load(f)
    except: return d

def save(p, d):
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(p + ".tmp", p)

def num(x, d=0):
    try: return float(x)
    except: return d

def fp(x):
    x = float(x)
    if x >= 1000: return f"{x:,.0f}"
    if x >= 1: return f"{x:,.3f}"
    if x >= .01: return f"{x:,.4f}"
    return f"{x:,.8f}"

def api(path, params=None):
    try:
        r = S.get(BASE + path, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("API:", e)
        return None

def init_markets():
    global NAMES
    for x in api("/market/all", {"isDetails": "true"}) or []:
        m = x["market"]
        if m.startswith("KRW-"):
            NAMES[m] = (x.get("korean_name") or m[4:], m[4:])

def name(m):
    x = NAMES.get(m)
    return f"{x[0]} ({x[1]})" if x else m[4:]

def candles(m, u, n=210):
    path = "/candles/days" if u == 1440 else f"/candles/minutes/{u}"
    d = api(path, {"market": m, "count": n})
    if not isinstance(d, list) or len(d) < 60: return None
    df = pd.DataFrame(d).rename(columns={
        "opening_price": "open", "high_price": "high", "low_price": "low",
        "trade_price": "close", "candle_acc_trade_volume": "volume"})
    c = ["open", "high", "low", "close", "volume"]
    df[c] = df[c].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=c).iloc[::-1].reset_index(drop=True)
    if len(df) <= 60: return None
    return df.iloc[:-1].reset_index(drop=True)

def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0)
    l = -d.clip(upper=0)
    ag = g.ewm(alpha=1/n, adjust=False).mean()
    al = l.ewm(alpha=1/n, adjust=False).mean()
    return (100 - 100 / (1 + ag / al.replace(0, np.nan))).fillna(50)

def atr(df, n=14):
    pc = df.close.shift()
    tr = pd.concat([
        df.high - df.low,
        (df.high - pc).abs(),
        (df.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def adx(df, n=14):
    u = df.high.diff()
    d = -df.low.diff()
    plus = pd.Series(np.where((u > d) & (u > 0), u, 0), index=df.index)
    minus = pd.Series(np.where((d > u) & (d > 0), d, 0), index=df.index)
    pc = df.close.shift()
    tr = pd.concat([
        df.high - df.low,
        (df.high - pc).abs(),
        (df.low - pc).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1/n, adjust=False).mean()
    p = 100 * plus.ewm(alpha=1/n, adjust=False).mean() / a.replace(0, np.nan)
    q = 100 * minus.ewm(alpha=1/n, adjust=False).mean() / a.replace(0, np.nan)
    return (100 * (p - q).abs() / (p + q).replace(0, np.nan)).ewm(alpha=1/n, adjust=False).mean().fillna(0)

def tf(df):
    if df is None: return None
    e20 = ema(df.close, 20)
    e50 = ema(df.close, 50)
    rr = rsi(df.close)
    return {
        "p": float(df.close.iloc[-1]),
        "e20": float(e20.iloc[-1]),
        "e50": float(e50.iloc[-1]),
        "rsi": float(rr.iloc[-1]),
        "adx": float(adx(df).iloc[-1])
    }

def btc_regime():
    d = candles("KRW-BTC", 15, 100)
    h = candles("KRW-BTC", 60, 100)
    if d is None or h is None: return "NEUTRAL", 0, 0
    c15 = (d.close.iloc[-1] / d.close.iloc[-2] - 1) * 100
    c1 = (h.close.iloc[-1] / h.close.iloc[-2] - 1) * 100
    if c15 <= -1.5 or c1 <= -2: return "CRASH", c15, c1
    if c15 < -.5 or c1 < -.8: return "WEAK", c15, c1
    if c15 > .5 and c1 > .8: return "BULL", c15, c1
    return "NEUTRAL", c15, c1

def analyze(m, regime):
    ds = [candles(m, u) for u in (15, 60, 240, 1440)]
    d15, d1, d4, dd = ds
    a15, a1, a4, ad = [tf(x) for x in ds]
    if any(x is None for x in (a15, a1, a4, ad)): return None

    p = a15["p"]
    e = a15["e20"]
    dist = (p / e - 1) * 100

    if dist > MAX_EMA or a15["rsi"] < MIN_RSI or a15["rsi"] > MAX_RSI: return None

    trend = sum(
        x["p"] >= x["e20"] and x["e20"] >= x["e50"]
        for x in (a1, a4, ad)
    )

    avg = float(d15.volume.iloc[-21:-1].mean())
    vol = float(d15.volume.iloc[-1] / avg * 100) if avg > 0 else 0
    body = abs(d15.close.iloc[-1] - d15.open.iloc[-1]) / d15.open.iloc[-1] * 100

    low = float(d15.low.iloc[-20:].min())
    near_low = p <= low * 1.035

    prev_high = float(d15.high.iloc[-21:-1].max())
    breakout = p > prev_high

    recovery = (
        p > d15.close.iloc[-2] and
        p >= e and
        p > d15.open.iloc[-1]
    )

    k = 100 * (d15.close - d15.low.rolling(5).min()) / (
        d15.high.rolling(5).max() - d15.low.rolling(5).min()
    ).replace(0, np.nan)

    st_gc = (
        k.iloc[-2] <= k.iloc[-3] and
        k.iloc[-1] > k.iloc[-2]
    )

    bounce = near_low and st_gc and recovery and vol >= MIN_VOL

    e20prev = float(ema(d15.close, 20).iloc[-2])
    pullback = (
        float(d15.low.iloc[-9:-1].min()) <= e20prev * 1.02
        and recovery
    )

    if not (breakout or pullback or bounce): return None

    minimum = STRONG_VOL if regime in ("WEAK", "CRASH") else MIN_VOL
    if vol < minimum and not bounce: return None
    if body > 5: return None

    score = 40
    score += trend * 8
    score += 10 if vol >= 180 else 5 if vol >= MIN_VOL else 0
    score += 10 if a15["rsi"] >= 45 else 0
    score += 8 if a15["adx"] >= 18 else 4 if a15["adx"] >= 14 else 0
    score += 10 if recovery else 0
    score += 10 if breakout else 0
    score += 12 if bounce else 0
    score += 5 if pullback else 0
    score = min(score, 100)

    threshold = (
        CRASH_SCORE if regime == "CRASH"
        else WEAK_SCORE if regime == "WEAK"
        else MIN_SCORE
    )

    if score < threshold: return None

    av = float(atr(d15).iloc[-1])
    swing = float(d15.low.iloc[-9:-1].min())
    stop = min(p - av * 1.2, swing - av * .2)
    risk = p - stop

    if risk < p * MIN_SL / 100:
        stop = p * (1 - MIN_SL / 100)
        risk = p - stop

    if risk > p * MAX_SL / 100: return None

    return {
        "market": m,
        "price": p,
        "stop": stop,
        "tp1": p + risk * 1.5,
        "tp2": p + risk * 2,
        "tp3": p + risk * 3,
        "score": score,
        "rsi": a15["rsi"],
        "adx": a15["adx"],
        "vol": vol,
        "dist": dist,
        "regime": regime,
        "type": "BOTTOM BOUNCE" if bounce else "BREAKOUT" if breakout else "PULLBACK",
        "trend": trend,
        "recovery": recovery,
        "bounce": bounce
    }

def tg(msg):
    if not TOKEN or not CHAT: return False
    try:
        return S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": CHAT,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=15
        ).ok
    except: return False

def reason(a):
    r = []
    if a["trend"] >= 3: r.append("1D·4H·1H 추세 정렬")
    elif a["trend"] == 2: r.append("상위 시간대 추세 우위")

    if a["vol"] >= 180: r.append(f"거래량 {a['vol']:.0f}% 급증")
    elif a["vol"] >= MIN_VOL: r.append(f"거래량 {a['vol']:.0f}% 증가")

    if a["bounce"]: r.append("저점권 + 스토캐스틱 반전")
    elif a["type"] == "BREAKOUT": r.append("최근 20봉 고점 돌파")
    else: r.append("EMA20 회복형 눌림")

    if a["recovery"]: r.append("상승 캔들·단기 회복 확인")
    if a["adx"] >= 18: r.append(f"ADX {a['adx']:.1f} 추세 강화")

    return " · ".join(r[:4])

def message(a):
    p = a["price"]
    s = a["stop"]
    sl = (s / p - 1) * 100
    r1 = (a["tp1"] / p - 1) * 100
    r2 = (a["tp2"] / p - 1) * 100
    r3 = (a["tp3"] / p - 1) * 100

    return f"""🚀 <b>UPBIT SPOT SIGNAL V7</b>
━━━━━━━━━━━━━━━━
💎 <b>{name(a['market'])}</b> <code>{a['market']}</code>
🎯 <b>조건점수 {a['score']}/100</b> | {a['type']}

💰 ENTRY <b>{fp(p)}</b>
🛡 SL <b>{fp(s)}</b> ({sl:.2f}%)

📈 <b>상승 근거</b>
• {reason(a)}
• RSI {a['rsi']:.1f} | ADX {a['adx']:.1f}
• EMA20 이격 {a['dist']:+.2f}%
• BTC Regime <b>{a['regime']}</b>

🎯 <b>TARGET</b>
TP1 {fp(a['tp1'])} (+{r1:.2f}%) · 1.5R
TP2 {fp(a['tp2'])} (+{r2:.2f}%) · 2.0R
TP3 {fp(a['tp3'])} (+{r3:.2f}%) · 3.0R

🔄 TP1 → SL ENTRY
🔄 TP2 → SL TP1
🏁 TP3 → 추적 종료

🔗 <a href="https://www.tradingview.com/symbols/UPBIT-{a['market'][4:]}KRW/">TradingView</a>"""

def track(state):
    if not state["positions"]: return

    qs = api(
        "/ticker",
        {"markets": ",".join(state["positions"].keys())}
    ) or []

    prices = {x["market"]: float(x["trade_price"]) for x in qs}

    for m, p in list(state["positions"].items()):
        price = prices.get(m)
        if price is None: continue

        p.setdefault("stage", 0)

        if price >= p["tp3"]:
            tg(
                f"🏆 <b>TP3 도달 / 추적 종료</b>\n"
                f"{name(m)}\n"
                f"현재가 {fp(price)}\n"
                f"수익률 +{(price/p['entry']-1)*100:.2f}%"
            )
            del state["positions"][m]

        elif p["stage"] < 2 and price >= p["tp2"]:
            p["stage"] = 2
            p["sl"] = p["tp1"]
            tg(
                f"🟢 <b>TP2 도달</b>\n"
                f"{name(m)}\n"
                f"현재가 {fp(price)}\n"
                f"SL → TP1 {fp(p['tp1'])}"
            )

        elif p["stage"] < 1 and price >= p["tp1"]:
            p["stage"] = 1
            p["sl"] = p["entry"]
            tg(
                f"🟢 <b>TP1 도달</b>\n"
                f"{name(m)}\n"
                f"현재가 {fp(price)}\n"
                f"SL → ENTRY {fp(p['entry'])}"
            )

        elif price <= p["sl"]:
            tg(
                f"🔴 <b>SL 도달 / 추적 종료</b>\n"
                f"{name(m)}\n"
                f"현재가 {fp(price)}\n"
                f"손익률 {(price/p['entry']-1)*100:.2f}%"
            )
            del state["positions"][m]

def main():
    print(f"UPBIT SPOT PROFIT TRACKING BOT V{V}")
    init_markets()

    state = load(
        STATE,
        {"version": V, "positions": {}, "sent": [], "last": {}}
    )

    state.setdefault("positions", {})
    state.setdefault("sent", [])
    state.setdefault("last", {})

    for p in state["positions"].values():
        p.setdefault("stage", 0)

    track(state)

    regime, c15, c1 = btc_regime()
    print(f"BTC {regime} | 15M {c15:+.2f}% | 1H {c1:+.2f}%")

    if len(state["positions"]) >= MAX_POSITIONS:
        state["version"] = V
        save(STATE, state)
        return

    qs = api(
        "/ticker",
        {"markets": ",".join(NAMES.keys())}
    ) or []

    qs = [
        x for x in qs
        if num(x.get("acc_trade_price_24h")) >= MIN_VALUE
    ]

    qs = sorted(
        qs,
        key=lambda x: x.get("acc_trade_price_24h", 0),
        reverse=True
    )[:MAX_SCAN]

    results = []

    for x in qs:
        a = analyze(x["market"], regime)
        if a: results.append(a)

    results.sort(
        key=lambda x: (x["score"], x["vol"]),
        reverse=True
    )

    for a in results:
        m = a["market"]

        if m in state["positions"]:
            continue

        last = state["last"].get(m)

        if last:
            try:
                elapsed = (now() - datetime.fromisoformat(last)).total_seconds()
                if elapsed < COOLDOWN * 3600:
                    continue
            except: pass

        sid = hashlib.sha1(
            f"{m}|{a['price']:.6f}|{a['score']}".encode()
        ).hexdigest()

        if sid in state["sent"]:
            continue

        if tg(message(a)):
            state["positions"][m] = {
                "entry": a["price"],
                "sl": a["stop"],
                "tp1": a["tp1"],
                "tp2": a["tp2"],
                "tp3": a["tp3"],
                "stage": 0,
                "created": now().isoformat()
            }

            state["sent"] = (state["sent"] + [sid])[-500:]
            state["last"][m] = now().isoformat()

            print("NEW SIGNAL:", name(m), a["score"])
            break

    state["version"] = V
    save(STATE, state)

if __name__ == "__main__":
    main()
