import json
import os
import time
import requests
import numpy as np

from datetime import datetime, timedelta, timezone


# ============================================================
# GOLDEN RULE
# ============================================================
# 목표:
# 1. 신호 개수보다 신호 품질 우선
# 2. 추격매수 방지
# 3. 거래량만 증가한 가짜 돌파 방지
# 4. 손절폭이 지나치게 큰 종목 제외
# 5. TP1 기대수익 대비 위험이 충분한 경우만 신호
# 6. 동일 종목 반복 신호 방지
# 7. 진행 중 캔들이 아닌 완성 캔들 기준으로 판단
# ============================================================


# ============================================================
# SETTINGS
# ============================================================

CACHE_FILE = "tracked_coins.json"

UPBIT_BASE_URL = "https://api.upbit.com/v1"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# ------------------------------------------------------------
# 유동성 필터
# ------------------------------------------------------------

MIN_24H_TRADE_VALUE = 10_000_000_000       # 100억
MAX_SCAN_COINS = 80                         # 거래대금 상위 최대 80개


# ------------------------------------------------------------
# 신호 품질
# ------------------------------------------------------------

MIN_SCORE = 75

MIN_VOLUME_RATIO = 180                     # 평균 대비 180%
STRONG_VOLUME_RATIO = 300

MIN_BODY_RATIO = 0.55                       # 캔들 몸통 비율
MAX_ENTRY_DISTANCE_FROM_EMA20 = 4.0         # EMA20 대비 최대 4% 추격


# ------------------------------------------------------------
# 리스크
# ------------------------------------------------------------

MIN_STOP_LOSS_PCT = 1.0
MAX_STOP_LOSS_PCT = 7.0

MIN_RR_TP1 = 1.5
MIN_RR_TP2 = 2.0

MAX_TAKE_PROFIT_1_PCT = 12.0


# ------------------------------------------------------------
# 재알림
# ------------------------------------------------------------

SIGNAL_COOLDOWN_HOURS = 6

MIN_NEW_SIGNAL_PRICE_DISTANCE = 2.0


# ------------------------------------------------------------
# BTC 시장 필터
# ------------------------------------------------------------

BTC_15M_CRASH_PCT = -1.5


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Upbit-Spot-Signal-Bot/2.0"
})


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(text):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram 환경변수가 없습니다.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }

    try:

        response = session.post(
            url,
            json=payload,
            timeout=15
        )

        response.raise_for_status()

        return True

    except Exception as e:

        print(f"Telegram 전송 실패: {e}")

        return False


# ============================================================
# CACHE
# ============================================================

def load_cache():

    if not os.path.exists(CACHE_FILE):
        return {}

    try:

        with open(
            CACHE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return {}


def save_cache(cache):

    try:

        with open(
            CACHE_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                cache,
                f,
                ensure_ascii=False,
                indent=4
            )

    except Exception as e:

        print(f"캐시 저장 실패: {e}")


# ============================================================
# PRICE FORMAT
# ============================================================

def round_upbit_tick(price):

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

    return round(
        round(price / tick) * tick,
        8
    )


def format_price(price):

    if price >= 1000:
        return f"{price:,.0f}"

    if price >= 100:
        return f"{price:,.1f}"

    if price >= 10:
        return f"{price:,.2f}"

    if price >= 1:
        return f"{price:,.3f}"

    if price >= 0.1:
        return f"{price:,.4f}"

    return f"{price:,.6f}"


# ============================================================
# UPBIT API
# ============================================================

def api_get(endpoint, params=None):

    try:

        response = session.get(
            f"{UPBIT_BASE_URL}{endpoint}",
            params=params,
            timeout=15
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:

        print(
            f"API ERROR: {endpoint} / {e}"
        )

        return None


# ============================================================
# MARKET LIST
# ============================================================

def get_market_names():

    data = api_get(
        "/market/all",
        {
            "is_details": "true"
        }
    )

    if not isinstance(data, list):
        return {}

    markets = {}

    for item in data:

        market = item.get("market")

        if not market:
            continue

        if not market.startswith("KRW-"):
            continue

        # 유의/주의 종목은 보수적으로 제외
        event = item.get("market_event", {})

        if event.get("warning") is True:
            continue

        if event.get("caution"):
            continue

        markets[market] = item.get(
            "korean_name",
            market
        )

    return markets


# ============================================================
# TICKER
# ============================================================

def get_all_tickers(markets):

    result = []

    chunk_size = 100

    for i in range(
        0,
        len(markets),
        chunk_size
    ):

        chunk = markets[i:i + chunk_size]

        data = api_get(
            "/ticker",
            {
                "markets": ",".join(chunk)
            }
        )

        if isinstance(data, list):
            result.extend(data)

        time.sleep(0.12)

    return result


# ============================================================
# CANDLE
# ============================================================

def fetch_candles(
    market,
    unit,
    count
):

    data = api_get(
        f"/candles/minutes/{unit}",
        {
            "market": market,
            "count": count
        }
    )

    if not isinstance(data, list):
        return None

    if len(data) < 30:
        return None

    data = list(reversed(data))

    return data


# ============================================================
# INDICATORS
# ============================================================

def ema(values, period):

    values = np.asarray(
        values,
        dtype=float
    )

    if len(values) < period:
        return None

    alpha = 2 / (period + 1)

    result = np.zeros(
        len(values),
        dtype=float
    )

    result[0] = values[0]

    for i in range(1, len(values)):

        result[i] = (
            alpha * values[i]
            + (1 - alpha) * result[i - 1]
        )

    return result


def atr(highs, lows, closes, period=14):

    highs = np.asarray(highs)
    lows = np.asarray(lows)
    closes = np.asarray(closes)

    if len(closes) < period + 1:
        return None

    previous_close = closes[:-1]

    tr1 = highs[1:] - lows[1:]

    tr2 = np.abs(
        highs[1:] - previous_close
    )

    tr3 = np.abs(
        lows[1:] - previous_close
    )

    tr = np.maximum(
        tr1,
        np.maximum(tr2, tr3)
    )

    return np.mean(
        tr[-period:]
    )


# ============================================================
# CANDLE QUALITY
# ============================================================

def candle_quality(candle):

    open_price = candle["opening_price"]
    high = candle["high_price"]
    low = candle["low_price"]
    close = candle["trade_price"]

    candle_range = high - low

    if candle_range <= 0:
        return 0, 0

    body = abs(close - open_price)

    body_ratio = body / candle_range

    close_position = (
        close - low
    ) / candle_range

    return body_ratio, close_position


# ============================================================
# BTC MARKET FILTER
# ============================================================

def check_btc_market():

    btc_15m = fetch_candles(
        "KRW-BTC",
        15,
        80
    )

    btc_1h = fetch_candles(
        "KRW-BTC",
        60,
        100
    )

    btc_4h = fetch_candles(
        "KRW-BTC",
        240,
        100
    )

    if not btc_15m or not btc_1h or not btc_4h:

        return False, "BTC 데이터 부족"

    # 마지막 캔들은 진행 중일 수 있으므로 제외
    c15 = btc_15m[:-1]
    c1h = btc_1h[:-1]
    c4h = btc_4h[:-1]

    close_15 = np.array([
        x["trade_price"]
        for x in c15
    ])

    close_1h = np.array([
        x["trade_price"]
        for x in c1h
    ])

    close_4h = np.array([
        x["trade_price"]
        for x in c4h
    ])

    ema20_1h = ema(
        close_1h,
        20
    )

    ema50_1h = ema(
        close_1h,
        50
    )

    ema20_4h = ema(
        close_4h,
        20
    )

    current_15 = close_15[-1]
    previous_15 = close_15[-2]

    change_15 = (
        (current_15 - previous_15)
        / previous_15
    ) * 100

    score = 0

    if close_4h[-1] > ema20_4h[-1]:
        score += 35

    if ema20_1h[-1] > ema50_1h[-1]:
        score += 35

    if close_1h[-1] > ema20_1h[-1]:
        score += 20

    if change_15 > BTC_15M_CRASH_PCT:
        score += 10

    # BTC 급락은 무조건 차단
    if change_15 <= BTC_15M_CRASH_PCT:

        return False, (
            f"BTC 15M 급락 {change_15:.2f}%"
        )

    # 장기/중기 구조 모두 무너지면 알트 매수 금지
    if (
        close_4h[-1] < ema20_4h[-1]
        and ema20_1h[-1] < ema50_1h[-1]
    ):

        return False, "BTC 중기 추세 약세"

    # 최소한 50점 이상인 경우만 허용
    if score < 50:

        return False, (
            f"BTC 시장 점수 부족 {score}"
        )

    return True, (
        f"BTC 정상 / Score {score}"
    )


# ============================================================
# COIN ANALYSIS
# ============================================================

def analyze_coin(
    ticker,
    current_price
):

    candles_1d = fetch_candles(
        ticker,
        240,
        100
    )

    # 4H 대신 240분봉 사용
    candles_4h = candles_1d

    candles_1h = fetch_candles(
        ticker,
        60,
        120
    )

    candles_15m = fetch_candles(
        ticker,
        15,
        120
    )

    candles_day = api_get(
        "/candles/days",
        {
            "market": ticker,
            "count": 60
        }
    )

    if not all([
        candles_day,
        candles_4h,
        candles_1h,
        candles_15m
    ]):

        return None

    if (
        len(candles_day) < 30
        or len(candles_4h) < 60
        or len(candles_1h) < 60
        or len(candles_15m) < 60
    ):

        return None

    # 진행 중 캔들 제거
    day = candles_day[:-1]
    h4 = candles_4h[:-1]
    h1 = candles_1h[:-1]
    m15 = candles_15m[:-1]

    # --------------------------------------------------------
    # DATA
    # --------------------------------------------------------

    d_close = np.array([
        x["trade_price"]
        for x in day
    ])

    h4_close = np.array([
        x["trade_price"]
        for x in h4
    ])

    h4_high = np.array([
        x["high_price"]
        for x in h4
    ])

    h4_low = np.array([
        x["low_price"]
        for x in h4
    ])

    h1_close = np.array([
        x["trade_price"]
        for x in h1
    ])

    m15_close = np.array([
        x["trade_price"]
        for x in m15
    ])

    m15_volume = np.array([
        x["candle_acc_trade_volume"]
        for x in m15
    ])

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    d_ema20 = ema(
        d_close,
        20
    )

    d_ema50 = ema(
        d_close,
        50
    )

    h4_ema20 = ema(
        h4_close,
        20
    )

    h4_ema50 = ema(
        h4_close,
        50
    )

    h1_ema20 = ema(
        h1_close,
        20
    )

    h1_ema50 = ema(
        h1_close,
        50
    )

    m15_ema20 = ema(
        m15_close,
        20
    )

    if any(
        x is None
        for x in [
            d_ema20,
            d_ema50,
            h4_ema20,
            h4_ema50,
            h1_ema20,
            h1_ema50,
            m15_ema20
        ]
    ):

        return None

    # ========================================================
    # SCORE
    # ========================================================

    score = 0

    reasons = []

    # ========================================================
    # 1D TREND - 25점
    # ========================================================

    d_price = d_close[-1]

    if d_price > d_ema20[-1]:

        score += 10
        reasons.append("1D EMA20 위")

    else:

        return None

    if d_ema20[-1] > d_ema50[-1]:

        score += 10
        reasons.append("1D EMA20 > EMA50")

    else:

        return None

    # EMA20 상승 여부
    if d_ema20[-1] > d_ema20[-4]:

        score += 5
        reasons.append("1D 상승 추세")

    # ========================================================
    # 4H TREND - 25점
    # ========================================================

    h4_price = h4_close[-1]

    if h4_price > h4_ema20[-1]:

        score += 10
        reasons.append("4H EMA20 위")

    else:

        return None

    if h4_ema20[-1] > h4_ema50[-1]:

        score += 10
        reasons.append("4H EMA20 > EMA50")

    else:

        return None

    if h4_ema20[-1] > h4_ema20[-4]:

        score += 5
        reasons.append("4H 상승 추세")

    # ========================================================
    # 1H TREND - 15점
    # ========================================================

    h1_price = h1_close[-1]

    if h1_price > h1_ema20[-1]:

        score += 5
        reasons.append("1H EMA20 위")

    else:

        return None

    if h1_ema20[-1] > h1_ema50[-1]:

        score += 5
        reasons.append("1H EMA20 > EMA50")

    if h1_ema20[-1] > h1_ema20[-4]:

        score += 5
        reasons.append("1H 상승")

    # ========================================================
    # 15M ENTRY - 35점
    # ========================================================

    m15_price = m15_close[-1]

    # EMA20 위
    if m15_price > m15_ema20[-1]:

        score += 5
        reasons.append("15M EMA20 위")

    else:

        return None

    # EMA20 상승
    if m15_ema20[-1] > m15_ema20[-4]:

        score += 5
        reasons.append("15M EMA 상승")

    # --------------------------------------------------------
    # 거래량
    # --------------------------------------------------------

    volume_avg = np.mean(
        m15_volume[-21:-1]
    )

    if volume_avg <= 0:
        return None

    volume_ratio = (
        m15_volume[-1]
        / volume_avg
    ) * 100

    if volume_ratio >= MIN_VOLUME_RATIO:

        score += 10
        reasons.append(
            f"거래량 {volume_ratio:.0f}%"
        )

    else:

        return None

    # --------------------------------------------------------
    # 캔들 방향
    # --------------------------------------------------------

    last_candle = m15[-1]

    body_ratio, close_position = candle_quality(
        last_candle
    )

    if (
        last_candle["trade_price"]
        > last_candle["opening_price"]
        and body_ratio >= MIN_BODY_RATIO
        and close_position >= 0.70
    ):

        score += 10
        reasons.append("15M 강한 양봉")

    else:

        return None

    # --------------------------------------------------------
    # 최근 고점 돌파
    # --------------------------------------------------------

    recent_high = np.max(
        m15_close[-21:-1]
    )

    breakout = (
        m15_price >= recent_high
    )

    if breakout:

        score += 5
        reasons.append("15M 고점 돌파")

    # ========================================================
    # 추격매수 방지
    # ========================================================

    distance_from_ema20 = (
        (m15_price - m15_ema20[-1])
        / m15_ema20[-1]
    ) * 100

    if distance_from_ema20 > MAX_ENTRY_DISTANCE_FROM_EMA20:

        return None

    # ========================================================
    # 최근 급등 추격 방지
    # ========================================================

    change_4h = (
        (h4_price - h4_close[-5])
        / h4_close[-5]
    ) * 100

    # 4시간 동안 이미 12% 이상 급등했다면 추격 방지
    if change_4h >= 12:

        return None

    # ========================================================
    # SCORE MINIMUM
    # ========================================================

    if score < MIN_SCORE:

        return None

    # ========================================================
    # STOP LOSS
    # ========================================================

    recent_swing_low = np.min(
        h4_low[-12:-1]
    )

    atr_4h = atr(
        h4_high,
        h4_low,
        h4_close,
        14
    )

    if atr_4h is None:

        return None

    # 스윙 저점보다 약간 아래
    raw_stop = (
        recent_swing_low
        - atr_4h * 0.15
    )

    stop_loss = round_upbit_tick(
        raw_stop
    )

    if stop_loss >= current_price:

        return None

    stop_loss_pct = (
        (current_price - stop_loss)
        / current_price
    ) * 100

    if (
        stop_loss_pct < MIN_STOP_LOSS_PCT
        or stop_loss_pct > MAX_STOP_LOSS_PCT
    ):

        return None

    # ========================================================
    # TARGET
    # ========================================================

    # 최근 4H 저항
    resistance_1 = np.max(
        h4_high[-20:-1]
    )

    resistance_2 = np.max(
        h4_high[-40:-1]
    )

    risk = (
        current_price
        - stop_loss
    )

    # TP1:
    # 최근 저항과 최소 1.5R 중 높은 쪽
    min_tp1 = (
        current_price
        + risk * MIN_RR_TP1
    )

    raw_tp1 = max(
        resistance_1,
        min_tp1
    )

    target_1 = round_upbit_tick(
        raw_tp1
    )

    tp1_pct = (
        (target_1 - current_price)
        / current_price
    ) * 100

    # TP1이 너무 멀면 매수하지 않음
    if tp1_pct > MAX_TAKE_PROFIT_1_PCT:

        return None

    rr_tp1 = (
        (target_1 - current_price)
        / risk
    )

    if rr_tp1 < MIN_RR_TP1:

        return None

    # TP2
    min_tp2 = (
        current_price
        + risk * MIN_RR_TP2
    )

    raw_tp2 = max(
        resistance_2,
        min_tp2,
        target_1 * 1.015
    )

    target_2 = round_upbit_tick(
        raw_tp2
    )

    rr_tp2 = (
        (target_2 - current_price)
        / risk
    )

    # TP3
    target_3 = round_upbit_tick(
        max(
            target_2 * 1.025,
            current_price + risk * 3
        )
    )

    rr_tp3 = (
        (target_3 - current_price)
        / risk
    )

    # ========================================================
    # RETURN
    # ========================================================

    candle_time = m15[-1].get(
        "candle_date_time_kst",
        ""
    )

    return {

        "score": score,

        "current_price": current_price,

        "stop_loss": stop_loss,

        "stop_loss_pct": stop_loss_pct,

        "target_1": target_1,
        "target_2": target_2,
        "target_3": target_3,

        "tp1_pct": tp1_pct,

        "rr_tp1": rr_tp1,
        "rr_tp2": rr_tp2,
        "rr_tp3": rr_tp3,

        "volume_ratio": volume_ratio,

        "distance_from_ema20": distance_from_ema20,

        "change_4h": change_4h,

        "breakout": breakout,

        "candle_time": candle_time,

        "reasons": reasons
    }


# ============================================================
# DUPLICATE / COOLDOWN
# ============================================================

def should_send_signal(
    ticker,
    result,
    cache
):

    now = datetime.now(
        timezone.utc
    )

    previous = cache.get(
        ticker
    )

    if not previous:

        return True

    # --------------------------------------------------------
    # 같은 15M 캔들 중복 방지
    # --------------------------------------------------------

    if (
        previous.get("signal_candle")
        == result["candle_time"]
    ):

        return False

    # --------------------------------------------------------
    # 시간 cooldown
    # --------------------------------------------------------

    last_alert = previous.get(
        "last_alert"
    )

    if last_alert:

        try:

            last_time = datetime.fromisoformat(
                last_alert
            )

            if (
                now - last_time
            ).total_seconds() < (
                SIGNAL_COOLDOWN_HOURS * 3600
            ):

                return False

        except Exception:
            pass

    # --------------------------------------------------------
    # 이전 신호보다 충분히 가격이 상승한 경우
    # 새로운 setup 허용
    # --------------------------------------------------------

    previous_price = previous.get(
        "price",
        0
    )

    if previous_price > 0:

        price_change = abs(
            (
                result["current_price"]
                - previous_price
            )
            / previous_price
        ) * 100

        if price_change < MIN_NEW_SIGNAL_PRICE_DISTANCE:

            return False

    return True


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def build_message(
    ticker,
    korean_name,
    acc_trade_price,
    result
):

    current = result["current_price"]

    volume_ratio = result[
        "volume_ratio"
    ]

    if volume_ratio >= STRONG_VOLUME_RATIO:

        volume_text = "💥 폭발적"

    elif volume_ratio >= 220:

        volume_text = "🔥 강한"

    else:

        volume_text = "⚡ 증가"

    breakout_text = (
        "돌파 확인"
        if result["breakout"]
        else "돌파 전환"
    )

    reasons = ", ".join(
        result["reasons"]
    )

    upbit_url = (
        f"https://upbit.com/exchange?code=CRIX.UPBIT.{ticker}"
    )

    message = (

        f"🚀 *[SPOT BUY SIGNAL]*\n"
        f"━━━━━━━━━━━━━━━━━━\n"

        f"▪ *자산:* `{korean_name} ({ticker})`\n"
        f"▪ *현재가:* `{format_price(current)} KRW`\n"
        f"▪ *24H 거래대금:* "
        f"`{acc_trade_price / 100_000_000:,.1f}억`\n\n"

        f"📊 *SIGNAL QUALITY*\n"
        f"▪ 점수: `{result['score']} / 100`\n"
        f"▪ 15M 거래량: "
        f"`평균 대비 {volume_ratio:.0f}%` "
        f"{volume_text}\n"
        f"▪ 4H 변화: "
        f"`{result['change_4h']:+.1f}%`\n"
        f"▪ 상태: `{breakout_text}`\n\n"

        f"🎯 *TARGET / RISK*\n"
        f"▪ ENTRY: "
        f"`{format_price(current)}원`\n"

        f"▪ TP1: "
        f"`{format_price(result['target_1'])}원` "
        f"(+{result['tp1_pct']:.1f}%) "
        f"`R:R 1:{result['rr_tp1']:.1f}`\n"

        f"▪ TP2: "
        f"`{format_price(result['target_2'])}원` "
        f"`R:R 1:{result['rr_tp2']:.1f}`\n"

        f"▪ TP3: "
        f"`{format_price(result['target_3'])}원` "
        f"`R:R 1:{result['rr_tp3']:.1f}`\n"

        f"▪ SL: "
        f"`{format_price(result['stop_loss'])}원` "
        f"(-{result['stop_loss_pct']:.1f}%)\n\n"

        f"🔎 *CONFIRMATION*\n"
        f"`{reasons}`\n\n"

        f"📱 [업비트 차트 열기]({upbit_url})\n"
        f"━━━━━━━━━━━━━━━━━━\n"

        f"⚠️ *현물 매수 참고용 신호입니다. "
        f"수익을 보장하지 않으며 SL 이탈 시 손실 관리가 필요합니다.*"
    )

    return message


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("UPBIT SPOT PROFIT-FOCUSED SIGNAL BOT")
    print("=" * 60)

    cache = load_cache()

    # --------------------------------------------------------
    # BTC
    # --------------------------------------------------------

    btc_safe, btc_reason = check_btc_market()

    print(
        f"[BTC MARKET] {btc_reason}"
    )

    if not btc_safe:

        print(
            "🚫 BTC 시장 조건 불충족."
        )

        print(
            "알트코인 신규 매수 신호를 생성하지 않습니다."
        )

        return

    # --------------------------------------------------------
    # Markets
    # --------------------------------------------------------

    market_names = get_market_names()

    if not market_names:

        print(
            "마켓 조회 실패"
        )

        return

    markets = list(
        market_names.keys()
    )

    print(
        f"전체 KRW 마켓: {len(markets)}"
    )

    # --------------------------------------------------------
    # Ticker
    # --------------------------------------------------------

    ticker_data = get_all_tickers(
        markets
    )

    if not ticker_data:

        print(
            "Ticker 데이터 없음"
        )

        return

    # --------------------------------------------------------
    # 유동성 필터
    # --------------------------------------------------------

    candidates = [

        x for x in ticker_data

        if x.get(
            "acc_trade_price_24h",
            0
        ) >= MIN_24H_TRADE_VALUE

    ]

    candidates.sort(
        key=lambda x: x.get(
            "acc_trade_price_24h",
            0
        ),
        reverse=True
    )

    candidates = candidates[
        :MAX_SCAN_COINS
    ]

    print(
        f"유동성 통과: {len(candidates)}개"
    )

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    signal_count = 0

    for index, data in enumerate(
        candidates,
        start=1
    ):

        ticker = data["market"]

        korean_name = market_names.get(
            ticker,
            ticker
        )

        current_price = data[
            "trade_price"
        ]

        acc_trade_price = data[
            "acc_trade_price_24h"
        ]

        print(
            f"[{index}/{len(candidates)}] "
            f"{korean_name} "
            f"({ticker}) 분석"
        )

        try:

            result = analyze_coin(
                ticker,
                current_price
            )

            if not result:

                time.sleep(0.12)

                continue

            print(
                f"  SCORE={result['score']} "
                f"VOL={result['volume_ratio']:.0f}% "
                f"SL={result['stop_loss_pct']:.2f}% "
                f"RR={result['rr_tp1']:.2f}"
            )

            if not should_send_signal(
                ticker,
                result,
                cache
            ):

                print(
                    "  → 중복/쿨다운"
                )

                time.sleep(0.12)

                continue

            message = build_message(
                ticker,
                korean_name,
                acc_trade_price,
                result
            )

            sent = send_telegram_message(
                message
            )

            if sent:

                now = datetime.now(
                    timezone.utc
                )

                cache[ticker] = {

                    "price":
                        result[
                            "current_price"
                        ],

                    "target_1":
                        result[
                            "target_1"
                        ],

                    "signal_candle":
                        result[
                            "candle_time"
                        ],

                    "last_alert":
                        now.isoformat(),

                    "score":
                        result[
                            "score"
                        ]
                }

                save_cache(
                    cache
                )

                signal_count += 1

                print(
                    f"  🚀 SIGNAL SENT: "
                    f"{korean_name}"
                )

        except Exception as e:

            print(
                f"  분석 오류: {e}"
            )

        # Upbit candle API rate limit 대응
        time.sleep(0.15)

    print("=" * 60)

    print(
        f"SCAN COMPLETE / "
        f"SIGNALS: {signal_count}"
    )

    print("=" * 60)


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "사용자에 의해 종료"
        )

    except Exception as e:

        print(
            f"치명적 오류: {e}"
        )
