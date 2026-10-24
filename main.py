import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or "").strip()
STATE_FILE = "bot_state.json"
MAX_POSITIONS = 8
MIN_TURNOVER = 10_000_000_000
MIN_SCORE = 7
EXCLUDED = {"USDT", "BTT"}
KST = timezone(timedelta(hours=9))
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def now():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")

def price_fmt(p):
    if p >= 1000: return f"{p:,.0f}"
    if p >= 100: return f"{p:,.1f}"
    if p >= 1: return f"{p:,.2f}"
    return f"{p:.4f}"

def load_state():
    default = {"positions": {}, "history": [], "btc_regime": "NEUTRAL", "cooldowns": {}}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in default.items():
                    data.setdefault(k, v)
                if not isinstance(data["positions"], dict): data["positions"] = {}
                if not isinstance(data["history"], list): data["history"] = []
                if not isinstance(data["cooldowns"], dict): data["cooldowns"] = {}
                return data
        except Exception as e:
            print("STATE LOAD ERROR:", e)
    return default

def save_state(st):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)

def telegram(msg):
    if not TOKEN or not CHAT_ID:
        print("TELEGRAM ERROR: TELEGRAM_TOKEN 또는 CHAT_ID 누락")
        return False
    try:
        r = SESSION.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=15
        )
        if r.status_code != 200:
            print("TELEGRAM ERROR:", r.status_code, r.text[:300])
            return False
        return True
    except Exception as e:
        print("TELEGRAM ERROR:", e)
        return False

def api_get(url, params=None, timeout=8):
    try:
        r = SESSION.get(url, params=params, timeout=timeout)
        if r.status_code == 429:
            time.sleep(1.2)
            r = SESSION.get(url, params=params, timeout=timeout)
        if r.status_code != 200:
            print("API ERROR:", r.status_code, url.split("?")[0])
            return None
        return r.json()
    except Exception as e:
        print("API ERROR:", str(e)[:150])
        return None

def ema(values, period):
    if not values: return []
    k = 2 / (period + 1)
    out = [values[0]]
    for x in values[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out

def rsi(values, period=14):
    if len(values) < period + 1: return 50.0
    diffs = [values[i] - values[i-1] for i in range(1, len(values))]
    gains = [max(x, 0) for x in diffs]
    losses = [max(-x, 0) for x in diffs]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0: return 100.0 if ag > 0 else 50.0
    return 100 - 100 / (1 + ag / al)

def bollinger(values, period=20):
    x = values[-period:]
    avg = sum(x) / len(x)
    sd = (sum((v - avg) ** 2 for v in x) / len(x)) ** 0.5
    return avg + 2 * sd, avg - 2 * sd

def get_markets():
    data = api_get("https://api.upbit.com/v1/market/all",
                   {"isDetails": "false"})
    if not isinstance(data, list):
        return []
    return [x["market"] for x in data
            if x.get("market", "").startswith("KRW-")
            and x["market"].split("-")[-1] not in EXCLUDED]

def get_tickers(markets):
    out = {}
    for i in range(0, len(markets), 80):
        batch = markets[i:i+80]
        data = api_get("https://api.upbit.com/v1/ticker",
                       {"markets": ",".join(batch)})
        if isinstance(data, list):
            for x in data:
                out[x["market"]] = {
                    "price": float(x["trade_price"]),
                    "turnover": float(x["acc_trade_price_24h"])
                }
        time.sleep(0.08)
    return out

def get_candles(market, unit="15", count=80):
    data = api_get(f"https://api.upbit.com/v1/candles/minutes/{unit}",
                   {"market": market, "count": count})
    if not isinstance(data, list) or len(data) < 2:
        return [], [], [], []
    data.reverse()
    return (
        [float(x["trade_price"]) for x in data],
        [float(x["high_price"]) for x in data],
        [float(x["low_price"]) for x in data],
        [float(x["candle_acc_trade_volume"]) for x in data]
    )

def get_btc_regime():
    closes, _, _, _ = get_candles("KRW-BTC", "60", 70)
    closes = closes[:-1]
    if len(closes) < 50:
        return "NEUTRAL"
    e20, e50 = ema(closes, 20)[-1], ema(closes, 50)[-1]
    if closes[-1] > e20 > e50: return "BULLISH"
    if closes[-1] < e20 < e50: return "BEARISH"
    return "NEUTRAL"

def analyze(closes, highs, lows, volumes, live_price):
    closes, highs, lows, volumes = (
        closes[:-1], highs[:-1], lows[:-1], volumes[:-1]
    )
    if min(len(closes), len(highs), len(lows), len(volumes)) < 51:
        return None, "데이터 부족"

    e20s, e50s = ema(closes, 20), ema(closes, 50)
    e20, e50 = e20s[-1], e50s[-1]
    rv, prev_rsi = rsi(closes), rsi(closes[:-1])
    upper, _ = bollinger(closes, 20)
    avg_vol = sum(volumes[-21:-1]) / 20
    vr = volumes[-1] / avg_vol if avg_vol > 0 else 0

    score, reasons = 0, []
    if live_price > e20:
        score += 2
        reasons.append("EMA20 상회")
    if e20 > e50:
        score += 2
        reasons.append("EMA 정배열")
    if prev_rsi <= 50 and rv > prev_rsi:
        score += 2
        reasons.append(f"RSI 회복 {rv:.1f}")
    elif 50 <= rv <= 68:
        score += 2
        reasons.append(f"RSI 모멘텀 {rv:.1f}")
    if vr >= 3:
        score += 3
        reasons.append(f"거래량 {vr:.1f}배")
    elif vr >= 2:
        score += 2
        reasons.append(f"거래량 {vr:.1f}배")
    elif vr >= 1.4:
        score += 1
        reasons.append(f"거래량 {vr:.1f}배")
    if live_price >= upper:
        score += 2
        reasons.append("볼린저 상단 돌파")

    trend_ok = live_price > e20 and e20 > e50
    recovery_ok = trend_ok and prev_rsi <= 52 and rv > prev_rsi and vr >= 1.3
    breakout_ok = trend_ok and live_price >= upper and vr >= 1.5
    momentum_ok = trend_ok and 50 <= rv <= 68 and vr >= 2.0
    valid_setup = recovery_ok or breakout_ok or momentum_ok

    if score < MIN_SCORE:
        return None, f"점수 미달({score})"
    if not valid_setup:
        return None, "진입 구조 미충족"
    if rv >= 75:
        return None, "RSI 과열"

    risk = live_price * 0.05
    sl = live_price - risk
    tp1 = live_price + risk * 1.2
    tp2 = live_price + risk * 2.2
    high_momentum = vr >= 3 and score >= 9
    tp3 = live_price + risk * 3.5 if high_momentum else None

    return {
        "price": live_price, "sl": sl, "tp1": tp1, "tp2": tp2,
        "tp3": tp3, "score": score, "rsi": rv, "vol_ratio": vr,
        "reasons": reasons, "high_momentum": high_momentum
    }, "통과"

def close_position(st, coin, info, outcome, price):
    entry = float(info.get("entry", price))
    pnl = (price / entry - 1) * 100 if entry else 0
    labels = {
        "tp3": "TP3 목표 도달",
        "tp2_end": "TP2 이후 수익 종료",
        "trailing": "트레일링 익절",
        "tp1_end": "TP1 이후 본전 보호 종료",
        "initial_sl": "초기 손절"
    }
    st["history"].append({
        "symbol": coin, "outcome": outcome, "entry": entry,
        "exit": price, "pnl_pct": round(pnl, 3), "time": now()
    })
    st["history"] = st["history"][-1000:]
    st["positions"].pop(coin, None)
    
    st.setdefault("cooldowns", {})[coin] = time.time()

    link = f"https://upbit.com/exchange?code=CRIX.UPBIT.KRW-{coin}"
    msg = (
        f"📊 <b>[현물 시그널 종료]</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"• 종목: <code>#{coin}</code>\n"
        f"• 결과: <b>{labels.get(outcome, outcome)}</b>\n"
        f"• 진입가: ₩{price_fmt(entry)}\n"
        f"• 종료가: ₩{price_fmt(price)} ({pnl:+.2f}%)\n"
        f"• <a href=\"{link}\">업비트 차트</a>\n"
        f"⏱ <code>{now()}</code>"
    )
    telegram(msg)
    save_state(st)

def monitor_positions(st):
    if not st["positions"]:
        return False
    markets = [f"KRW-{c}" for c in st["positions"]]
    tickers = get_tickers(markets)
    changed = False

    for coin, info in list(st["positions"].items()):
        ticker = tickers.get(f"KRW-{coin}")
        if not ticker:
            continue
        cur = ticker["price"]
        entry = float(info.get("entry", cur))
        sl = float(info.get("sl", entry * 0.95))
        tp1 = float(info.get("tp1", entry * 1.06))
        tp2 = float(info.get("tp2", entry * 1.11))
        tp3 = info.get("tp3")
        tp3 = float(tp3) if tp3 else None
        hit = int(info.get("max_tp_hit", 0))
        peak = max(float(info.get("highest_price", entry)), cur)
        info["highest_price"] = peak

        if tp3 and cur >= tp3:
            close_position(st, coin, info, "tp3", cur)
            changed = True
            continue

        if cur >= tp2 and hit < 2:
            if tp3 is None:
                close_position(st, coin, info, "tp2_end", cur)
                changed = True
                continue
            info["max_tp_hit"] = 2
            info["sl"] = max(float(info.get("sl", entry)), tp1)
            changed = True
            telegram(
                f"🎯 <b>[현물 시그널] TP2 도달</b>\n"
                f"종목: <code>#{coin}</code>\n"
                f"현재가: ₩{price_fmt(cur)}\n"
                f"보호 손절가를 TP1(₩{price_fmt(tp1)})로 상향했습니다.\n"
                f"⏱ <code>{now()}</code>"
            )
            save_state(st)
            continue

        if cur >= tp1 and hit < 1:
            info["max_tp_hit"] = 1
            info["sl"] = entry
            changed = True
            telegram(
                f"🎯 <b>[현물 시그널] TP1 도달</b>\n"
                f"종목: <code>#{coin}</code>\n"
                f"현재가: ₩{price_fmt(cur)}\n"
                f"손절가를 진입가(₩{price_fmt(entry)})로 조정했습니다.\n"
                f"⏱ <code>{now()}</code>"
            )
            save_state(st)
            continue

        if cur <= sl:
            outcome = "tp2_end" if hit >= 2 else (
                "tp1_end" if hit >= 1 else "initial_sl"
            )
            close_position(st, coin, info, outcome, cur)
            changed = True
            continue

        peak_profit = (peak / entry - 1) * 100 if entry else 0
        
        # 💡 트레일링 스탑 완화: 최고점 대비 3.0% 이상 하락할 때만 작동 (기존 1.5%에서 완화)
        if peak_profit >= 3.0 and cur > entry and cur <= peak * (1 - 0.03):
            outcome = "tp2_end" if hit >= 2 else "trailing"
            close_position(st, coin, info, outcome, cur)
            changed = True
            continue

        st["positions"][coin] = info

    save_state(st)
    return changed

def send_status(st, btc_regime):
    history = st["history"]
    positions = st["positions"]
    counts = {k: sum(1 for x in history if x.get("outcome") == k)
              for k in ["tp3", "tp2_end", "trailing", "tp1_end", "initial_sl"]}
    wins = counts["tp3"] + counts["tp2_end"] + counts["trailing"]
    draws, losses = counts["tp1_end"], counts["initial_sl"]
    decisive = wins + losses
    rate = wins / decisive * 100 if decisive else 0
    tickers = get_tickers([f"KRW-{c}" for c in positions]) if positions else {}

    msg = (
        f"📊 <b>UPBIT SPOT QUANT SCANNER</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"BTC 추세: <b>{btc_regime}</b>\n"
        f"총 종료 시그널: <b>{len(history)}건</b>\n\n"
        f"🎯 TP3 종료: {counts['tp3']}건\n"
        f"🎯 TP2 이후 종료: {counts['tp2_end']}건\n"
        f"📈 트레일링 익절: {counts['trailing']}건\n"
        f"🤝 TP1 이후 본전 보호 종료: {draws}건\n"
        f"🛑 초기 손절: {losses}건\n\n"
        f"🏆 승률(승/패, 무 제외): <b>{rate:.1f}%</b>\n"
        f"승 {wins} / 패 {losses} / 무 {draws}\n"
        f"현재 포지션: <b>{len(positions)}/{MAX_POSITIONS}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
    )
    for coin, info in positions.items():
        cur = tickers.get(f"KRW-{coin}", {}).get("price", info["entry"])
        entry = float(info["entry"])
        pnl = (cur / entry - 1) * 100 if entry else 0
        msg += (
            f"• <code>#{coin}</code> ₩{price_fmt(cur)} "
            f"({pnl:+.2f}%) | TP{info.get('max_tp_hit', 0)} 진행\n"
        )
    msg += f"\n⏱ <code>{now()}</code>\n<i>모의 시그널 추적 통계이며 실제 계좌 수익률이 아닙니다.</i>"
    telegram(msg)

def main():
    print("=" * 55)
    print("UPBIT SPOT QUANT SCANNER V16.4 (LOOSE TRAILING)")
    print("시간:", now())
    print("=" * 55)

    if not TOKEN or not CHAT_ID:
        print("경고: 텔레그램 환경변수 누락. 신호 전송 불가")

    st = load_state()
    btc = get_btc_regime()
    st["btc_regime"] = btc
    print("BTC 추세:", btc)

    changed = monitor_positions(st)
    slots = MAX_POSITIONS - len(st["positions"])
    stats = {"markets": 0, "turnover": 0, "data": 0,
             "score": 0, "structure": 0, "overheat": 0,
             "signals": 0, "sent_fail": 0}
    new_signal = False

    if slots > 0:
        markets = get_markets()
        print("KRW 마켓 수:", len(markets))
        tickers = get_tickers(markets)
        candidates = [
            (m, t) for m, t in tickers.items()
            if t["turnover"] >= MIN_TURNOVER
        ]
        candidates.sort(key=lambda x: x[1]["turnover"], reverse=True)
        stats["markets"] = len(tickers)
        stats["turnover"] = len(candidates)
        print(f"거래대금 필터 통과: {len(candidates)}개 (최소 100억 원)")

        cooldowns = st.setdefault("cooldowns", {})
        current_time = time.time()
        COOLDOWN_SECONDS = 3 * 3600  # 3시간 쿨다운

        for market, ticker in candidates:
            coin = market.replace("KRW-", "")
            
            if coin in st["positions"] or coin in EXCLUDED:
                continue
            
            if current_time - cooldowns.get(coin, 0) < COOLDOWN_SECONDS:
                continue

            if len(st["positions"]) >= MAX_POSITIONS:
                break

            closes, highs, lows, volumes = get_candles(market, "15", 80)
            time.sleep(0.12)
            if len(closes) < 52:
                stats["data"] += 1
                continue

            result, reason = analyze(
                closes, highs, lows, volumes, ticker["price"]
            )
            if result is None:
                if reason.startswith("점수 미달"):
                    stats["score"] += 1
                elif reason == "데이터 부족":
                    stats["data"] += 1
                elif reason == "RSI 과열":
                    stats["overheat"] += 1
                else:
                    stats["structure"] += 1
                continue

            stats["signals"] += 1
            r = result
            tp3_line = (
                f"🎯 TP3: ₩{price_fmt(r['tp3'])} "
                f"(+{(r['tp3']/r['price']-1)*100:.1f}%)\n"
                if r["tp3"] else ""
            )
            link = f"https://upbit.com/exchange?code=CRIX.UPBIT.KRW-{coin}"
            msg = (
                f"🟢 <b>[업비트 현물 매수 시그널]</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"• 종목: <code>#{coin}</code>\n"
                f"• BTC 1시간 추세: <b>{btc}</b>\n"
                f"• 신호 점수: <b>{r['score']}점</b>\n"
                f"• RSI: {r['rsi']:.1f} | 거래량: {r['vol_ratio']:.1f}배\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💰 진입가: ₩{price_fmt(r['price'])}\n"
                f"🎯 TP1: ₩{price_fmt(r['tp1'])} "
                f"(+{(r['tp1']/r['price']-1)*100:.1f}%)\n"
                f"🎯 TP2: ₩{price_fmt(r['tp2'])} "
                f"(+{(r['tp2']/r['price']-1)*100:.1f}%)\n"
                f"{tp3_line}\n"
                f"🛡 손절가: ₩{price_fmt(r['sl'])} (-5.0%)\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"포착 근거: {', '.join(r['reasons'])}\n"
                f"<a href=\"{link}\">업비트 거래소 바로가기</a>\n"
                f"⏱ <code>{now()}</code>"
            )

            if not telegram(msg):
                stats["sent_fail"] += 1
                continue

            st["positions"][coin] = {
                "entry": r["price"], "sl": r["sl"],
                "tp1": r["tp1"], "tp2": r["tp2"], "tp3": r["tp3"],
                "highest_price": r["price"], "max_tp_hit": 0,
                "time": now()
            }
            save_state(st)
            new_signal = True
            print(f"NEW SIGNAL: {coin} score={r['score']}")
            time.sleep(1)

    save_state(st)
    print("=" * 55)
    print("스캔 결과:", stats)
    print("현재 포지션:", len(st["positions"]), "/", MAX_POSITIONS)
    print("=" * 55)

    if changed or new_signal:
        send_status(st, btc)

if __name__ == "__main__":
    main()
