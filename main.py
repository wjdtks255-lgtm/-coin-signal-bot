import json
import os
import requests
import numpy as np
from datetime import datetime, timedelta

CACHE_FILE = "tracked_coins.json"
MIN_ACC_TRADE_PRICE = 10_000_000_000   # 24시간 거래대금 100억 이상
MAX_ALLOWABLE_STOP_LOSS_PCT = 10.0     # 최대 허용 손절 폭 10%

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("텔레그램 토큰 또는 챗 ID가 설정되지 않았습니다.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        res.raise_for_status()
    except Exception as e:
        print(f"텔레그램 전송 에러: {e}")


def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=4)


def get_market_names():
    try:
        url = "https://api.upbit.com/v1/market/all"
        res = requests.get(url, timeout=10).json()
        return {item['market']: item['korean_name'] for item in res if item['market'].startswith('KRW-')}
    except Exception as e:
        print(f"마켓 목록 조회 실패: {e}")
        return {}


def round_upbit_tick(price):
    """업비트 KRW 마켓 호가 단위(Tick Size) 정밀 적용"""
    if price >= 2_000_000:
        tick = 1000
    elif price >= 1_000_000:
        tick = 500
    elif price >= 500_000:
        tick = 100
    elif price >= 100_000:
        tick = 50
    elif price >= 10_000:
        tick = 10
    elif price >= 1_000:
        tick = 1
    elif price >= 100:
        tick = 0.1
    elif price >= 10:
        tick = 0.01
    elif price >= 1:
        tick = 0.001
    elif price >= 0.1:
        tick = 0.0001
    elif price >= 0.01:
        tick = 0.00001
    else:
        tick = 0.000001

    return round(round(price / tick) * tick, 8)


def format_price(price):
    if price >= 1000:
        return f"{price:,.0f}"
    elif price >= 100:
        return f"{price:,.1f}"
    elif price >= 10:
        return f"{price:,.2f}"
    elif price >= 1:
        return f"{price:,.3f}"
    elif price >= 0.1:
        return f"{price:,.4f}"
    else:
        return f"{price:,.6f}"


def fetch_candles(market, timeframe_type="minutes", unit=60, count=50):
    """타임프레임별 업비트 캔들 수집"""
    if timeframe_type == "days":
        url = f"https://api.upbit.com/v1/candles/days?market={market}&count={count}"
    else:
        url = f"https://api.upbit.com/v1/candles/minutes/{unit}?market={market}&count={count}"

    try:
        res = requests.get(url, timeout=10).json()
        if isinstance(res, list) and len(res) >= 20:
            res.reverse()
            closes = [c['trade_price'] for c in res]
            highs = [c['high_price'] for c in res]
            lows = [c['low_price'] for c in res]
            volumes = [c['candle_acc_trade_volume'] for c in res]
            return {
                "closes": np.array(closes),
                "highs": np.array(highs),
                "lows": np.array(lows),
                "volumes": np.array(volumes)
            }
    except Exception as e:
        print(f"[{market}] 캔들 수집 에러 ({timeframe_type}/{unit}): {e}")
    return None


def analyze_multi_timeframe(ticker):
    """
    [멀티 타임프레임 종합 분석 함수]
    1. 일봉(1D): 대추세 및 메이저 방향성
    2. 4시간봉(4H): 구조적 지지/저항 및 파동 분석
    3. 15분봉(15M): 단기 변동성 및 진입 모멘텀
    """
    candles_1d = fetch_candles(ticker, timeframe_type="days", count=30)
    candles_4h = fetch_candles(ticker, timeframe_type="minutes", unit=240, count=40)
    candles_15m = fetch_candles(ticker, timeframe_type="minutes", unit=15, count=40)

    if not candles_1d or not candles_4h or not candles_15m:
        return None

    current_price = candles_15m['closes'][-1]
    score = 0

    # 1. [일봉 (1D)] 대추세 검증 (30점 만점)
    closes_1d = candles_1d['closes']
    ma5_1d = np.mean(closes_1d[-5:])
    ma20_1d = np.mean(closes_1d[-20:])
    
    if current_price > ma20_1d:
        score += 15
    if ma5_1d >= ma20_1d:
        score += 15

    # 2. [4시간봉 (4H)] 지지/저항 및 볼린저밴드 (40점 만점)
    closes_4h = candles_4h['closes']
    highs_4h = candles_4h['highs']
    lows_4h = candles_4h['lows']

    ma20_4h = np.mean(closes_4h[-20:])
    std20_4h = np.std(closes_4h[-20:])
    bb_upper_4h = ma20_4h + (2 * std20_4h)
    
    swing_high_4h = np.max(highs_4h[-20:-1])
    swing_low_4h = np.min(lows_4h[-15:])

    if current_price > ma20_4h:
        score += 20
    if current_price >= bb_upper_4h * 0.98:
        score += 20

    # 3. [15분봉 (15M)] 단기 돌파 및 수급 배율 (30점 만점)
    closes_15m = candles_15m['closes']
    volumes_15m = candles_15m['volumes']
    
    ma5_15m = np.mean(closes_15m[-5:])
    ma20_15m = np.mean(closes_15m[-20:])
    vol_avg_15m = np.mean(volumes_15m[-20:-1])
    
    # 15분봉 수급 폭발률 계산 (평균 대비 몇 %)
    vol_ratio_15m = (volumes_15m[-1] / vol_avg_15m * 100) if vol_avg_15m > 0 else 100.0

    if ma5_15m > ma20_15m:
        score += 15
    if vol_ratio_15m >= 180:
        score += 15

    # 4. [피보나치 확장 목표가 산출]
    wave_range = max(swing_high_4h - swing_low_4h, current_price * 0.02)
    fib_1272 = current_price + (wave_range * 0.272)
    fib_1618 = current_price + (wave_range * 0.618)

    return {
        "score": score,
        "current_price": current_price,
        "swing_low_4h": swing_low_4h,
        "swing_high_4h": swing_high_4h,
        "bb_upper_4h": bb_upper_4h,
        "fib_1272": fib_1272,
        "fib_1618": fib_1618,
        "vol_ratio_15m": vol_ratio_15m
    }


def evaluate_and_send_signal(ticker, korean_name, current_price, acc_trade_price):
    if acc_trade_price < MIN_ACC_TRADE_PRICE:
        return

    # 멀티 타임프레임 분석 실행
    mtf = analyze_multi_timeframe(ticker)
    if not mtf or mtf["score"] < 60:
        return

    # --- [가격 및 손절가 계산] ---
    raw_sl = mtf["swing_low_4h"] * 0.995
    stop_loss = round_upbit_tick(raw_sl)
    calculated_stop_loss_pct = ((current_price - stop_loss) / current_price) * 100

    if calculated_stop_loss_pct > MAX_ALLOWABLE_STOP_LOSS_PCT or calculated_stop_loss_pct < 0.8:
        return

    # --- [목표가 계산] ---
    raw_tp1 = max(mtf["bb_upper_4h"], mtf["fib_1272"])
    if raw_tp1 <= current_price:
        raw_tp1 = current_price * 1.025
    target_1 = round_upbit_tick(raw_tp1)

    raw_tp2 = max(mtf["swing_high_4h"], target_1 * 1.02)
    target_2 = round_upbit_tick(raw_tp2)

    raw_tp3 = max(mtf["fib_1618"], target_2 * 1.025)
    target_3 = round_upbit_tick(raw_tp3)

    # --- [손익비 (Risk/Reward) 계산] ---
    rr_tp1 = (target_1 - current_price) / (current_price - stop_loss) if (current_price - stop_loss) > 0 else 0
    rr_tp2 = (target_2 - current_price) / (current_price - stop_loss) if (current_price - stop_loss) > 0 else 0
    rr_tp3 = (target_3 - current_price) / (current_price - stop_loss) if (current_price - stop_loss) > 0 else 0

    # --- [스마트 트래킹 검증] ---
    cache = load_cache()
    now = datetime.now()

    if ticker in cache:
        prev_target_1 = cache[ticker].get("target_1", 0)
        last_alert_time_str = cache[ticker].get("last_alert", "")

        if last_alert_time_str:
            try:
                last_alert_time = datetime.fromisoformat(last_alert_time_str)
                if now - last_alert_time < timedelta(hours=4):
                    return
            except Exception:
                pass

        if current_price < prev_target_1 * 1.015:
            return

    # --- [출력 포맷팅] ---
    curr_str = format_price(current_price)
    tp1_str = format_price(target_1)
    tp2_str = format_price(target_2)
    tp3_str = format_price(target_3)
    sl_str = format_price(stop_loss)

    tp1_pct = ((target_1 - current_price) / current_price) * 100
    tp2_pct = ((target_2 - current_price) / current_price) * 100
    tp3_pct = ((target_3 - current_price) / current_price) * 100

    # 리스크 수준에 따른 경고 표기
    sl_warning = " ⚠️ (손절폭 유의)" if calculated_stop_loss_pct >= 7.0 else ""

    # 업비트 웹/앱 차트 연결 링크
    upbit_url = f"https://upbit.com/exchange?code=CASA.{ticker}"

    message = (
        f"🚀 **[MULTI-TIMEFRAME QUANT SIGNAL]**\n"
        f"────────────────────────\n"
        f"▪ **자산명**: `{korean_name} ({ticker})`\n"
        f"▪ **현재가**: `{curr_str} KRW`\n"
        f"▪ **24H 거래대금**: `{acc_trade_price / 100_000_000:,.1f}억 원`\n"
        f"▪ **15M 수급 강도**: `평균 대비 {mtf['vol_ratio_15m']:.0f}% 유입 🔥`\n"
        f"▪ **MTF 종합 점수**: `{mtf['score']} / 100점 (강한 정배열)`\n\n"
        f"🎯 **MULTI-LEVEL TARGETS (목표가 & 손익비)**\n"
        f"  ├ **TP1 (4H BB/1.272)**: `{tp1_str}원` (+{tp1_pct:.1f}%) | R:R 1:{rr_tp1:.1f}\n"
        f"  ├ **TP2 (4H 전고점)**: `{tp2_str}원` (+{tp2_pct:.1f}%) | R:R 1:{rr_tp2:.1f}\n"
        f"  └ **TP3 (1.618 확장)**: `{tp3_str}원` (+{tp3_pct:.1f}%) | R:R 1:{rr_tp3:.1f}\n\n"
        f"🛡️ **RISK MANAGEMENT (리스크 관리)**\n"
        f"  ├ **4H 지지 손절가 (SL)**: `{sl_str}원` (-{calculated_stop_loss_pct:.1f}%){sl_warning}\n"
        f"  └ **타임프레임**: `1D 대추세 + 4H 지지/저항 + 15M 돌파`\n\n"
        f"📱 [업비트 차트 열기]({upbit_url})\n"
        f"────────────────────────\n"
        f"💡 *Strategy: 1D/4H/15M 멀티 타임프레임 컨플루언스 포착*"
    )

    print(f"🔥 [알림 전송 완료] {korean_name}({ticker}) Score: {mtf['score']}")
    send_telegram_message(message)

    cache[ticker] = {
        "last_price": current_price,
        "target_1": target_1,
        "last_alert": now.isoformat()
    }
    save_cache(cache)


if __name__ == "__main__":
    print("업비트 멀티 타임프레임 종합 분석 스캐너 가동 시작...")
    try:
        market_names = get_market_names()
        markets = list(market_names.keys())

        if not markets:
            print("조회 가능한 KRW 마켓이 없습니다.")
            exit()

        chunk_size = 100
        ticker_data = []

        for i in range(0, len(markets), chunk_size):
            chunk = markets[i:i + chunk_size]
            ticker_url = f"https://api.upbit.com/v1/ticker?markets={','.join(chunk)}"
            res = requests.get(ticker_url, timeout=10).json()
            if isinstance(res, list):
                ticker_data.extend(res)

        for data in ticker_data:
            ticker = data['market']
            korean_name = market_names.get(ticker, ticker)
            current_price = data['trade_price']
            acc_trade_price = data['acc_trade_price_24h']

            evaluate_and_send_signal(
                ticker=ticker,
                korean_name=korean_name,
                current_price=current_price,
                acc_trade_price=acc_trade_price
            )

    except Exception as e:
        print(f"실행 중 에러 발생: {e}")
