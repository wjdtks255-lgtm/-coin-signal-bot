import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta

# ==========================================
# 텔레그램 및 기본 설정
# ==========================================
TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or "").strip()

STATE_FILE = "bot_state.json"
MAX_POSITIONS = 15
KST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json"
}

def now():
    return datetime.now(KST)

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default

def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def telegram(text):
    if not TOKEN or not CHAT_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=15
        )
        return r.status_code == 200
    except Exception as e:
        print("❌ 텔레그램 전송 실패:", e)
        return False

def format_price(price):
    if price >= 1000:
        return f"{price:,.0f}"
    elif price >= 100:
        return f"{price:,.1f}"
    elif price >= 1:
        return f"{price:,.2f}"
    else:
        return f"{price:.4f}"

# ==========================================
# 지표 자체 계산 함수
# ==========================================
def calc_ema(prices, period):
    if len(prices) < period:
        return [prices[-1]] * len(prices)
    k = 2 / (period + 1)
    ema = [prices[0]]
    for p in prices[1:]:
        ema.append((p * k) + (ema[-1] * (1 - k)))
    return ema

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return closes[-1] * 0.02
    tr_list = []
    for i in range(1, len(closes)):
        h, l, pc = highs[i], lows[i], closes[i-1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)
    return sum(tr_list[-period:]) / period

# ==========================================
# 업비트 API 데이터 수집
# ==========================================
def get_krw_markets():
    try:
        url = "https://api.upbit.com/v1/market/all?isDetails=false"
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            markets = [m["market"] for m in res.json() if m["market"].startswith("KRW-")]
            return sorted(markets)
    except Exception as e:
        print("⚠️ 마켓 목록 수집 에러:", e)
    return ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP"]

def fetch_candles(market, unit="15", count=80):
    try:
        url = f"https://api.upbit.com/v1/candles/minutes/{unit}?market={market}&count={count}"
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            data = res.json()
            data.reverse()
            closes = [float(x['trade_price']) for x in data]
            highs = [float(x['high_price']) for x in data]
            lows = [float(x['low_price']) for x in data]
            return closes, highs, lows
    except Exception:
        pass
    return [], [], []

def get_current_prices(markets):
    try:
        url = f"https://api.upbit.com/v1/ticker?markets={','.join(markets)}"
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            data = res.json()
            return {item['market']: float(item['trade_price']) for item in data}
    except Exception:
        pass
    return {}

def get_btc_regime():
    closes, _, _ = fetch_candles("KRW-BTC", unit="15", count=60)
    if not closes or len(closes) < 50:
        return "NEUTRAL"
    
    price = closes[-1]
    ema20 = calc_ema(closes, 20)[-1]
    ema50 = calc_ema(closes, 50)[-1]

    if price > ema20 > ema50:
        return "BULLISH"
    elif price < ema20 < ema50:
        return "BEARISH"
    else:
        return "NEUTRAL"

def analyze_market(closes, highs, lows):
    if len(closes) < 50:
        return None

    ema20_list = calc_ema(closes, 20)
    ema50_list = calc_ema(closes, 50)
    
    price = closes[-1]
    ema20 = ema20_list[-1]
    ema50 = ema50_list[-1]
    
    rsi = calc_rsi(closes, 14)
    prev_rsi = calc_rsi(closes[:-1], 14)
    atr = calc_atr(highs, lows, closes, 14)

    score = 0
    reasons = []

    if price > ema20:
        score += 3
        reasons.append("EMA20 상회")
    if ema20 > ema50:
        score += 3
        reasons.append("EMA20 > EMA50 정배열")
    if prev_rsi <= 48 and rsi > prev_rsi:
        score += 2
        reasons.append(f"RSI 반등 ({rsi:.1f})")
    elif 50 <= rsi <= 68:
        score += 2
        reasons.append(f"RSI 모멘텀 ({rsi:.1f})")

    if score >= 6:
        risk_pct = max((atr / price) * 100 * 1.5, 2.5)
        risk_amount = price * (risk_pct / 100)

        sl = price - risk_amount
        tp1 = price + (risk_amount * 2.0)
        tp2 = price + (risk_amount * 4.0)

        return {
            "price": price, "sl": sl, "tp1": tp1, "tp2": tp2,
            "tp1_pct": ((tp1 - price) / price) * 100,
            "tp2_pct": ((tp2 - price) / price) * 100,
            "sl_pct": ((sl - price) / price) * 100,
            "rsi": rsi, "score": score, "reasons": reasons
        }
    return None

def monitor_positions(positions):
    if not positions:
        return positions

    market_list = [f"KRW-{coin}" for coin in positions.keys()]
    current_prices = get_current_prices(market_list)
    updated_positions = {}

    for coin, info in positions.items():
        market = f"KRW-{coin}"
        cur_price = current_prices.get(market)
        if not cur_price:
            updated_positions[coin] = info
            continue

        entry = info["entry"]
        sl = info["sl"]
        tp1 = info["tp1"]
        tp2 = info["tp2"]
        upbit_link = f"https://upbit.com/exchange?code=CRIX.UPBIT.KRW-{coin}"

        # 1. 손절(SL) 도달
        if cur_price <= sl:
            loss_pct = ((cur_price - entry) / entry) * 100
            msg = (
                f"🛡 <b>[코인 현물] 손절가(SL) 도달 청산</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>종목명</b> : <code>#{coin}</code>\n"
                f"• <b>진입가</b> : ₩{format_price(entry)}\n"
                f"• <b>청산가</b> : <b>₩{format_price(cur_price)}</b> ({loss_pct:.1f}%)\n"
                f"📈 <a href=\"{upbit_link}\"><b>[업비트 거래소 이동]</b></a>\n"
                f"⏱ <code>{now().strftime('%Y-%m-%d %H:%M:%S KST')}</code>"
            )
            telegram(msg)
            print(f"🛡 SL 도달 청산: #{coin}")
            continue # 포지션 제거

        # 2. 2차 목표가(TP2) 도달 (최종 익절)
        elif cur_price >= tp2:
            profit_pct = ((cur_price - entry) / entry) * 100
            msg = (
                f"🏆 <b>[코인 현물] 2차 목표가(TP2) 도달 달성! (WIN)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>종목명</b> : <code>#{coin}</code>\n"
                f"• <b>진입가</b> : ₩{format_price(entry)}\n"
                f"• <b>최종 익절가</b> : <b>₩{format_price(cur_price)}</b> (<b>+{profit_pct:.1f}%</b>)\n"
                f"📈 <a href=\"{upbit_link}\"><b>[업비트 거래소 이동]</b></a>\n"
                f"⏱ <code>{now().strftime('%Y-%m-%d %H:%M:%S KST')}</code>"
            )
            telegram(msg)
            print(f"🏆 TP2 달성 청산: #{coin}")
            continue # 포지션 제거

        # 3. 1차 목표가(TP1) 도달
        elif cur_price >= tp1 and not info.get("tp1_hit", False):
            profit_pct = ((cur_price - entry) / entry) * 100
            msg = (
                f"🎯 <b>[코인 현물] 1차 목표가(TP1) 도달 성공!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>종목명</b> : <code>#{coin}</code>\n"
                f"• <b>현재가</b> : ₩{format_price(cur_price)} (+{profit_pct:.1f}%)\n"
                f"🛡 <i>안전을 위해 손절가를 진입가(본전)로 상향하세요!</i>\n"
                f"📈 <a href=\"{upbit_link}\"><b>[업비트 거래소 이동]</b></a>\n"
                f"⏱ <code>{now().strftime('%Y-%m-%d %H:%M:%S KST')}</code>"
            )
            telegram(msg)
            info["tp1_hit"] = True

        updated_positions[coin] = info

    return updated_positions

def main():
    print("=" * 55)
    print("🚀 UPBIT SPOT QUANT SCANNER V8.5 STARTED...")
    print("=" * 55)

    state = load_json(STATE_FILE, {"positions": {}, "signals": {}})
    
    # 1. 기존 보유 포지션 실시간 가격 감시 (익절/손절 체크)
    print("👀 기존 보유 포지션 모니터링 중...")
    positions = monitor_positions(state.get("positions", {}))
    state["positions"] = positions
    save_json(STATE_FILE, state)

    if len(positions) >= MAX_POSITIONS:
        print(f"⚠️ 최대 포지션 한도 도달 ({len(positions)}/{MAX_POSITIONS}). 신규 스킵.")
        return

    # 2. 신규 시그널 스캔
    btc_regime = get_btc_regime()
    markets = get_krw_markets()
    print(f"🔍 업비트 현물 전 종목 신규 스캔 중... (현재 보유: {len(positions)}/{MAX_POSITIONS})")

    new_signals = 0

    for market in markets:
        coin_symbol = market.replace("KRW-", "")
        if coin_symbol in positions:
            continue

        closes, highs, lows = fetch_candles(market, unit="15")
        if not closes:
            continue

        result = analyze_market(closes, highs, lows)
        if result:
            new_signals += 1
            price = result["price"]
            sl = result["sl"]
            tp1 = result["tp1"]
            tp2 = result["tp2"]

            upbit_link = f"https://upbit.com/exchange?code=CRIX.UPBIT.KRW-{coin_symbol}"

            msg = (
                f"🟢 <b>[코인 현물] 신규 매수 시그널</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>종목명</b> : <code>#{coin_symbol}</code> (KRW 마켓)\n"
                f"• <b>시장 추세</b> : <code>BTC {btc_regime}</code>\n"
                f"• <b>RSI (14)</b> : <code>{result['rsi']:.1f}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 <b>진입가</b> : <code>₩{format_price(price)}</code>\n"
                f"🎯 <b>1차 목표가 (TP1)</b> : <code>₩{format_price(tp1)}</code> (<b>+{result['tp1_pct']:.1f}%</b>)\n"
                f"🎯 <b>2차 목표가 (TP2)</b> : <code>₩{format_price(tp2)}</code> (<b>+{result['tp2_pct']:.1f}%</b>)\n"
                f"🛡 <b>손절가 (SL)</b> : <code>₩{format_price(sl)}</code> ({result['sl_pct']:.1f}%)\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚙️ <b>포착 조건</b> : {', '.join(result['reasons'])}\n"
                f"📈 <a href=\"{upbit_link}\"><b>[업비트 거래소 바로가기]</b></a>\n"
                f"⏱ <code>{now().strftime('%Y-%m-%d %H:%M:%S KST')}</code>"
            )

            telegram(msg)
            print(f"🎯 시그널 포착! #{coin_symbol} -> 전송 완료")

            positions[coin_symbol] = {
                "entry": price, "sl": sl, "tp1": tp1, "tp2": tp2,
                "tp1_hit": False, "time": now().strftime("%Y-%m-%d %H:%M:%S")
            }
            time.sleep(1)

            if len(positions) >= MAX_POSITIONS:
                break

    state["positions"] = positions
    save_json(STATE_FILE, state)

    print(f"✅ 스캔 완료 | 신규 시그널: {new_signals}개 | 현재 포지션: {len(positions)}개")

if __name__ == "__main__":
    main()
