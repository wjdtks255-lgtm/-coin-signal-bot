import os, json, hashlib, requests, numpy as np, pandas as pd
from datetime import datetime, timezone

V = "7.1"
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

def reason_list(a):
    reasons = []
    if a.get("trend", 0) >= 3:
        reasons.append("<b>추세 정렬</b>: 1D · 4H · 1H 상위 시간대 정배열")
    elif a.get("trend", 0) == 2:
        reasons.append("<b>추세 우위</b>: 상위 시간대 상승 모멘텀 유지")
    else:
        reasons.append("<b>구조 회복</b>: 단기 이동평균선(EMA20) 재돌파")

    vol = a.get("vol", 0)
    if vol >= 180:
        reasons.append(f"<b>수급 강도</b>: 15M 거래량 {vol:.0f}% 급증 (평균 대비) 🔥")
    elif vol >= MIN_VOL:
        reasons.append(f"<b>수급 유입</b>: 15M 거래량 {vol:.0f}% 유입 확인")

    stype = a.get("type", "")
    if a.get("bounce") or stype == "BOTTOM BOUNCE":
        reasons.append("<b>시그널 포착</b>: 저점권 과매도 + 스토캐스틱 반전")
    elif stype == "BREAKOUT":
        reasons.append("<b>돌파 파동</b>: 최근 20봉 고점 강한 상방 돌파")
    else:
        reasons.append("<b>눌림목 완성</b>: EMA20 지지 확인 후 재반등")

    if a.get("recovery", True):
        reasons.append("<b>모멘텀 확인</b>: 단기 EMA20 회복 & 양봉 캔들 완성")

    return reasons[:4]

def message(a):
    p = a["price"]
    s = a["stop"]
    sl_pct = (s / p - 1) * 100
    tp1, tp2, tp3 = a["tp1"], a["tp2"], a["tp3"]
    score_tag = " [MAX]" if a["score"] == 100 else ""
    stype = a.get("type", "QUANT_SIGNAL")
    
    reasons = reason_list(a)
    reason_fmt = "\n".join([f"  ├ {r}" for r in reasons[:-1]] + [f"  └ {reasons[-1]}"]) if reasons else "  └ 기술적 반등 조건 충족"

    return f"""🚀 <b>[QUANT MTF CONFLUENCE SIGNAL]</b>
────────────────────────
💎 <b>{name(a['market'])}</b> | <code>{a['market']}</code>
🎯 <b>신호 점수</b>: {a['score']} / 100점<code>{score_tag}</code> | <b>{stype}</b>
────────────────────────
📈 <b>CORE BULLISH CATALYSTS (상승 핵심 근거)</b>
{reason_fmt}
────────────────────────
🎯 <b>TARGETS & EXPECTED RETURN</b>
  ├ <b>TP1</b>: {fp(tp1)} ({(tp1/p-1)*100:+.2f}%) | <b>1.5R</b>
  ├ <b>TP2</b>: {fp(tp2)} ({(tp2/p-1)*100:+.2f}%) | <b>2.0R</b>
  └ <b>TP3</b>: {fp(tp3)} ({(tp3/p-1)*100:+.2f}%) | <b>3.0R</b>

🛡️ <b>RISK MANAGEMENT & STRATEGY</b>
  ├ <b>진입가 (ENTRY)</b>: {fp(p)}
  ├ <b>손절가 (SL)</b>: {fp(s)} ({sl_pct:.2f}%) ⚠️
  ├ <b>RSI / ADX</b>: {a['rsi']:.1f} / {a['adx']:.1f}
  ├ <b>EMA20 이격</b>: {a['dist']:+.2f}% | <b>BTC Regime</b>: {a['regime']}
  └ <b>운영 규칙</b>: TP1 달성 시 SL→ENTRY / TP2 달성 시 SL→TP1
────────────────────────
💡 <i>Strategy: Multi-Timeframe Confluence & {stype}</i>
🔗 <a href="https://www.tradingview.com/symbols/UPBIT-{a['market'].replace('KRW-', '')}KRW/"><b>[ TradingView 차트 열기 ]</b></a>"""

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
