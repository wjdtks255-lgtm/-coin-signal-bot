import os
import json
import hashlib
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone

# ==========================================
# [승률 극대화(High Win-Rate) & 포지션 추적 설정]
# ==========================================
V = "8.1"
BASE = "https://api.upbit.com/v1"
STATE = "tracked_coins.json"

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# 🛡️ 승률 강화를 위한 필터 수치 상향
MIN_VALUE = 5_000_000_000   # 최소 24시간 거래대금 50억 이상 (잡코인 제거)
MAX_SCAN = 80               # 상위 80개 유동성 코인만 스캔
MIN_SCORE = 85              # 최소 진입 점수 85점 이상 (A급 신호만)
WEAK_SCORE = 90             # 하락유의장 진입 점수 90점 이상
CRASH_SCORE = 95            # 폭락장 진입 점수 95점 이상
COOLDOWN = 6                # 재진입 쿨다운 6시간 연장

MIN_VOL = 200               # 최소 거래량 유입 비율 200% 이상
MAX_EMA = 3.5               # EMA20 이격률 최대 3.5% (과열 진입 금지)
MIN_RSI = 45                # RSI 최소 45 이상
MAX_RSI = 70                # RSI 70 이상 과매수 구간 진입 금지
MIN_SL = 1.0                # 최소 손절 폭 (%)
MAX_SL = 4.5                # 최대 손절 폭 (%)

S = requests.Session()
NAMES = {}


def now():
    return datetime.now(timezone.utc)

def load(p, d):
    try:
        with open(p, encoding="utf-8") as f: return json.load(f)
    except: return d

def save(p, d):
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, separators=",:")
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
        print("[API 오류]:", e)
        return None

def init_markets():
    global NAMES
    # 거래유의 종목 스캔 제외 처리
    for x in api("/market/all", {"isDetails": "true"}) or []:
        m = x["market"]
        if m.startswith("KRW-"):
            event = x.get("market_event", {})
            if event.get("warning"):
                continue
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
        "trade_price": "close", "candle_acc_trade_volume": "volume"
    })
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
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def adx(df, n=14):
    u = df.high.diff()
    d = -df.low.diff()
    plus = pd.Series(np.where((u > d) & (u > 0), u, 0), index=df.index)
    minus = pd.Series(np.where((d > u) & (d > 0), d, 0), index=df.index)
    pc = df.close.shift()
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1/n, adjust=False).mean()
    p = 100 * plus.ewm(alpha=1/n, adjust=False).mean() / a.replace(0, np.nan)
    q = 100 * minus.ewm(alpha=1/n, adjust=False).mean() / a.replace(0, np.nan)
    return (100 * (p - q).abs() / (p + q).replace(0, np.nan)).ewm(alpha=1/n, adjust=False).mean().fillna(0)

def tf(df):
    if df is None: return None
    e20 = ema(df.close, 20)
    e50 = ema(df.close, 50)
    return {
        "p": float(df.close.iloc[-1]),
        "e20": float(e20.iloc[-1]),
        "e50": float(e50.iloc[-1]),
        "rsi": float(rsi(df.close).iloc[-1]),
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

# ==========================================
# [승률 최우선 퀀트 분석 알고리즘]
# ==========================================
def analyze(m, regime):
    ds = [candles(m, u) for u in (15, 60, 240, 1440)]
    d15, d1, d4, dd = ds
    a15, a1, a4, ad = [tf(x) for x in ds]
    if any(x is None for x in (a15, a1, a4, ad)): return None

    p = a15["p"]
    e = a15["e20"]
    dist = (p / e - 1) * 100

    if dist > MAX_EMA or a15["rsi"] < MIN_RSI or a15["rsi"] > MAX_RSI:
        return None

    trend = sum(x["p"] >= x["e20"] and x["e20"] >= x["e50"] for x in (a1, a4, ad))
    if trend < 3:
        return None

    avg = float(d15.volume.iloc[-21:-1].mean())
    vol = float(d15.volume.iloc[-1] / avg * 100) if avg > 0 else 0
    if vol < MIN_VOL:
        return None

    high_p = d15.high.iloc[-1]
    low_p = d15.low.iloc[-1]
    open_p = d15.open.iloc[-1]
    close_p = d15.close.iloc[-1]
    total_range = high_p - low_p
    upper_wick = high_p - max(open_p, close_p)
    
    if total_range > 0 and (upper_wick / total_range) > 0.40:
        return None

    prev_high = float(d15.high.iloc[-21:-1].max())
    breakout = p > prev_high
    e20prev = float(ema(d15.close, 20).iloc[-2])
    pullback = (float(d15.low.iloc[-9:-1].min()) <= e20prev * 1.015) and (p > e)

    if not (breakout or pullback):
        return None

    score = 50
    score += 15 if vol >= 300 else 10 if vol >= 200 else 0
    score += 10 if a15["rsi"] >= 50 else 0
    score += 10 if a15["adx"] >= 20 else 5
    score += 15 if breakout else 10 if pullback else 0
    score = min(score, 100)

    threshold = CRASH_SCORE if regime == "CRASH" else WEAK_SCORE if regime == "WEAK" else MIN_SCORE
    if score < threshold:
        return None

    av = float(atr(d15).iloc[-1])
    swing = float(d15.low.iloc[-9:-1].min())
    stop = max(p - av * 1.0, swing - av * 0.1)
    risk = p - stop

    if risk < p * MIN_SL / 100:
        stop = p * (1 - MIN_SL / 100)
        risk = p - stop

    if risk > p * MAX_SL / 100:
        return None

    return {
        "market": m,
        "price": p,
        "stop": stop,
        "tp1": p + risk * 1.5,
        "tp2": p + risk * 2.0,
        "tp3": p + risk * 3.0,
        "score": score,
        "rsi": a15["rsi"],
        "adx": a15["adx"],
        "vol": vol,
        "dist": dist,
        "regime": regime,
        "type": "BREAKOUT" if breakout else "PULLBACK",
        "trend": trend
    }

def tg(msg):
    if not TOKEN or not CHAT: return False
    try:
        return S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15
        ).ok
    except: return False

def message_entry(a):
    p = a["price"]
    s = a["stop"]
    sl_pct = (s / p - 1) * 100
    tp1, tp2, tp3 = a["tp1"], a["tp2"], a["tp3"]
    stype = a.get("type", "QUANT")

    return f"""🔴 🔥 <b>HIGH QUALITY ENTRY (A급 현물 진입)</b>

━━━━━━━━━━━━━━━━━━━━
🪙 <b>{name(a['market'])}</b> <code>({a['market']})</code>
📌 <b>BUY / LONG</b> 🟢
🏆 Quality : A
⭐ Entry Score : {a['score']}/100 | {stype}
━━━━━━━━━━━━━━━━━━━━

💰 TRADE PLAN
├ Entry : <code>{fp(p)} 원</code>
├ SL : <code>{fp(s)} 원</code> (<b>{sl_pct:.2f}%</b>)
├ TP1 : <code>{fp(tp1)} 원</code> (<b>+{(tp1/p-1)*100:.2f}%</b>) | 1.5R
├ TP2 : <code>{fp(tp2)} 원</code> (<b>+{(tp2/p-1)*100:.2f}%</b>) | 2.0R
└ TP3 : <code>{fp(tp3)} 원</code> (<b>+{(tp3/p-1)*100:.2f}%</b>) | 3.0R

📊 MARKET
├ RSI 15M : {a['rsi']:.1f}
├ ADX 15M : {a['adx']:.1f}
├ Volume : +{a['vol']:.0f}%
└ Upper Wick Filter : 통과 (Clean Candle)

⚙️ RISK MANAGEMENT
├ TP1 달성 시 ➔ SL = ENTRY (본절가 자동 보장)
└ TP3 달성 시 ➔ TRACKING END

🔗 <a href="https://www.tradingview.com/symbols/UPBIT-{a['market'].replace('KRW-', '')}KRW/">TradingView 차트 보기</a>"""

# ==========================================
# [실시간 포지션 익절/손절 추적 엔지니어링]
# ==========================================
def track_positions(state):
    positions = state.get("positions", {})
    if not positions:
        return

    active_markets = list(positions.keys())
    tickers = api("/ticker", {"markets": ",".join(active_markets)}) or []
    ticker_map = {t["market"]: float(t["trade_price"]) for t in tickers}

    for m in active_markets:
        pos = positions[m]
        curr_p = ticker_map.get(m)
        if not curr_p:
            continue

        entry = pos["entry"]
        sl = pos["sl"]
        tp1, tp2, tp3 = pos["tp1"], pos["tp2"], pos["tp3"]

        # 1. SL (손절) 체크
        if curr_p <= sl:
            pnl = (curr_p / entry - 1) * 100
            msg = f"❌ <b>[SL TRIGGERED]</b> {name(m)}\n손절가 도달 완료: <code>{fp(curr_p)} 원</code> (수익률: <b>{pnl:.2f}%</b>)\n포지션을 정리합니다."
            if tg(msg):
                print(f"TG {m} SL: ok=True")
                del state["positions"][m]
            continue

        # 2. TP1 도달 체크
        if not pos.get("tp1_hit") and curr_p >= tp1:
            pos["tp1_hit"] = True
            pos["sl"] = entry # 스탑로스를 본절가로 수정 (Risk Free)
            pnl = (curr_p / entry - 1) * 100
            msg = f"🎯 <b>[TP1 REACHED]</b> {name(m)}\n1차 목표가 달성: <code>{fp(curr_p)} 원</code> (수익률: <b>+{pnl:.2f}%</b>)\n🛡️ <b>손절가가 진입가({fp(entry)}원)로 수정되었습니다 (본절가 방어).</b>"
            if tg(msg):
                print(f"TG {m} TP1: ok=True")

        # 3. TP2 도달 체크
        if pos.get("tp1_hit") and not pos.get("tp2_hit") and curr_p >= tp2:
            pos["tp2_hit"] = True
            pnl = (curr_p / entry - 1) * 100
            msg = f"🎯🎯 <b>[TP2 REACHED]</b> {name(m)}\n2차 목표가 달성: <code>{fp(curr_p)} 원</code> (수익률: <b>+{pnl:.2f}%</b>)\n추가 익절을 진행하세요."
            if tg(msg):
                print(f"TG {m} TP2: ok=True")

        # 4. TP3 (최종) 도달 체크
        if pos.get("tp2_hit") and curr_p >= tp3:
            pnl = (curr_p / entry - 1) * 100
            msg = f"🚀 <b>[TP3 TARGET CLEARED]</b> {name(m)}\n최종 3차 목표가 완충: <code>{fp(curr_p)} 원</code> (수익률: <b>+{pnl:.2f}%</b>)\n포지션 추적을 종료합니다."
            if tg(msg):
                print(f"TG {m} TP3 CLEAR: ok=True")
                del state["positions"][m]

def main():
    print(f"UPBIT SPOT FINAL ENTRY BOT V{V} STARTED...")
    init_markets()

    state = load(STATE, {"version": V, "positions": {}, "sent": [], "last": {}})
    state.setdefault("positions", {})
    state.setdefault("sent", [])
    state.setdefault("last", {})

    # 1. 기존 포지션 익절/손절 실시간 추적
    track_positions(state)

    # 2. 시장 레짐 및 신규 마켓 스캔
    regime, c15, c1 = btc_regime()
    print(f"BTC Regime: {regime} | 15M: {c15:+.2f}% | 1H: {c1:+.2f}%")

    qs = api("/ticker", {"markets": ",".join(NAMES.keys())}) or []
    qs = [x for x in qs if num(x.get("acc_trade_price_24h")) >= MIN_VALUE]
    qs = sorted(qs, key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)[:MAX_SCAN]

    results = [analyze(x["market"], regime) for x in qs]
    results = [a for a in results if a is not None]
    results.sort(key=lambda x: (x["score"], x["vol"]), reverse=True)

    final_signals = 0
    for a in results:
        m = a["market"]
        
        # 이미 추적 중인 포지션 스킵
        if m in state["positions"]:
            print(f"SKIP ACTIVE: {m}")
            continue

        # 쿨다운 스킵
        last = state["last"].get(m)
        if last:
            try:
                if (now() - datetime.fromisoformat(last)).total_seconds() < COOLDOWN * 3600:
                    print(f"SKIP COOLDOWN: {m}")
                    continue
            except: pass

        sid = hashlib.sha1(f"{m}|{a['price']:.6f}|{a['score']}".encode()).hexdigest()
        if sid in state["sent"]: continue

        if tg(message_entry(a)):
            state["sent"] = (state["sent"] + [sid])[-500:]
            state["last"][m] = now().isoformat()
            
            # 신규 포지션 추적 등록
            state["positions"][m] = {
                "entry": a["price"],
                "sl": a["stop"],
                "tp1": a["tp1"],
                "tp2": a["tp2"],
                "tp3": a["tp3"],
                "tp1_hit": False,
                "tp2_hit": False,
                "time": now().isoformat()
            }
            final_signals += 1
            print(f"TG {m} BUY A: http=200 ok=True")
            print(f"SIGNAL SENT: {m} A {a['score']}")

    print(f"FINAL SIGNALS: {final_signals}")
    print(f"ACTIVE POSITIONS: {len(state['positions'])}")
    print("DONE")

    state["version"] = V
    save(STATE, state)

if __name__ == "__main__":
    main()
