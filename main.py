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
MIN_TRADE_PRICE_24H = 5_000_000_000  # 24시간 거래대금 최소 50억 원 이상만 스캔
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

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return closes[-1] * 0.02
    tr_list = []
    for i in range(1, len(closes)):
        h, l, pc = highs[i], lows[i], closes[i-1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)
    return sum(tr_list[-period:]) / period

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

def check_btc_regime_shift(current_regime, state):
    prev_regime = state.get("btc_regime", "NEUTRAL")
    if current_regime != prev_regime:
        emoji_map = {
            "BULLISH": "🚀 (1시간봉 상승세 / 강세장 진입)",
            "BEARISH": "⚠️ (1시간봉 하락세 / 약세장 진입)",
            "NEUTRAL": "⚖️ (1시간봉 횡보장 / 혼조세 전환)"
        }
        msg = (
            f"🚨 <b>[시황 긴급 알림] 비트코인 1시간봉 추세 전환 포착!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>이전 추세</b> : <code>{prev_regime}</code>\n"
            f"• <b>현재 추세</b> : <b>{current_regime}</b> {emoji_map.get(current_regime, '')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱ <code>{now().strftime('%Y-%m-%d %H:%M:%S KST')}</code>"
        )
        telegram(msg)
    state["btc_regime"] = current_regime

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
    atr = calc_atr(highs, lows, closes, 14)
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

    if vol_ratio >= 2.5:
        score += 3
        reasons.append(f"거래량 대폭발 ({vol_ratio:.1f}배)")
    elif vol_ratio >= 1.8:
        score += 2
        reasons.append(f"거래량 증가 ({vol_ratio:.1f}배)")

    if price >= bb_upper:
        score += 2
        reasons.append("볼린저 밴드 상단 돌파")

    min_required_score = 7
    if btc_regime == "NEUTRAL":
        min_required_score = 8
    elif btc_regime == "BEARISH":
        min_required_score = 9

    if score >= min_required_score:
        risk_pct = max((atr / price) * 100 * 1.5, 2.5)
        risk_amount = price * (risk_pct / 100)

        sl = price - risk_amount
        tp1 = price + (risk_amount * 1.5)
        tp2 = price + (risk_amount * 3.0)
        tp3 = price + (risk_amount * 5.0)

        return {
            "price": price, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "tp1_pct": ((tp1 - price) / price) * 100,
            "tp2_pct": ((tp2 - price) / price) * 100,
            "tp3_pct": ((tp3 - price) / price) * 100,
            "sl_pct": ((sl - price) / price) * 100,
            "rsi": rsi, "score": score, "reasons": reasons
        }
    return None

# ==========================================
# 포지션 감시 및 집계 로직 (안전 기본값 추가)
# ==========================================
def monitor_positions(positions, stats):
    if not positions:
        return {}, stats, False

    market_list = [f"KRW-{coin}" for coin in positions.keys()]
    ticker_data = get_market_tickers(market_list)
    updated_positions = {}
    has_changed = False

    for coin, info in positions.items():
        market = f"KRW-{coin}"
        ticker = ticker_data.get(market)
        if not ticker:
            updated_positions[coin] = info
            continue

        cur_price = ticker['price']
        entry = info.get("entry", cur_price)
        sl = info.get("sl", entry * 0.95)
        tp1 = info.get("tp1", entry * 1.05)
        tp2 = info.get("tp2", entry * 1.10)
        tp3 = info.get("tp3", entry * 1.15)
        max_tp_hit = info.get("max_tp_hit", 0)
        upbit_link = f"https://upbit.com/exchange?code=CRIX.UPBIT.KRW-{coin}"

        # 1. TP3 최종 완결 달성 종료
        if cur_price >= tp3:
            stats["tp3"] = stats.get("tp3", 0) + 1
            has_changed = True
            profit_pct = ((cur_price - entry) / entry) * 100
            msg = (
                f"🎯 <b>[코인 현물] TP3 완결 달성 종료!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>종목명</b> : <code>#{coin}</code>\n"
                f"• <b>진입가</b> : ₩{format_price(entry)}\n"
                f"• <b>최종 익절가</b> : <b>₩{format_price(cur_price)}</b> (<b>+{profit_pct:.1f}%</b>)\n"
                f"📈 <a href=\"{upbit_link}\"><b>[업비트 거래소 이동]</b></a>\n"
                f"⏱ <code>{now().strftime('%Y-%m-%d %H:%M:%S KST')}</code>"
            )
            telegram(msg)
            continue

        # 2. 손절가 / 본전 스탑 도달 체크 (종료 처리)
        elif cur_price <= sl:
            pnl_pct = ((cur_price - entry) / entry) * 100
            has_changed = True

            if max_tp_hit == 2:
                stats["tp2"] = stats.get("tp2", 0) + 1
                tp_text = "TP2 달성 후 종료"
            elif max_tp_hit == 1:
                stats["tp1"] = stats.get("tp1", 0) + 1
                tp_text = "TP1 달성 후 종료"
            else:
                stats["sl"] = stats.get("sl", 0) + 1
                tp_text = "초기 손절 종료"

            msg = (
                f"🛑 <b>[코인 현물] 포지션 최종 종료 ({tp_text})</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>종목명</b> : <code>#{coin}</code>\n"
                f"• <b>진입가</b> : ₩{format_price(entry)}\n"
                f"• <b>종료가</b> : ₩{format_price(cur_price)} ({pnl_pct:+.1f}%)\n"
                f"📈 <a href=\"{upbit_link}\"><b>[업비트 거래소 이동]</b></a>\n"
                f"⏱ <code>{now().strftime('%Y-%m-%d %H:%M:%S KST')}</code>"
            )
            telegram(msg)
            continue

        # 3. 진행 중 목표가 도달 처리
        elif cur_price >= tp2 and max_tp_hit < 2:
            has_changed = True
            profit_pct = ((cur_price - entry) / entry) * 100
            info["max_tp_hit"] = 2
            info["sl"] = tp1
            msg = (
                f"🎯 <b>[코인 현물] TP2 목표가 도달! (진행 중)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>종목명</b> : <code>#{coin}</code>\n"
                f"• <b>현재가</b> : ₩{format_price(cur_price)} (+{profit_pct:.1f}%)\n"
                f"🛡 <i>손절가가 TP1(₩{format_price(tp1)})로 상향 조정되었습니다.</i>\n"
                f"📈 <a href=\"{upbit_link}\"><b>[업비트 거래소 이동]</b></a>\n"
                f"⏱ <code>{now().strftime('%Y-%m-%d %H:%M:%S KST')}</code>"
            )
            telegram(msg)

        elif cur_price >= tp1 and max_tp_hit < 1:
            has_changed = True
            profit_pct = ((cur_price - entry) / entry) * 100
            info["max_tp_hit"] = 1
            info["sl"] = entry
            msg = (
                f"🎯 <b>[코인 현물] TP1 목표가 도달! (진행 중)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>종목명</b> : <code>#{coin}</code>\n"
                f"• <b>현재가</b> : ₩{format_price(cur_price)} (+{profit_pct:.1f}%)\n"
                f"🛡 <i>손절가가 진입가(본전 ₩{format_price(entry)})로 상향 조정되었습니다.</i>\n"
                f"📈 <a href=\"{upbit_link}\"><b>[업비트 거래소 이동]</b></a>\n"
                f"⏱ <code>{now().strftime('%Y-%m-%d %H:%M:%S KST')}</code>"
            )
            telegram(msg)

        info["entry"] = entry
        info["sl"] = sl
        info["tp1"] = tp1
        info["tp2"] = tp2
        info["tp3"] = tp3
        updated_positions[coin] = info

    return updated_positions, stats, has_changed

# ==========================================
# 성과 통계 브리핑
# ==========================================
def send_status_briefing(positions, stats):
    market_list = [f"KRW-{coin}" for coin in positions.keys()]
    ticker_data = get_market_tickers(market_list) if market_list else {}

    tp3_cnt = stats.get("tp3", 0)
    tp2_cnt = stats.get("tp2", 0)
    tp1_cnt = stats.get("tp1", 0)
    sl_cnt = stats.get("sl", 0)

    total_closed = tp3_cnt + tp2_cnt + tp1_cnt + sl_cnt
    wins = tp3_cnt + tp2_cnt + tp1_cnt
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0

    msg = (
        f"📊 <b>시그널 누적 성과 통계</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>총 종료 시그널</b> : <b>{total_closed}건</b>\n\n"
        f"🎯 <b>TP3 종료</b> : {tp3_cnt}건\n"
        f"🎯 <b>TP2 종료</b> : {tp2_cnt}건\n"
        f"🎯 <b>TP1 종료</b> : {tp1_cnt}건\n"
        f"🛑 <b>손절 종료</b> : {sl_cnt}건\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>최종 승률</b> : <b>{win_rate:.1f}%</b>\n"
        f" (승리 {wins}건 · 손절 {sl_cnt}건)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>현재 보유 슬롯</b> : <b>{len(positions)} / {MAX_POSITIONS}개</b>\n"
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
            status_str = f"TP{max_tp} 진행 중" if max_tp > 0 else "TP1 진입 중"
            upbit_link = f"https://upbit.com/exchange?code=CRIX.UPBIT.KRW-{coin}"

            msg += (
                f"{emoji} <a href=\"{upbit_link}\"><b>#{coin}</b></a>\n"
                f"  • 진입: ₩{format_price(entry)} | 현재: ₩{format_price(cur_price)}\n"
                f"  • 수익률: <b>{sign}{profit_pct:.1f}%</b> ({status_str})\n"
            )
    else:
        msg += "<i>현재 보유 중인 포지션이 없습니다.</i>\n"

    msg += (
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ <code>{now().strftime('%Y-%m-%d %H:%M:%S KST')}</code>"
    )
    telegram(msg)

def main():
    print("=" * 55)
    print("🚀 UPBIT SPOT QUANT SCANNER V12.1 (SAFE STATS)...")
    print("=" * 55)

    default_state = {
        "positions": {},
        "btc_regime": "NEUTRAL",
        "stats": {"tp3": 0, "tp2": 0, "tp1": 0, "sl": 0}
    }
    state = load_json(STATE_FILE, default_state)
    stats = state.get("stats", {"tp3": 0, "tp2": 0, "tp1": 0, "sl": 0})
    old_positions = state.get("positions", {}).copy()
    
    btc_regime = get_btc_regime()
    check_btc_regime_shift(btc_regime, state)

    # 1. 보유 포지션 감시 및 종료 처리
    positions, stats, pos_changed = monitor_positions(old_positions, stats)
    
    # 2. 신규 시그널 스캔
    new_signal_caught = False
    if len(positions) < MAX_POSITIONS:
        markets = get_krw_markets()
        ticker_data = get_market_tickers(markets)

        for market in markets:
            coin_symbol = market.replace("KRW-", "")
            if coin_symbol in positions:
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

                msg = (
                    f"🟢 <b>[코인 현물] 고승률 매수 시그널 (점수: {result['score']}점)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"• <b>종목명</b> : <code>#{coin_symbol}</code> (KRW 마켓)\n"
                    f"• <b>시황 추세</b> : <code>BTC 1시간봉 {btc_regime}</code>\n"
                    f"• <b>RSI (14)</b> : <code>{result['rsi']:.1f}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 <b>진입가</b> : <code>₩{format_price(price)}</code>\n"
                    f"🎯 <b>1차 목표가 (TP1)</b> : <code>₩{format_price(tp1)}</code> (<b>+{result['tp1_pct']:.1f}%</b>)\n"
                    f"🎯 <b>2차 목표가 (TP2)</b> : <code>₩{format_price(tp2)}</code> (<b>+{result['tp2_pct']:.1f}%</b>)\n"
                    f"🎯 <b>3차 목표가 (TP3)</b> : <code>₩{format_price(tp3)}</code> (<b>+{result['tp3_pct']:.1f}%</b>)\n"
                    f"🛡 <b>손절가 (SL)</b> : <code>₩{format_price(sl)}</code> ({result['sl_pct']:.1f}%)\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚙️ <b>포착 조건</b> : {', '.join(result['reasons'])}\n"
                    f"📈 <a href=\"{upbit_link}\"><b>[업비트 거래소 바로가기]</b></a>\n"
                    f"⏱ <code>{now().strftime('%Y-%m-%d %H:%M:%S KST')}</code>"
                )

                telegram(msg)

                positions[coin_symbol] = {
                    "entry": price, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
                    "max_tp_hit": 0, "time": now().strftime("%Y-%m-%d %H:%M:%S")
                }
                time.sleep(1)

                if len(positions) >= MAX_POSITIONS:
                    break

    state["positions"] = positions
    state["stats"] = stats
    save_json(STATE_FILE, state)

    # 3. 변동 발생 시에만 리포트 전송
    if pos_changed or new_signal_caught or (set(old_positions.keys()) != set(positions.keys())):
        send_status_briefing(positions, stats)

if __name__ == "__main__":
    main()
