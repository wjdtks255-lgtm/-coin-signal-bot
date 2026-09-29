import json
import os
import time
import requests
import numpy as np

from datetime import datetime, timezone


# ============================================================
# UPBIT SPOT PROFIT TRACKING BOT
# ============================================================
#
# CORE LOGIC
#
# 1. BTC market condition check
# 2. BTC is NOT an absolute altcoin filter
# 3. Strong BTC crash -> block new entries
# 4. BTC weak -> individual coin requirements become stricter
# 5. Scan high-liquidity KRW markets
# 6. Analyze 1D / 4H / 1H / 15M
# 7. Create only high-quality signals
# 8. Track active positions
# 9. TP1 -> SL to ENTRY
# 10. TP2 -> SL to TP1
# 11. TP3 -> close
# 12. SL -> close
#
# IMPORTANT:
# This is a signal/tracking system.
# It does NOT execute real trades.
# Profitability must be validated with actual historical/forward data.
# ============================================================


# ============================================================
# FILES
# ============================================================

CACHE_FILE = "tracked_coins.json"


# ============================================================
# ENV
# ============================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# ============================================================
# UPBIT
# ============================================================

UPBIT_BASE_URL = "https://api.upbit.com/v1"

session = requests.Session()

session.headers.update({
    "User-Agent": "Upbit-Spot-Profit-Tracking-Bot/4.0"
})


# ============================================================
# LIQUIDITY
# ============================================================

MIN_24H_TRADE_VALUE = 10_000_000_000

MAX_SCAN_COINS = 60


# ============================================================
# SIGNAL QUALITY
# ============================================================

MIN_SCORE = 75

# BTC weak condition
WEAK_BTC_MIN_SCORE = 80

MIN_VOLUME_RATIO = 180

STRONG_VOLUME_RATIO = 300

MIN_BODY_RATIO = 0.55

MAX_ENTRY_DISTANCE_FROM_EMA20 = 4.0


# ============================================================
# RISK
# ============================================================

MIN_STOP_LOSS_PCT = 1.0

MAX_STOP_LOSS_PCT = 7.0

MIN_RR_TP1 = 1.5

MIN_RR_TP2 = 2.0

MAX_TP1_DISTANCE_PCT = 12.0


# ============================================================
# DUPLICATE
# ============================================================

SIGNAL_COOLDOWN_HOURS = 6

MIN_NEW_SIGNAL_PRICE_DISTANCE = 2.0


# ============================================================
# BTC MARKET FILTER
# ============================================================

# BTC 15M move below this value = emergency risk-off
BTC_15M_CRASH_PCT = -1.5

# BTC 1H move below this value = additional risk warning
BTC_1H_CRASH_PCT = -2.0


# ============================================================
# TRACKING
# ============================================================

MOVE_SL_TO_ENTRY_AFTER_TP1 = True

MOVE_SL_TO_TP1_AFTER_TP2 = True


# ============================================================
# API
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
            f"[API ERROR] {endpoint}: {e}"
        )

        return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram_status():

    token_ok = bool(
        TELEGRAM_TOKEN
        and TELEGRAM_TOKEN.strip()
    )

    chat_ok = bool(
        TELEGRAM_CHAT_ID
        and TELEGRAM_CHAT_ID.strip()
    )

    print(
        "[TELEGRAM] "
        f"TOKEN={'OK' if token_ok else 'MISSING'} "
        f"CHAT_ID={'OK' if chat_ok else 'MISSING'}"
    )

    return token_ok and chat_ok


def send_telegram_message(text):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:

        print(
            "[TELEGRAM] "
            "환경변수가 없습니다."
        )

        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

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

        result = response.json()

        if not result.get("ok"):

            print(
                f"[TELEGRAM ERROR] "
                f"{result}"
            )

            return False

        print(
            "[TELEGRAM] 메시지 전송 성공"
        )

        return True

    except Exception as e:

        print(
            f"[TELEGRAM ERROR] {e}"
        )

        return False


# ============================================================
# CACHE
# ============================================================

def load_cache():

    if not os.path.exists(CACHE_FILE):

        return {
            "positions": {},
            "history": []
        }

    try:

        with open(
            CACHE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if not isinstance(data, dict):

            return {
                "positions": {},
                "history": []
            }

        if "positions" not in data:

            data["positions"] = {}

        if "history" not in data:

            data["history"] = []

        return data

    except Exception as e:

        print(
            f"[CACHE LOAD ERROR] {e}"
        )

        return {
            "positions": {},
            "history": []
        }


def save_cache(cache):

    try:

        temp_file = (
            CACHE_FILE
            + ".tmp"
        )

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                cache,
                f,
                ensure_ascii=False,
                indent=4
            )

        os.replace(
            temp_file,
            CACHE_FILE
        )

    except Exception as e:

        print(
            f"[CACHE ERROR] {e}"
        )


# ============================================================
# PRICE
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
# EMA
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

    for i in range(
        1,
        len(values)
    ):

        result[i] = (
            alpha * values[i]
            + (1 - alpha)
            * result[i - 1]
        )

    return result


# ============================================================
# ATR
# ============================================================

def atr(
    highs,
    lows,
    closes,
    period=14
):

    highs = np.asarray(
        highs,
        dtype=float
    )

    lows = np.asarray(
        lows,
        dtype=float
    )

    closes = np.asarray(
        closes,
        dtype=float
    )

    if len(closes) < period + 1:

        return None

    previous_close = closes[:-1]

    tr1 = highs[1:] - lows[1:]

    tr2 = np.abs(
        highs[1:]
        - previous_close
    )

    tr3 = np.abs(
        lows[1:]
        - previous_close
    )

    tr = np.maximum(
        tr1,
        np.maximum(
            tr2,
            tr3
        )
    )

    return np.mean(
        tr[-period:]
    )


# ============================================================
# MARKET
# ============================================================

def get_market_names():

    data = api_get(
        "/market/all",
        {
            "is_details": "true"
        }
    )

    if not isinstance(
        data,
        list
    ):

        return {}

    result = {}

    for item in data:

        market = item.get(
            "market"
        )

        if not market:

            continue

        if not market.startswith(
            "KRW-"
        ):

            continue

        market_event = item.get(
            "market_event",
            {}
        )

        if isinstance(
            market_event,
            dict
        ):

            if market_event.get(
                "warning"
            ) is True:

                continue

        result[
            market
        ] = item.get(
            "korean_name",
            market
        )

    return result


# ============================================================
# TICKERS
# ============================================================

def get_tickers(markets):

    result = []

    chunk_size = 100

    for i in range(
        0,
        len(markets),
        chunk_size
    ):

        chunk = markets[
            i:i + chunk_size
        ]

        data = api_get(
            "/ticker",
            {
                "markets": ",".join(chunk)
            }
        )

        if isinstance(
            data,
            list
        ):

            result.extend(
                data
            )

        time.sleep(
            0.12
        )

    return result


# ============================================================
# CANDLES
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

    if not isinstance(
        data,
        list
    ):

        return None

    if len(data) < 30:

        return None

    data.reverse()

    return data


def fetch_daily_candles(
    market,
    count
):

    data = api_get(
        "/candles/days",
        {
            "market": market,
            "count": count
        }
    )

    if not isinstance(
        data,
        list
    ):

        return None

    if len(data) < 30:

        return None

    data.reverse()

    return data


# ============================================================
# CANDLE QUALITY
# ============================================================

def candle_quality(candle):

    opening = candle[
        "opening_price"
    ]

    high = candle[
        "high_price"
    ]

    low = candle[
        "low_price"
    ]

    close = candle[
        "trade_price"
    ]

    candle_range = high - low

    if candle_range <= 0:

        return 0, 0

    body = abs(
        close - opening
    )

    body_ratio = (
        body
        / candle_range
    )

    close_position = (
        close - low
    ) / candle_range

    return (
        body_ratio,
        close_position
    )


# ============================================================
# BTC MARKET CONDITION
# ============================================================

def check_btc_market():

    btc_15 = fetch_candles(
        "KRW-BTC",
        15,
        100
    )

    btc_1h = fetch_candles(
        "KRW-BTC",
        60,
        120
    )

    btc_4h = fetch_candles(
        "KRW-BTC",
        240,
        120
    )

    if not all([
        btc_15,
        btc_1h,
        btc_4h
    ]):

        return {
            "status": "UNKNOWN",
            "score": 0,
            "reason": "BTC 데이터 부족"
        }

    # 진행 중 캔들 제거

    btc_15 = btc_15[:-1]
    btc_1h = btc_1h[:-1]
    btc_4h = btc_4h[:-1]

    close15 = np.array([
        x["trade_price"]
        for x in btc_15
    ])

    close1h = np.array([
        x["trade_price"]
        for x in btc_1h
    ])

    close4h = np.array([
        x["trade_price"]
        for x in btc_4h
    ])

    ema20_1h = ema(
        close1h,
        20
    )

    ema50_1h = ema(
        close1h,
        50
    )

    ema20_4h = ema(
        close4h,
        20
    )

    if any(
        x is None
        for x in [
            ema20_1h,
            ema50_1h,
            ema20_4h
        ]
    ):

        return {
            "status": "UNKNOWN",
            "score": 0,
            "reason": "BTC EMA 데이터 부족"
        }

    current15 = close15[-1]

    previous15 = close15[-2]

    change15 = (
        (
            current15
            - previous15
        )
        / previous15
    ) * 100

    change1h = (
        (
            close1h[-1]
            - close1h[-5]
        )
        / close1h[-5]
    ) * 100

    score = 0

    if close4h[-1] > ema20_4h[-1]:

        score += 35

    if ema20_1h[-1] > ema50_1h[-1]:

        score += 35

    if close1h[-1] > ema20_1h[-1]:

        score += 20

    if change15 > -0.5:

        score += 10

    # --------------------------------------------------------
    # EMERGENCY CRASH
    # --------------------------------------------------------

    if (
        change15
        <= BTC_15M_CRASH_PCT
    ):

        return {
            "status": "CRASH",
            "score": score,
            "reason": (
                f"BTC 15M 급락 "
                f"{change15:.2f}%"
            ),
            "change15": change15,
            "change1h": change1h
        }

    if (
        change1h
        <= BTC_1H_CRASH_PCT
        and
        change15 < -0.8
    ):

        return {
            "status": "CRASH",
            "score": score,
            "reason": (
                f"BTC 1H 급락 "
                f"{change1h:.2f}%"
            ),
            "change15": change15,
            "change1h": change1h
        }

    # --------------------------------------------------------
    # MARKET STATUS
    # --------------------------------------------------------

    if score >= 70:

        status = "BULL"

        reason = (
            f"BTC 강세 / Score {score}"
        )

    elif score >= 45:

        status = "NEUTRAL"

        reason = (
            f"BTC 중립 / Score {score}"
        )

    else:

        status = "WEAK"

        reason = (
            f"BTC 약세 / Score {score}"
        )

    return {

        "status": status,

        "score": score,

        "reason": reason,

        "change15": change15,

        "change1h": change1h
    }


# ============================================================
# COIN ANALYSIS
# ============================================================

def analyze_coin(
    ticker,
    current_price,
    btc_status
):

    daily = fetch_daily_candles(
        ticker,
        80
    )

    h4 = fetch_candles(
        ticker,
        240,
        100
    )

    h1 = fetch_candles(
        ticker,
        60,
        100
    )

    m15 = fetch_candles(
        ticker,
        15,
        100
    )

    if not all([
        daily,
        h4,
        h1,
        m15
    ]):

        return None

    if any(
        len(x) < 60
        for x in [
            daily,
            h4,
            h1,
            m15
        ]
    ):

        return None

    # 진행 중 캔들 제거

    daily = daily[:-1]
    h4 = h4[:-1]
    h1 = h1[:-1]
    m15 = m15[:-1]

    # ========================================================
    # ARRAYS
    # ========================================================

    d_close = np.array([
        x["trade_price"]
        for x in daily
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

    # ========================================================
    # EMA
    # ========================================================

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

    score = 0

    reasons = []

    # ========================================================
    # 1D TREND
    # ========================================================

    if d_close[-1] <= d_ema20[-1]:

        return None

    score += 10

    reasons.append(
        "1D EMA20 위"
    )

    if d_ema20[-1] <= d_ema50[-1]:

        return None

    score += 10

    reasons.append(
        "1D EMA20 > EMA50"
    )

    if d_ema20[-1] > d_ema20[-4]:

        score += 5

        reasons.append(
            "1D EMA 상승"
        )

    # ========================================================
    # 4H TREND
    # ========================================================

    if h4_close[-1] <= h4_ema20[-1]:

        return None

    score += 10

    reasons.append(
        "4H EMA20 위"
    )

    if h4_ema20[-1] <= h4_ema50[-1]:

        return None

    score += 10

    reasons.append(
        "4H EMA20 > EMA50"
    )

    if h4_ema20[-1] > h4_ema20[-4]:

        score += 5

        reasons.append(
            "4H EMA 상승"
        )

    # ========================================================
    # 1H TREND
    # ========================================================

    if h1_close[-1] <= h1_ema20[-1]:

        return None

    score += 5

    reasons.append(
        "1H EMA20 위"
    )

    if h1_ema20[-1] > h1_ema50[-1]:

        score += 5

        reasons.append(
            "1H EMA 정배열"
        )

    if h1_ema20[-1] > h1_ema20[-4]:

        score += 5

        reasons.append(
            "1H EMA 상승"
        )

    # ========================================================
    # 15M
    # ========================================================

    if m15_close[-1] <= m15_ema20[-1]:

        return None

    score += 5

    reasons.append(
        "15M EMA20 위"
    )

    if m15_ema20[-1] > m15_ema20[-4]:

        score += 5

        reasons.append(
            "15M EMA 상승"
        )

    # ========================================================
    # VOLUME
    # ========================================================

    avg_volume = np.mean(
        m15_volume[-21:-1]
    )

    if avg_volume <= 0:

        return None

    volume_ratio = (
        m15_volume[-1]
        / avg_volume
    ) * 100

    if volume_ratio < MIN_VOLUME_RATIO:

        return None

    score += 10

    reasons.append(
        f"거래량 {volume_ratio:.0f}%"
    )

    # ========================================================
    # CANDLE
    # ========================================================

    body_ratio, close_position = (
        candle_quality(
            m15[-1]
        )
    )

    last_open = m15[-1][
        "opening_price"
    ]

    last_close = m15[-1][
        "trade_price"
    ]

    if (
        last_close > last_open
        and
        body_ratio >= MIN_BODY_RATIO
        and
        close_position >= 0.70
    ):

        score += 10

        reasons.append(
            "15M 강한 양봉"
        )

    else:

        return None

    # ========================================================
    # BREAKOUT
    # ========================================================

    recent_high = np.max(
        m15_close[-21:-1]
    )

    breakout = (
        m15_close[-1]
        >= recent_high
    )

    if breakout:

        score += 5

        reasons.append(
            "15M 고점 돌파"
        )

    # ========================================================
    # CHASING FILTER
    # ========================================================

    distance_from_ema = (
        (
            current_price
            - m15_ema20[-1]
        )
        / m15_ema20[-1]
    ) * 100

    if (
        distance_from_ema
        > MAX_ENTRY_DISTANCE_FROM_EMA20
    ):

        return None

    # ========================================================
    # 4H PUMP FILTER
    # ========================================================

    change_4h = (
        (
            h4_close[-1]
            - h4_close[-5]
        )
        / h4_close[-5]
    ) * 100

    if change_4h >= 12:

        return None

    # ========================================================
    # BTC WEAK MARKET MODE
    # ========================================================
    #
    # BTC 약세라고 알트 신호를 차단하지 않는다.
    #
    # 대신 개별 코인이 더 강해야 통과한다.
    # ========================================================

    btc_bonus = 0

    if btc_status == "BULL":

        btc_bonus = 5

        reasons.append(
            "BTC 우호적"
        )

    elif btc_status == "NEUTRAL":

        btc_bonus = 0

        reasons.append(
            "BTC 중립"
        )

    elif btc_status == "WEAK":

        # BTC 약세에서도 상승하는
        # 개별 강세 코인을 허용.
        #
        # 대신 추가 조건:
        # 15M 거래량 250% 이상
        # + 실제 고점 돌파
        # 를 요구한다.

        if (
            volume_ratio < 250
            or
            not breakout
        ):

            return None

        score += 5

        reasons.append(
            "BTC 약세 속 개별 강세"
        )

    # ========================================================
    # FINAL SCORE
    # ========================================================

    final_score = (
        score
        + btc_bonus
    )

    required_score = MIN_SCORE

    if btc_status == "WEAK":

        required_score = (
            WEAK_BTC_MIN_SCORE
        )

    if final_score < required_score:

        return None

    # ========================================================
    # STOP LOSS
    # ========================================================

    swing_low = np.min(
        h4_low[-12:-1]
    )

    atr_value = atr(
        h4_high,
        h4_low,
        h4_close,
        14
    )

    if atr_value is None:

        return None

    raw_stop = (
        swing_low
        - atr_value * 0.15
    )

    stop_loss = round_upbit_tick(
        raw_stop
    )

    if stop_loss >= current_price:

        return None

    stop_loss_pct = (
        (
            current_price
            - stop_loss
        )
        / current_price
    ) * 100

    if (
        stop_loss_pct < MIN_STOP_LOSS_PCT
        or
        stop_loss_pct > MAX_STOP_LOSS_PCT
    ):

        return None

    # ========================================================
    # TARGETS
    # ========================================================

    risk = (
        current_price
        - stop_loss
    )

    resistance_1 = np.max(
        h4_high[-20:-1]
    )

    resistance_2 = np.max(
        h4_high[-40:-1]
    )

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
        (
            target_1
            - current_price
        )
        / current_price
    ) * 100

    if tp1_pct > MAX_TP1_DISTANCE_PCT:

        return None

    rr_tp1 = (
        target_1
        - current_price
    ) / risk

    if rr_tp1 < MIN_RR_TP1:

        return None

    # TP2

    min_tp2 = (
        current_price
        + risk * MIN_RR_TP2
    )

    target_2 = round_upbit_tick(
        max(
            resistance_2,
            min_tp2,
            target_1 * 1.015
        )
    )

    rr_tp2 = (
        target_2
        - current_price
    ) / risk

    # TP3

    target_3 = round_upbit_tick(
        max(
            target_2 * 1.025,
            current_price
            + risk * 3
        )
    )

    rr_tp3 = (
        target_3
        - current_price
    ) / risk

    # ========================================================
    # RESULT
    # ========================================================

    return {

        "score": final_score,

        "current_price": current_price,

        "stop_loss": stop_loss,

        "original_stop_loss": stop_loss,

        "target_1": target_1,

        "target_2": target_2,

        "target_3": target_3,

        "tp1_pct": tp1_pct,

        "stop_loss_pct": stop_loss_pct,

        "rr_tp1": rr_tp1,

        "rr_tp2": rr_tp2,

        "rr_tp3": rr_tp3,

        "volume_ratio": volume_ratio,

        "distance_from_ema20": distance_from_ema,

        "change_4h": change_4h,

        "breakout": breakout,

        "btc_status": btc_status,

        "candle_time": m15[-1].get(
            "candle_date_time_kst",
            ""
        ),

        "reasons": reasons
    }


# ============================================================
# 1 MINUTE CANDLES
# ============================================================

def get_recent_1m_candles(
    ticker,
    count=10
):

    data = api_get(
        "/candles/minutes/1",
        {
            "market": ticker,
            "count": count
        }
    )

    if not isinstance(
        data,
        list
    ):

        return []

    data.reverse()

    return data


# ============================================================
# TRACK ACTIVE POSITIONS
# ============================================================

def track_positions(cache):

    positions = cache.get(
        "positions",
        {}
    )

    if not positions:

        print(
            "[TRACKING] "
            "현재 추적 중인 종목 없음"
        )

        return

    print(
        f"[TRACKING] "
        f"{len(positions)}개 종목 추적 시작"
    )

    completed = []

    for ticker in list(
        positions.keys()
    ):

        position = positions[
            ticker
        ]

        try:

            current_ticker = api_get(
                "/ticker",
                {
                    "markets": ticker
                }
            )

            if (
                not isinstance(
                    current_ticker,
                    list
                )
                or
                not current_ticker
            ):

                continue

            current_price = (
                current_ticker[0]
                ["trade_price"]
            )

            candles = (
                get_recent_1m_candles(
                    ticker,
                    10
                )
            )

            if not candles:

                continue

            stage = position.get(
                "stage",
                "ENTRY"
            )

            entry = position[
                "entry"
            ]

            tp1 = position[
                "tp1"
            ]

            tp2 = position[
                "tp2"
            ]

            tp3 = position[
                "tp3"
            ]

            sl = position[
                "current_sl"
            ]

            hit_tp1 = False
            hit_tp2 = False
            hit_tp3 = False
            hit_sl = False

            for candle in candles:

                high = candle[
                    "high_price"
                ]

                low = candle[
                    "low_price"
                ]

                if low <= sl:

                    hit_sl = True

                if high >= tp1:

                    hit_tp1 = True

                if high >= tp2:

                    hit_tp2 = True

                if high >= tp3:

                    hit_tp3 = True

            # =================================================
            # SL FIRST
            # =================================================

            if hit_sl:

                if stage in [
                    "TP1",
                    "TP2"
                ]:

                    pnl_pct = (
                        (
                            sl - entry
                        )
                        / entry
                    ) * 100

                    result_type = (
                        "BREAKEVEN"
                        if abs(
                            pnl_pct
                        ) < 0.05
                        else "PROTECTED"
                    )

                else:

                    pnl_pct = (
                        (
                            sl - entry
                        )
                        / entry
                    ) * 100

                    result_type = (
                        "STOP_LOSS"
                    )

                message = (

                    f"🛑 *[POSITION CLOSED]*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"

                    f"▪ 자산: `{ticker}`\n"

                    f"▪ 진입가: "
                    f"`{format_price(entry)}원`\n"

                    f"▪ 종료가: "
                    f"`{format_price(sl)}원`\n"

                    f"▪ 결과: "
                    f"`{result_type}`\n"

                    f"▪ 손익률: "
                    f"`{pnl_pct:+.2f}%`\n"

                    f"▪ 당시 단계: "
                    f"`{stage}`\n"

                    f"━━━━━━━━━━━━━━━━━━\n"

                    f"📌 추적 종료"
                )

                send_telegram_message(
                    message
                )

                record_history(
                    cache,
                    ticker,
                    position,
                    result_type,
                    sl,
                    pnl_pct
                )

                completed.append(
                    ticker
                )

                continue

            # =================================================
            # TP3
            # =================================================

            if (
                hit_tp3
                and
                stage != "TP3"
            ):

                pnl_pct = (
                    (
                        tp3 - entry
                    )
                    / entry
                ) * 100

                message = (

                    f"🏆 *[TP3 FINAL]*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"

                    f"▪ 자산: `{ticker}`\n"

                    f"▪ ENTRY: "
                    f"`{format_price(entry)}원`\n"

                    f"▪ TP3: "
                    f"`{format_price(tp3)}원`\n"

                    f"▪ 수익률: "
                    f"`+{pnl_pct:.2f}%`\n\n"

                    f"🎯 TP1 / TP2 / TP3 완료\n"
                    f"📌 추적 종료"
                )

                send_telegram_message(
                    message
                )

                record_history(
                    cache,
                    ticker,
                    position,
                    "TP3",
                    tp3,
                    pnl_pct
                )

                completed.append(
                    ticker
                )

                continue

            # =================================================
            # TP2
            # =================================================

            if (
                hit_tp2
                and
                stage not in [
                    "TP2",
                    "TP3"
                ]
            ):

                pnl_pct = (
                    (
                        tp2 - entry
                    )
                    / entry
                ) * 100

                position[
                    "stage"
                ] = "TP2"

                if MOVE_SL_TO_TP1_AFTER_TP2:

                    position[
                        "current_sl"
                    ] = tp1

                message = (

                    f"🎯 *[TP2 REACHED]*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"

                    f"▪ 자산: `{ticker}`\n"

                    f"▪ ENTRY: "
                    f"`{format_price(entry)}원`\n"

                    f"▪ TP2: "
                    f"`{format_price(tp2)}원`\n"

                    f"▪ 현재 수익: "
                    f"`+{pnl_pct:.2f}%`\n"

                    f"▪ 새로운 SL: "
                    f"`{format_price(position['current_sl'])}원`\n\n"

                    f"➡️ TP3 계속 추적"
                )

                send_telegram_message(
                    message
                )

                continue

            # =================================================
            # TP1
            # =================================================

            if (
                hit_tp1
                and
                stage == "ENTRY"
            ):

                pnl_pct = (
                    (
                        tp1 - entry
                    )
                    / entry
                ) * 100

                position[
                    "stage"
                ] = "TP1"

                if MOVE_SL_TO_ENTRY_AFTER_TP1:

                    position[
                        "current_sl"
                    ] = entry

                message = (

                    f"🎯 *[TP1 REACHED]*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"

                    f"▪ 자산: `{ticker}`\n"

                    f"▪ ENTRY: "
                    f"`{format_price(entry)}원`\n"

                    f"▪ TP1: "
                    f"`{format_price(tp1)}원`\n"

                    f"▪ 수익: "
                    f"`+{pnl_pct:.2f}%`\n"

                    f"▪ 보호 SL: "
                    f"`{format_price(position['current_sl'])}원`\n\n"

                    f"➡️ TP2 / TP3 계속 추적"
                )

                send_telegram_message(
                    message
                )

                continue

        except Exception as e:

            print(
                f"[TRACK ERROR] "
                f"{ticker}: {e}"
            )

    for ticker in completed:

        positions.pop(
            ticker,
            None
        )

    cache[
        "positions"
    ] = positions

    save_cache(
        cache
    )


# ============================================================
# HISTORY
# ============================================================

def record_history(
    cache,
    ticker,
    position,
    result_type,
    exit_price,
    pnl_pct
):

    history = cache.get(
        "history",
        []
    )

    record = {

        "ticker": ticker,

        "entry": position.get(
            "entry"
        ),

        "exit": exit_price,

        "result": result_type,

        "pnl_pct": pnl_pct,

        "score": position.get(
            "score"
        ),

        "entry_time": position.get(
            "entry_time"
        ),

        "exit_time": datetime.now(
            timezone.utc
        ).isoformat()
    }

    history.append(
        record
    )

    cache[
        "history"
    ] = history[-500:]


# ============================================================
# DUPLICATE CHECK
# ============================================================

def can_create_new_signal(
    ticker,
    result,
    cache
):

    positions = cache.get(
        "positions",
        {}
    )

    if ticker in positions:

        return False

    history = cache.get(
        "history",
        []
    )

    previous = None

    for item in reversed(
        history
    ):

        if item.get(
            "ticker"
        ) == ticker:

            previous = item

            break

    if not previous:

        return True

    previous_time = previous.get(
        "entry_time"
    )

    if previous_time:

        try:

            previous_dt = (
                datetime.fromisoformat(
                    previous_time
                )
            )

            now = datetime.now(
                timezone.utc
            )

            elapsed = (
                now
                - previous_dt
            ).total_seconds()

            if elapsed < (
                SIGNAL_COOLDOWN_HOURS
                * 3600
            ):

                return False

        except Exception:

            pass

    previous_entry = previous.get(
        "entry",
        0
    )

    if previous_entry > 0:

        distance = abs(
            (
                result[
                    "current_price"
                ]
                - previous_entry
            )
            / previous_entry
        ) * 100

        if (
            distance
            < MIN_NEW_SIGNAL_PRICE_DISTANCE
        ):

            return False

    return True


# ============================================================
# CREATE POSITION
# ============================================================

def create_position(
    ticker,
    korean_name,
    acc_trade_price,
    result,
    cache
):

    entry = result[
        "current_price"
    ]

    now = datetime.now(
        timezone.utc
    ).isoformat()

    position = {

        "ticker": ticker,

        "korean_name": korean_name,

        "entry": entry,

        "original_sl": result[
            "original_stop_loss"
        ],

        "current_sl": result[
            "stop_loss"
        ],

        "tp1": result[
            "target_1"
        ],

        "tp2": result[
            "target_2"
        ],

        "tp3": result[
            "target_3"
        ],

        "score": result[
            "score"
        ],

        "volume_ratio": result[
            "volume_ratio"
        ],

        "rr_tp1": result[
            "rr_tp1"
        ],

        "rr_tp2": result[
            "rr_tp2"
        ],

        "rr_tp3": result[
            "rr_tp3"
        ],

        "stage": "ENTRY",

        "entry_time": now,

        "signal_candle": result[
            "candle_time"
        ],

        "btc_status": result.get(
            "btc_status",
            "UNKNOWN"
        )
    }

    cache[
        "positions"
    ][ticker] = position

    save_cache(
        cache
    )

    volume_ratio = result[
        "volume_ratio"
    ]

    if volume_ratio >= STRONG_VOLUME_RATIO:

        volume_text = "💥 폭발적"

    elif volume_ratio >= 220:

        volume_text = "🔥 강한 유입"

    else:

        volume_text = "⚡ 증가"

    breakout_text = (
        "고점 돌파"
        if result["breakout"]
        else
        "추세 전환"
    )

    reasons = ", ".join(
        result["reasons"]
    )

    upbit_url = (
        "https://upbit.com/exchange"
        f"?code=CRIX.UPBIT.{ticker}"
    )

    btc_status = result.get(
        "btc_status",
        "UNKNOWN"
    )

    if btc_status == "BULL":

        btc_text = "🟢 강세"

    elif btc_status == "NEUTRAL":

        btc_text = "🟡 중립"

    elif btc_status == "WEAK":

        btc_text = "🟠 약세 속 개별강세"

    else:

        btc_text = "⚪ 확인불가"

    message = (

        f"🚀 *[SPOT BUY SIGNAL]*\n"
        f"━━━━━━━━━━━━━━━━━━\n"

        f"▪ 자산: "
        f"`{korean_name} ({ticker})`\n"

        f"▪ ENTRY: "
        f"`{format_price(entry)}원`\n"

        f"▪ 24H 거래대금: "
        f"`{acc_trade_price / 100_000_000:,.1f}억`\n\n"

        f"🌐 *MARKET*\n"

        f"▪ BTC 상태: "
        f"`{btc_text}`\n"

        f"▪ 4H 변화: "
        f"`{result['change_4h']:+.1f}%`\n\n"

        f"📊 *SIGNAL QUALITY*\n"

        f"▪ Score: "
        f"`{result['score']} / 100`\n"

        f"▪ 15M 거래량: "
        f"`평균 대비 {volume_ratio:.0f}%` "
        f"{volume_text}\n"

        f"▪ 상태: "
        f"`{breakout_text}`\n\n"

        f"🎯 *TARGETS*\n"

        f"▪ TP1: "
        f"`{format_price(result['target_1'])}원` "
        f"(+{result['tp1_pct']:.1f}%) "
        f"`R:R 1:{result['rr_tp1']:.1f}`\n"

        f"▪ TP2: "
        f"`{format_price(result['target_2'])}원` "
        f"`R:R 1:{result['rr_tp2']:.1f}`\n"

        f"▪ TP3: "
        f"`{format_price(result['target_3'])}원` "
        f"`R:R 1:{result['rr_tp3']:.1f}`\n\n"

        f"🛡️ *RISK*\n"

        f"▪ SL: "
        f"`{format_price(result['stop_loss'])}원` "
        f"(-{result['stop_loss_pct']:.1f}%)\n"

        f"▪ TP1 → SL = ENTRY\n"
        f"▪ TP2 → SL = TP1\n\n"

        f"🔎 *CONFIRMATION*\n"
        f"`{reasons}`\n\n"

        f"📱 [업비트 차트 열기]"
        f"({upbit_url})\n"

        f"━━━━━━━━━━━━━━━━━━\n"

        f"📌 *지금부터 자동 추적됩니다.*"
    )

    send_telegram_message(
        message
    )


# ============================================================
# NEW SIGNAL SCAN
# ============================================================

def scan_new_signals(cache):

    btc = check_btc_market()

    btc_status = btc.get(
        "status",
        "UNKNOWN"
    )

    btc_reason = btc.get(
        "reason",
        "UNKNOWN"
    )

    print(
        f"[BTC] {btc_reason}"
    )

    # ========================================================
    # ONLY STRONG BTC CRASH BLOCKS NEW ENTRIES
    # ========================================================

    if btc_status == "CRASH":

        print(
            "🚫 BTC 급락 감지"
        )

        print(
            "   신규 매수 신호 탐색 중단"
        )

        return

    if btc_status == "WEAK":

        print(
            "⚠️ BTC 약세"
        )

        print(
            "   개별 코인 조건을 강화하여 계속 탐색"
        )

    elif btc_status == "NEUTRAL":

        print(
            "🟡 BTC 중립"
        )

        print(
            "   정상적으로 개별 코인 탐색"
        )

    elif btc_status == "BULL":

        print(
            "🟢 BTC 강세"
        )

        print(
            "   정상적으로 개별 코인 탐색"
        )

    else:

        print(
            "⚠️ BTC 상태 확인 불가"
        )

        print(
            "   보수적으로 탐색 중단"
        )

        return

    # ========================================================
    # MARKETS
    # ========================================================

    market_names = get_market_names()

    if not market_names:

        print(
            "시장 목록 조회 실패"
        )

        return

    markets = list(
        market_names.keys()
    )

    tickers = get_tickers(
        markets
    )

    candidates = [

        x for x in tickers

        if x.get(
            "acc_trade_price_24h",
            0
        )
        >= MIN_24H_TRADE_VALUE

    ]

    candidates.sort(
        key=lambda x:
        x.get(
            "acc_trade_price_24h",
            0
        ),
        reverse=True
    )

    candidates = candidates[
        :MAX_SCAN_COINS
    ]

    print(
        f"[SCAN] "
        f"{len(candidates)}개 종목 분석"
    )

    signal_count = 0

    for index, data in enumerate(
        candidates,
        start=1
    ):

        ticker = data[
            "market"
        ]

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
            f"{ticker}"
        )

        try:

            result = analyze_coin(
                ticker,
                current_price,
                btc_status
            )

            if not result:

                time.sleep(
                    0.12
                )

                continue

            print(
                f"  SCORE="
                f"{result['score']} "
                f"VOL="
                f"{result['volume_ratio']:.0f}% "
                f"SL="
                f"{result['stop_loss_pct']:.2f}% "
                f"RR="
                f"{result['rr_tp1']:.2f}"
            )

            if not can_create_new_signal(
                ticker,
                result,
                cache
            ):

                print(
                    "  -> 이미 추적 중/쿨다운"
                )

                time.sleep(
                    0.12
                )

                continue

            create_position(
                ticker,
                korean_name,
                acc_trade_price,
                result,
                cache
            )

            signal_count += 1

            print(
                f"  🚀 SIGNAL: "
                f"{korean_name}"
            )

        except Exception as e:

            print(
                f"  [ANALYSIS ERROR] "
                f"{ticker}: {e}"
            )

        time.sleep(
            0.15
        )

    print(
        f"[SCAN COMPLETE] "
        f"신규 신호 {signal_count}개"
    )


# ============================================================
# STATISTICS
# ============================================================

def send_daily_statistics(cache):

    history = cache.get(
        "history",
        []
    )

    if not history:

        print(
            "[STATS] 기록 없음"
        )

        return

    total = len(
        history
    )

    tp3_count = sum(
        1
        for x in history
        if x.get(
            "result"
        ) == "TP3"
    )

    protected_count = sum(
        1
        for x in history
        if x.get(
            "result"
        ) in [
            "BREAKEVEN",
            "PROTECTED"
        ]
    )

    sl_count = sum(
        1
        for x in history
        if x.get(
            "result"
        ) == "STOP_LOSS"
    )

    total_pnl = sum(
        float(
            x.get(
                "pnl_pct",
                0
            )
        )
        for x in history
    )

    message = (

        f"📊 *[SIGNAL TRACKING STATISTICS]*\n"
        f"━━━━━━━━━━━━━━━━━━\n"

        f"▪ 누적 종료: `{total}건`\n"
        f"▪ TP3: `{tp3_count}건`\n"
        f"▪ 보호 종료: `{protected_count}건`\n"
        f"▪ SL: `{sl_count}건`\n"

        f"▪ 단순 누적 손익률: "
        f"`{total_pnl:+.2f}%`\n"

        f"▪ 현재 추적 중: "
        f"`{len(cache.get('positions', {}))}건`\n"

        f"━━━━━━━━━━━━━━━━━━\n"

        f"⚠️ 단순 합산 수치이며 "
        f"실제 포트폴리오 수익률과는 다를 수 있습니다."
    )

    send_telegram_message(
        message
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 65)

    print(
        "UPBIT SPOT PROFIT TRACKING BOT"
    )

    print("=" * 65)

    # --------------------------------------------------------
    # Telegram 상태
    # --------------------------------------------------------

    telegram_status()

    # --------------------------------------------------------
    # Cache
    # --------------------------------------------------------

    cache = load_cache()

    # ========================================================
    # STEP 1
    # ========================================================

    print(
        "\n[STEP 1] 기존 포지션 추적"
    )

    track_positions(
        cache
    )

    # cache 다시 로드

    cache = load_cache()

    # ========================================================
    # STEP 2
    # ========================================================

    print(
        "\n[STEP 2] 신규 매수 신호 탐색"
    )

    scan_new_signals(
        cache
    )

    # ========================================================
    # SAVE
    # ========================================================

    save_cache(
        cache
    )

    print(
        "\n" + "=" * 65
    )

    print(
        "BOT FINISHED"
    )

    print("=" * 65)


# ============================================================
# RUN
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
            f"[FATAL ERROR] {e}"
        )
