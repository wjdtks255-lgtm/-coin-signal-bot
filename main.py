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
MAX_POSITIONS = 8  # 동시 보유 최대 슬롯 8개
MIN_TRADE_PRICE_24H = 10_000_000_000  # 24시간 거래대금 최소 100억 원 이상

# 스캔 제외 대상 블랙리스트
EXCLUDED_COINS = {"USDT", "BTT"}

KST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json"
}

def now():
    return datetime.now(KST)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("positions", {})
                    data.setdefault("history", [])
                    data.setdefault("btc_regime", "NEUTRAL")
                    return data
        except Exception:
            pass
    return {"positions": {}, "history": [], "btc_regime": "NEUTRAL"}

def save_state(st):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)

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
# 지표 계산 함수
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

def calc_bollinger_bands(closes, period=20, std_dev=2):
    if len(closes) < period:
        return closes[-1], closes[-1]
    slice_closes = closes[-period:]
    sma = sum(slice_closes) / period
    variance = sum((x - sma) ** 2 for x in slice_closes) / period
    std = variance ** 0.5
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    return upper, lower

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
            volumes = [float(x['candle_acc_trade_volume']) for x in data]
            return closes, highs, lows, volumes
    except Exception:
        pass
    return [], [], [], []

def get_market_tickers(markets):
    try:
        url = f"https://api.upbit.com/v1/ticker?markets={','.join(markets)}"
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            data = res.json()
            result = {}
            for item in data:
                result[item['market']] = {
                    'price': float(item['trade_price']),
                    'acc_trade_price_24h': float(item['acc_trade_price_24h'])
                }
            return result
    except Exception:
        pass
    return {}

def get_btc_regime():
    closes, _, _, _ = fetch_candles("KRW-BTC", unit="60", count=60)
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

# ==========================================
# 분석 및 시그널 산출
# ==========================================
def analyze_market(closes, highs, lows, volumes, btc_regime):
    if len(closes) < 50 or len(volumes) < 20:
        return None

    ema20_list = calc_ema(closes, 20)
    ema50_list = calc_ema(closes, 50)
    
    price = closes[-1]
    ema20 = ema20_list[-1]
    ema50 = ema50_list[-1]
    
    rsi = calc_rsi(closes, 14)
    prev_rsi = calc_rsi(closes[:-1], 14)
    bb_upper, _ = calc_bollinger_bands(closes, 20, 2)

    cur_volume = volumes[-1]
    avg_volume = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else sum(volumes) / len(volumes)
    vol_ratio = (cur_volume / avg_volume) if avg_volume > 0 else 1.0

    score = 0
    reasons = []

    if price > ema20:
        score += 2
        reasons.append("EMA20 상회")
    if ema20 > ema50:
        score += 2
        reasons.append("EMA20 > EMA50 정배열")

    if prev_rsi <= 48 and rsi > prev_rsi:
        score += 2
        reasons.append(f"RSI 반등 ({rsi:.1f})")
    elif 50 <= rsi <= 68:
        score += 2
        reasons.append(f"RSI 모멘텀 ({rsi:.1f})")

    if vol_ratio >= 3.0:
        score += 3
        reasons.append(f"거래량 대폭발 ({vol_ratio:.1f}배)")
    elif vol_ratio >= 2.0:
        score += 2
        reasons.append(f"거래량 증가 ({vol_ratio:.1f}배)")

    if price >= bb_upper:
        score += 2
        reasons.append("볼린저 밴드 상단 돌파")

    if score >= 8:
        risk_pct = 5.0
        risk_amount = price * (risk_pct / 100)

        sl = price - risk_amount
        tp1 = price + (risk_amount * 1.2)
        tp2 = price + (risk_amount * 2.2)

        is_high_momentum = (vol_ratio >= 3.0 and score >= 10)
        tp3 = price + (risk_amount * 3.5) if is_high_momentum else None

        return {
            "price": price, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "is_high_momentum": is_high_momentum,
            "tp1_pct": ((tp1 - price) / price) * 100,
            "tp2_pct": ((tp2 - price) / price) * 100,
            "tp3_pct": (((tp3 - price) / price) * 100) if tp3 else None,
            "sl_pct": -risk_pct,
            "rsi": rsi, "score": score, "reasons": reasons
        }
    return None

# ==========================================
# 포지션 종료 및 승무패 기록 처리
# ==========================================
def close_position(st, coin, info, outcome, price):
    record = {
        "symbol": coin,
        "outcome": outcome, 
        "entry": info["entry"],
        "exit": price,
        "time": now()
    }
    st["history"].append(record)
    st["history"] = st["history"][-1000:]
    
    if coin in st["positions"]:
        del st["positions"][coin]
    
    labels = {
        "tp3": "TP3 목표 도달 (승)",
        "tp2_end": "TP2 이후 종료 (승)",
        "trailing": "트레일링 익절 (승)",
        "tp1_end": "1차 본전 종료 (무)",
        "initial_sl": "초기 손절 종료 (패)"
    }
    
    pnl_pct = ((price - info["entry"]) / info["entry"]) * 100
    upbit_link = f"https://upbit.com/exchange?code=CRIX.UPBIT.KRW-{coin}"
    
    msg = (
        f"📊 <b>[코인 현물] 시그널 종료</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>종목명</b> : <code>#{coin}</code>\n"
        f"• <b>결과 분류</b> : <b>{labels.get(outcome, outcome)}</b>\n"
        f"• <b>진입가</b> : ₩{format_price(info['entry'])}\n"
        f"• <b>종료가</b> : ₩{format_price(price)} ({pnl_pct:+.2f}%)\n"
        f"📈 <a href=\"{upbit_link}\"><b>[업비트 거래소 이동]</b></a>\n"
        f"⏱ <code>{now_kst_str()}</code>"
    )
    telegram(msg)
    save_state(st)

def now_kst_str():
    return datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')

# ==========================================
# 포지션 감시 로직 (손절가 우선 검사 패치)
# ==========================================
def monitor_positions(st):
    positions = st["positions"]
    if not positions:
        return False

    market_list = [f"KRW-{coin}" for coin in positions.keys()]
    ticker_data = get_market_tickers(market_list)
    has_changed = False

    for coin, info in list(positions.items()):
        market = f"KRW-{coin}"
        ticker = ticker_data.get(market)
        if not ticker:
            continue

        cur_price = ticker['price']
        entry = info.get("entry", cur_price)
        sl = info.get("sl", entry * 0.95)
        tp1 = info.get("tp1", entry * 1.06)
        tp2 = info.get("tp2", entry * 1.11)
        tp3 = info.get("tp3", None)
        max_tp_hit = info.get("max_tp_hit", 0)
        
        highest_price = max(info.get("highest_price", entry), cur_price)
        info["highest_price"] = highest_price

        # 💡 1. 손절가 도달 체크 (최우선 순위: 손실 방어)
        if cur_price <= sl:
            if max_tp_hit >= 1:
                close_position(st, coin, info, "tp1_end", cur_price) # 무 (1차 본전 종료)
            else:
                close_position(st, coin, info, "initial_sl", cur_price) # 패 (초기 손절)
            has_changed = True
            continue

        # 💡 2. TP3 최종 완결 (승)
        if tp3 and cur_price >= tp3:
            close_position(st, coin, info, "tp3", cur_price)
            has_changed = True
            continue

        # 💡 3. TP2 완결 (승)
        if cur_price >= tp2 and (not tp3 or max_tp_hit == 2):
            close_position(st, coin, info, "tp2_end", cur_price)
            has_changed = True
            continue

        # 💡 4. 트레일링 스탑 체크 (반드시 진입가보다 높은 수익 상태일 때만 익절 인정!)
        peak_profit_pct = ((highest_price - entry) / entry) * 100
        if peak_profit_pct >= 3.0 and cur_price > entry and cur_price <= highest_price * (1 - 0.015):
            close_position(st, coin, info, "trailing", cur_price)
            has_changed = True
            continue

        # 5. TP1 도달 처리
        elif cur_price >= tp1 and max_tp_hit < 1:
            info["max_tp_hit"] = 1
            info["sl"] = entry  # 본전 상향
            has_changed = True
            upbit_link = f"https://upbit.com/exchange?code=CRIX.UPBIT.KRW-{coin}"
            profit_pct = ((cur_price - entry) / entry) * 100
            msg = (
                f"🎯 <b>[코인 현물] TP1 목표가 도달! (진행 중)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>종목명</b> : <code>#{coin}</code>\n"
                f"• <b>현재가</b> : ₩{format_price(cur_price)} (+{profit_pct:.1f}%)\n"
                f"🛡 <i>손절가가 진입가(본전 ₩{format_price(entry)})로 상향 조정되었습니다.</i>\n"
                f"📈 <a href=\"{upbit_link}\"><b>[업비트 거래소 이동]</b></a>\n"
                f"⏱ <code>{now_kst_str()}</code>"
            )
            telegram(msg)

        positions[coin] = info

    return has_changed

# ==========================================
# 승무패 통계 리포트 생성 함수
# ==========================================
def send_status_briefing(st):
    positions = st["positions"]
    history = st["history"]
    
    market_list = [f"KRW-{coin}" for coin in positions.keys()]
    ticker_data = get_market_tickers(market_list) if market_list else {}

    counts = {
        "tp3": sum(1 for x in history if x.get("outcome") == "tp3"),
        "tp2_end": sum(1 for x in history if x.get("outcome") == "tp2_end"),
        "trailing": sum(1 for x in history if x.get("outcome") == "trailing"),
        "tp1_end": sum(1 for x in history if x.get("outcome") == "tp1_end"),
        "initial_sl": sum(1 for x in history if x.get("outcome") == "initial_sl")
    }
    total = len(history)
    
    wins = counts["tp3"] + counts["tp2_end"] + counts["trailing"]
    draws = counts["tp1_end"]
    losses = counts["initial_sl"]
    
    decisive_total = total - draws
    rate = 100 * wins / decisive_total if decisive_total > 0 else 0.0

    msg = (
        f"📊 <b>목표 도달 후 추적 종료 분류 통계 (V15.8 패치완료)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"총 종료 시그널: <b>{total}건</b>\n\n"
        f"🎯 TP3 종료 (승): {counts['tp3']}건\n"
        f"🎯 TP2 이후 종료 (승): {counts['tp2_end']}건\n"
        f"📈 트레일링 익절 (승): {counts['trailing']}건\n"
        f"🤝 1차 본전 종료 (무): {counts['tp1_end']}건\n"
        f"🛑 초기 손절 종료 (패): {counts['initial_sl']}건\n\n"
        f"🏆 <b>승률 (승/패 기준): {rate:.1f}%</b>\n"
        f" (승 {wins}건 / 패 {losses}건 | 무 {draws}건 제외)\n"
        f"• 현재 보유 슬롯 : <b>{len(positions)} / {MAX_POSITIONS}개</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
    )

    if positions:
        msg += "<b>[현재 진행 중 포지션]</b>\n"
        for coin, info in positions.items():
            market = f"KRW-{coin}"
            ticker = ticker_data.get(market, {})
            cur_price = ticker.get('price', info.get("entry", 0))
            entry = info.get("entry", cur_price)
            profit_pct = ((cur_price - entry) / entry) * 100 if entry > 0 else 0.0
            sign = "+" if profit_pct > 0 else ""
            emoji = "🟢" if profit_pct >= 0 else "🔴"
            max_tp = info.get("max_tp_hit", 0)
            status_str = f"TP{max_tp} 진행 중" if max_tp > 0 else "진행 중"
            upbit_link = f"https://upbit.com/exchange?code=CRIX.UPBIT.KRW-{coin}"

            msg += (
                f"{emoji} <a href=\"{upbit_link}\"><b>#{coin}</b></a>\n"
                f"  • 진입: ₩{format_price(entry)} | 현재: ₩{format_price(cur_price)}\n"
                f"  • 수익률: <b>{sign}{profit_pct:.2f}%</b> ({status_str})\n"
            )
    else:
        msg += "<i>현재 보유 중인 포지션이 없습니다.</i>\n"

    msg += (
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ <code>{now_kst_str()}</code>\n"
        f"<i>※ 실현 계좌 수익률이 아닌 모의 시그널 목표 도달·종료 기록입니다.</i>"
    )
    telegram(msg)

def main():
    print("=" * 55)
    print("🚀 UPBIT SPOT QUANT SCANNER V15.8 (SL BUGFIX)...")
    print("=" * 55)

    st = load_state()
    btc_regime = get_btc_regime()
    st["btc_regime"] = btc_regime

    pos_changed = monitor_positions(st)
    
    new_signal_caught = False
    if len(st["positions"]) < MAX_POSITIONS:
        markets = get_krw_markets()
        ticker_data = get_market_tickers(markets)
        markets = sorted(markets, key=lambda m: ticker_data.get(m, {}).get('acc_trade_price_24h', 0), reverse=True)

        for market in markets:
            coin_symbol = market.replace("KRW-", "")
            
            if coin_symbol in EXCLUDED_COINS or coin_symbol in st["positions"]:
                continue

            ticker = ticker_data.get(market)
            if not ticker or ticker['acc_trade_price_24h'] < MIN_TRADE_PRICE_24H:
                continue

            closes, highs, lows, volumes = fetch_candles(market, unit="15")
            if not closes:
                continue

            result = analyze_market(closes, highs, lows, volumes, btc_regime)
            if result:
                new_signal_caught = True
                price = result["price"]
                sl = result["sl"]
                tp1 = result["tp1"]
                tp2 = result["tp2"]
                tp3 = result["tp3"]

                upbit_link = f"https://upbit.com/exchange?code=CRIX.UPBIT.KRW-{coin_symbol}"
                target_type_str = "🔥 고승률 주도주 (빠른 익절)" if result["is_high_momentum"] else "⚖️ 일반 고승률 타겟"

                msg = (
                    f"🟢 <b>[코인 현물] 고승률 매수 시그널 (점수: {result['score']}점)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"• <b>종목명</b> : <code>#{coin_symbol}</code> (KRW 마켓)\n"
                    f"• <b>시황 추세</b> : <code>BTC 1시간봉 {btc_regime}</code>\n"
                    f"• <b>목표 구조</b> : <b>{target_type_str}</b>\n"
                    f"• <b>RSI (14)</b> : <code>{result['rsi']:.1f}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 <b>진입가</b> : <code>₩{format_price(price)}</code>\n"
                    f"🎯 <b>1차 목표가 (TP1)</b> : <code>₩{format_price(tp1)}</code> (<b>+{result['tp1_pct']:.1f}%</b>)\n"
                    f"🎯 <b>2차 목표가 (TP2)</b> : <code>₩{format_price(tp2)}</code> (<b>+{result['tp2_pct']:.1f}%</b>)\n"
                )

                if tp3:
                    msg += f"🎯 <b>3차 목표가 (TP3)</b> : <code>₩{format_price(tp3)}</code> (<b>+{result['tp3_pct']:.1f}%</b>)\n"

                msg += (
                    f"🛡 <b>손절가 (SL)</b> : <code>₩{format_price(sl)}</code> (-5.0%)\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚙️ <b>포착 조건</b> : {', '.join(result['reasons'])}\n"
                    f"📈 <a href=\"{upbit_link}\"><b>[업비트 거래소 바로가기]</b></a>\n"
                    f"⏱ <code>{now_kst_str()}</code>"
                )

                telegram(msg)

                st["positions"][coin_symbol] = {
                    "entry": price, "sl": sl, "tp1": tp1, "tp2": tp2,
