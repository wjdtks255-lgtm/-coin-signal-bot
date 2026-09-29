import json
import os
import time
import requests
import numpy as np

from datetime import datetime, timezone


# ============================================================
# UPBIT SPOT SMART SIGNAL BOT
# ============================================================
#
# ALL KRW MARKET SCANNER
#
# 1. Scan ALL KRW markets
# 2. Fast market-wide candidate scan
# 3. Deep analysis only for promising candidates
# 4. BTC is NOT an absolute altcoin filter
# 5. BTC crash blocks new entries
# 6. BTC weak requires stronger individual coins
# 7. TP1 -> SL ENTRY
# 8. TP2 -> SL TP1
# 9. TP3 -> close
# ============================================================


# ============================================================
# FILE
# ============================================================

CACHE_FILE = "tracked_coins.json"


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# ============================================================
# UPBIT API
# ============================================================

UPBIT_BASE_URL = "https://api.upbit.com/v1"

session = requests.Session()

session.headers.update({
    "User-Agent": "Upbit-All-Market-Smart-Signal-Bot/5.0"
})


# ============================================================
# MARKET SCAN
# ============================================================

# 전체 KRW 시장을 1차 검사한다.
#
# 단, 너무 거래량이 적은 종목은 최종 신호에서 제외한다.
#
# 이 값은 "스캔 제외 기준"이 아니라
# "최종 후보의 최소 유동성 기준"에 가깝게 사용한다.

MIN_FINAL_24H_TRADE_VALUE = 1_000_000_000

# 전체 시장 중 1차 후보를 몇 개까지 정밀분석할지
MAX_DEEP_SCAN = 80


# ============================================================
# SIGNAL QUALITY
# ============================================================

MIN_SCORE = 75

WEAK_BTC_MIN_SCORE = 82

STRONG_SCORE = 88

MIN_VOLUME_RATIO = 160

WEAK_BTC_VOLUME_RATIO = 220

STRONG_VOLUME_RATIO = 300

MIN_BODY_RATIO = 0.50

MAX_ENTRY_DISTANCE_FROM_EMA20 = 4.5


# ============================================================
# MOMENTUM PRE-SCAN
# ============================================================

# 24시간 상승률이 이 값보다 낮아도
# 다른 조건이 강하면 후보가 될 수 있도록 너무 높게 잡지 않는다.

MIN_24H_CHANGE = -3.0

# 단기 급락 종목 제외

MAX_24H_CRASH = -20.0


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
# BTC
# ============================================================

BTC_15M_CRASH_PCT = -1.5

BTC_1H_CRASH_PCT = -2.0


# ============================================================
# TRACKING
# ============================================================

MOVE_SL_TO_ENTRY_AFTER_TP1 = True

MOVE_SL_TO_TP1_AFTER_TP2 = True


# ============================================================
# API GET
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
# TELEGRAM STATUS
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


# ============================================================
# TELEGRAM SEND
# ============================================================

def send_telegram_message(text):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:

        print(
            "[TELEGRAM] 환경변수가 없습니다."
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
                f"[TELEGRAM ERROR] {result}"
            )

            return False

        print(
            "[TELEGRAM] 전송 성공"
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

    if not os.path.exists(
        CACHE_FILE
    ):

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

        if not isinstance(
            data,
            dict
        ):

            data = {}

        data.setdefault(
            "positions",
            {}
        )

        data.setdefault(
            "history",
            []
        )

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
            f"[CACHE SAVE ERROR] {e}"
        )


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
# EMA
# ============================================================

def ema(values, period):

    values = np.asarray(
        values,
        dtype=float
    )

    if len(values) < period:

        return None

    alpha = 2 / (
        period + 1
    )

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
            +
            (1 - alpha)
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

    if len(closes) < (
        period + 1
    ):

        return None

    prev_close = closes[:-1]

    tr1 = (
        highs[1:]
        - lows[1:]
    )

    tr2 = np.abs(
        highs[1:]
        - prev_close
    )

    tr3 = np.abs(
        lows[1:]
        - prev_close
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
# MARKET LIST
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

    markets = {}

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

        markets[
            market
        ] = item.get(
            "korean_name",
            market
        )

    return markets


# ============================================================
# ALL TICKERS
# ============================================================

def get_all_tickers(markets):

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
                "markets":
                ",".join(chunk)
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
            0.15
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

    # 현재 진행 중인 candle 제거

    if len(data) > 2:

        data = data[:-1]

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

    if len(data) > 2:

        data = data[:-1]

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

    candle_range = (
        high - low
    )

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
# BTC MARKET
# ============================================================

def check_btc_market():

    btc15 = fetch_candles(
        "KRW-BTC",
        15,
        100
    )

    btc1h = fetch_candles(
        "KRW-BTC",
        60,
        120
    )

    btc4h = fetch_candles(
        "KRW-BTC",
        240,
        120
    )

    if not all([
        btc15,
        btc1h,
        btc4h
    ]):

        return {
            "status": "UNKNOWN",
            "score": 0,
            "reason": "BTC 데이터 부족"
        }

    close15 = np.array([
        x["trade_price"]
        for x in btc15
    ])

    close1h = np.array([
        x["trade_price"]
        for x in btc1h
    ])

    close4h = np.array([
        x["trade_price"]
        for x in btc4h
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
            "reason": "BTC EMA 부족"
        }

    change15 = (
        (
            close15[-1]
            - close15[-2]
        )
        / close15[-2]
    ) * 100

    change1h = (
        (
            close1h[-1]
            - close1h[-5]
        )
        / close1h[-5]
    ) * 100

    score = 0

    if (
        close4h[-1]
        > ema20_4h[-1]
    ):

        score += 35

    if (
        ema20_1h[-1]
        > ema50_1h[-1]
    ):

        score += 35

    if (
        close1h[-1]
        > ema20_1h[-1]
    ):

        score += 20

    if change15 > -0.5:

        score += 10

    # ========================================================
    # CRASH
    # ========================================================

    if change15 <= BTC_15M_CRASH_PCT:

        return {
            "status": "CRASH",
            "score": score,
            "reason":
                f"BTC 15M 급락 {change15:.2f}%",
            "change15": change15,
            "change1h": change1h
        }

    if (
        change1h <= BTC_1H_CRASH_PCT
        and
        change15 < -0.8
    ):

        return {
            "status": "CRASH",
            "score": score,
            "reason":
                f"BTC 1H 급락 {change1h:.2f}%",
            "change15": change15,
            "change1h": change1h
        }

    # ========================================================
    # STATUS
    # ========================================================

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
# FAST MARKET-WIDE PRE-SCAN
# ============================================================

def build_candidates(
    tickers,
    market_names
):

    candidates = []

    print(
        f"\n[MARKET] "
        f"전체 KRW 마켓 {len(market_names)}개 확인"
    )

    for ticker in tickers:

        market = ticker.get(
            "market"
        )

        if not market:

            continue

        if market not in market_names:

            continue

        price = ticker.get(
            "trade_price",
            0
        )

        trade_value = ticker.get(
            "acc_trade_price_24h",
            0
        )

        change_pct = (
            ticker.get(
                "signed_change_rate",
                0
            )
            * 100
        )

        volume_24h = ticker.get(
            "acc_trade_volume_24h",
            0
        )

        # ----------------------------------------------------
        # 기본 이상치 제거
        # ----------------------------------------------------

        if price <= 0:

            continue

        if change_pct <= MAX_24H_CRASH:

            continue

        if change_pct < MIN_24H_CHANGE:

            continue

        # ----------------------------------------------------
        # 1차 점수
        # ----------------------------------------------------

        fast_score = 0

        reasons = []

        # 상승률

        if change_pct >= 3:

            fast_score += 15

            reasons.append(
                "24H 강세"
            )

        elif change_pct >= 1:

            fast_score += 10

            reasons.append(
                "24H 상승"
            )

        elif change_pct >= 0:

            fast_score += 5

            reasons.append(
                "24H 플러스"
            )

        # 거래대금

        if trade_value >= 50_000_000_000:

            fast_score += 20

            reasons.append(
                "거래대금 매우 큼"
            )

        elif trade_value >= 10_000_000_000:

            fast_score += 15

            reasons.append(
                "거래대금 충분"
            )

        elif trade_value >= 3_000_000_000:

            fast_score += 10

            reasons.append(
                "거래대금 양호"
            )

        elif trade_value >= MIN_FINAL_24H_TRADE_VALUE:

            fast_score += 5

        # ----------------------------------------------------
        # 가격 변동
        # ----------------------------------------------------

        if abs(change_pct) >= 5:

            fast_score += 5

            reasons.append(
                "강한 변동성"
            )

        # ----------------------------------------------------
        # 후보
        # ----------------------------------------------------

        candidates.append({

            "market": market,

            "name": market_names[
                market
            ],

            "price": price,

            "trade_value": trade_value,

            "change_pct": change_pct,

            "volume_24h": volume_24h,

            "fast_score": fast_score,

            "reasons": reasons
        })

    # --------------------------------------------------------
    # 전체 시장을 빠른 점수 순으로 정렬
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: (
            x["fast_score"],
            x["trade_value"],
            x["change_pct"]
        ),
        reverse=True
    )

    print(
        f"[MARKET] "
        f"1차 후보 {len(candidates)}개"
    )

    # 상위 후보 출력

    preview = candidates[:20]

    print(
        "\n[TOP CANDIDATES]"
    )

    for i, item in enumerate(
        preview,
        start=1
    ):

        print(
            f"{i:02d}. "
            f"{item['name']} "
            f"{item['market']} "
            f"| 24H "
            f"{item['change_pct']:+.2f}% "
            f"| Score "
            f"{item['fast_score']} "
            f"| "
            f"{item['trade_value']/100_000_000:.1f}억"
        )

    return candidates


# ============================================================
# DEEP ANALYSIS
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

    d20 = ema(
        d_close,
        20
    )

    d50 = ema(
        d_close,
        50
    )

    h420 = ema(
        h4_close,
        20
    )

    h450 = ema(
        h4_close,
        50
    )

    h120 = ema(
        h1_close,
        20
    )

    h150 = ema(
        h1_close,
        50
    )

    m1520 = ema(
        m15_close,
        20
    )

    if any(
        x is None
        for x in [
            d20,
            d50,
            h420,
            h450,
            h120,
            h150,
            m1520
        ]
    ):

        return None

    score = 0

    reasons = []

    # ========================================================
    # DAILY
    # ========================================================

    if d_close[-1] > d20[-1]:

        score += 8

        reasons.append(
            "1D EMA20 위"
        )

    else:

        return None

    if d20[-1] > d50[-1]:

        score += 8

        reasons.append(
            "1D 정배열"
        )

    else:

        return None

    if d20[-1] > d20[-4]:

        score += 4

        reasons.append(
            "1D 상승"
        )

    # ========================================================
    # 4H
    # ========================================================

    if h4_close[-1] > h420[-1]:

        score += 8

        reasons.append(
            "4H EMA20 위"
        )

    else:

        return None

    if h420[-1] > h450[-1]:

        score += 8

        reasons.append(
            "4H 정배열"
        )

    else:

        return None

    if h420[-1] > h420[-4]:

        score += 4

        reasons.append(
            "4H 상승"
        )

    # ========================================================
    # 1H
    # ========================================================

    if h1_close[-1] > h120[-1]:

        score += 6

        reasons.append(
            "1H EMA20 위"
        )

    else:

        return None

    if h120[-1] > h150[-1]:

        score += 5

        reasons.append(
            "1H 정배열"
        )

    if h120[-1] > h120[-4]:

        score += 4

        reasons.append(
            "1H 상승"
        )

    # ========================================================
    # 15M
    # ========================================================

    if m15_close[-1] > m1520[-1]:

        score += 5

        reasons.append(
            "15M EMA20 위"
        )

    else:

        return None

    if m1520[-1] > m1520[-4]:

        score += 4

        reasons.append(
            "15M 상승"
        )

    # ========================================================
    # VOLUME
    # ========================================================

    if len(
        m15_volume
    ) < 25:

        return None

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

    if volume_ratio >= STRONG_VOLUME_RATIO:

        score += 10

        reasons.append(
            f"거래량 폭증 {volume_ratio:.0f}%"
        )

    elif volume_ratio >= 220:

        score += 8

        reasons.append(
            f"거래량 강세 {volume_ratio:.0f}%"
        )

    else:

        score += 5

        reasons.append(
            f"거래량 증가 {volume_ratio:.0f}%"
        )

    # ========================================================
    # CANDLE
    # ========================================================

    body_ratio, close_position = (
        candle_quality(
            m15[-1]
        )
    )

    opening = m15[-1][
        "opening_price"
    ]

    closing = m15[-1][
        "trade_price"
    ]

    if (
        closing > opening
        and
        body_ratio >= MIN_BODY_RATIO
        and
        close_position >= 0.68
    ):

        score += 8

        reasons.append(
            "강한 15M 양봉"
        )

    else:

        return None

    # ========================================================
    # BREAKOUT
    # ========================================================

    previous_high = np.max(
        m15_close[-21:-1]
    )

    breakout = (
        m15_close[-1]
        >= previous_high
    )

    if breakout:

        score += 8

        reasons.append(
            "15M 고점 돌파"
        )

    # ========================================================
    # SHORT TERM MOMENTUM
    # ========================================================

    change_1h = (
        (
            h1_close[-1]
            - h1_close[-5]
        )
        / h1_close[-5]
    ) * 100

    change_4h = (
        (
            h4_close[-1]
            - h4_close[-5]
        )
        / h4_close[-5]
    ) * 100

    if (
        change_1h >= 1.0
    ):

        score += 4

        reasons.append(
            "1H 모멘텀"
        )

    if (
        change_4h >= 2.0
    ):

        score += 4

        reasons.append(
            "4H 모멘텀"
        )

    # ========================================================
    # CHASING FILTER
    # ========================================================

    distance = (
        (
            current_price
            - m1520[-1]
        )
        / m1520[-1]
    ) * 100

    if distance > MAX_ENTRY_DISTANCE_FROM_EMA20:

        return None

    # ========================================================
    # OVERHEATED FILTER
    # ========================================================

    if change_4h >= 15:

        return None

    # ========================================================
    # BTC WEAK
    # ========================================================

    if btc_status == "WEAK":

        # BTC 약세에서는
        # 거래량 + 돌파를 반드시 요구

        if (
            volume_ratio
            < WEAK_BTC_VOLUME_RATIO
        ):

            return None

        if not breakout:

            return None

        score += 5

        reasons.append(
            "BTC 약세 속 상대강도"
        )

    elif btc_status == "BULL":

        score += 3

        reasons.append(
            "BTC 강세 환경"
        )

    elif btc_status == "NEUTRAL":

        reasons.append(
            "BTC 중립 환경"
        )

    # ========================================================
    # FINAL SCORE
    # ========================================================

    required_score = MIN_SCORE

    if btc_status == "WEAK":

        required_score = (
            WEAK_BTC_MIN_SCORE
        )

    if score < required_score:

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

    stop_loss = round_upbit_tick(
        swing_low
        - atr_value * 0.15
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

    target_1 = round_upbit_tick(
        max(
            resistance_1,
            min_tp1
        )
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

    target_2 = round_upbit_tick(
        max(
            resistance_2,
            current_price
            + risk * MIN_RR_TP2,
            target_1 * 1.015
        )
    )

    rr_tp2 = (
        target_2
        - current_price
    ) / risk

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

    return {

        "score": score,

        "current_price": current_price,

        "stop_loss": stop_loss,

        "original_stop_loss":
            stop_loss,

        "target_1": target_1,

        "target_2": target_2,

        "target_3": target_3,

        "tp1_pct": tp1_pct,

        "stop_loss_pct":
            stop_loss_pct,

        "rr_tp1": rr_tp1,

        "rr_tp2": rr_tp2,

        "rr_tp3": rr_tp3,

        "volume_ratio":
            volume_ratio,

        "distance_from_ema20":
            distance,

        "change_1h":
            change_1h,

        "change_4h":
            change_4h,

        "breakout":
            breakout,

        "btc_status":
            btc_status,

        "candle_time":
            m15[-1].get(
                "candle_date_time_kst",
                ""
            ),

        "reasons":
            reasons
    }


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
    item,
    result,
    cache
):

    ticker = item[
        "market"
    ]

    name = item[
        "name"
    ]

    trade_value = item[
        "trade_value"
    ]

    entry = result[
        "current_price"
    ]

    now = datetime.now(
        timezone.utc
    ).isoformat()

    position = {

        "ticker":
            ticker,

        "korean_name":
            name,

        "entry":
            entry,

        "original_sl":
            result[
                "original_stop_loss"
            ],

        "current_sl":
            result[
                "stop_loss"
            ],

        "tp1":
            result[
                "target_1"
            ],

        "tp2":
            result[
                "target_2"
            ],

        "tp3":
            result[
                "target_3"
            ],

        "score":
            result[
                "score"
            ],

        "volume_ratio":
            result[
                "volume_ratio"
            ],

        "rr_tp1":
            result[
                "rr_tp1"
            ],

        "rr_tp2":
            result[
                "rr_tp2"
            ],

        "rr_tp3":
            result[
                "rr_tp3"
            ],

        "stage":
            "ENTRY",

        "entry_time":
            now,

        "signal_candle":
            result[
                "candle_time"
            ],

        "btc_status":
            result[
                "btc_status"
            ]
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

    if volume_ratio >= 300:

        volume_text = (
            "💥 폭발"
        )

    elif volume_ratio >= 220:

        volume_text = (
            "🔥 강한 유입"
        )

    else:

        volume_text = (
            "⚡ 증가"
        )

    btc_status = result[
        "btc_status"
    ]

    if btc_status == "BULL":

        btc_text = "🟢 강세"

    elif btc_status == "NEUTRAL":

        btc_text = "🟡 중립"

    elif btc_status == "WEAK":

        btc_text = (
            "🟠 약세 속 개별강세"
        )

    else:

        btc_text = "⚪ 확인불가"

    breakout_text = (
        "고점 돌파"
        if result["breakout"]
        else
        "추세 강화"
    )

    reasons = ", ".join(
        result["reasons"]
    )

    upbit_url = (
        "https://upbit.com/exchange"
        f"?code=CRIX.UPBIT.{ticker}"
    )

    message = (

        f"🚀 *[UPBIT BUY SIGNAL]*\n"
        f"━━━━━━━━━━━━━━━━━━\n"

        f"▪ 코인: "
        f"`{name} ({ticker})`\n"

        f"▪ ENTRY: "
        f"`{format_price(entry)}원`\n"

        f"▪ 24H 거래대금: "
        f"`{trade_value / 100_000_000:,.1f}억`\n"

        f"▪ 24H 변화: "
        f"`{item['change_pct']:+.2f}%`\n\n"

        f"🌐 *MARKET*\n"

        f"▪ BTC: "
        f"`{btc_text}`\n"

        f"▪ 상태: "
        f"`{breakout_text}`\n\n"

        f"📊 *SIGNAL QUALITY*\n"

        f"▪ Score: "
        f"`{result['score']}/100`\n"

        f"▪ 15M 거래량: "
        f"`{volume_ratio:.0f}%` "
        f"{volume_text}\n"

        f"▪ 1H 변화: "
        f"`{result['change_1h']:+.2f}%`\n"

        f"▪ 4H 변화: "
        f"`{result['change_4h']:+.2f}%`\n\n"

        f"🎯 *TARGETS*\n"

        f"▪ TP1: "
        f"`{format_price(result['target_1'])}원` "
        f"(+{result['tp1_pct']:.1f}%)\n"

        f"▪ TP2: "
        f"`{format_price(result['target_2'])}원`\n"

        f"▪ TP3: "
        f"`{format_price(result['target_3'])}원`\n\n"

        f"🛡️ *RISK*\n"

        f"▪ SL: "
        f"`{format_price(result['stop_loss'])}원` "
        f"(-{result['stop_loss_pct']:.1f}%)\n"

        f"▪ TP1 R:R: "
        f"`1:{result['rr_tp1']:.1f}`\n"

        f"▪ TP2 R:R: "
        f"`1:{result['rr_tp2']:.1f}`\n"

        f"▪ TP3 R:R: "
        f"`1:{result['rr_tp3']:.1f}`\n\n"

        f"🔎 *CONFIRMATION*\n"
        f"`{reasons}`\n\n"

        f"📱 [업비트 차트]({upbit_url})\n"

        f"━━━━━━━━━━━━━━━━━━\n"

        f"📌 TP1 도달 → SL ENTRY\n"
        f"📌 TP2 도달 → SL TP1\n"
        f"📌 TP3 도달 → 추적 종료"
    )

    sent = send_telegram_message(
        message
    )

    if sent:

        print(
            f"  📲 TELEGRAM SIGNAL SENT "
            f"{ticker}"
        )

    else:

        print(
            f"  ❌ TELEGRAM SEND FAILED "
            f"{ticker}"
        )


# ============================================================
# NEW SIGNAL SCAN
# ============================================================

def scan_new_signals(cache):

    # ========================================================
    # BTC
    # ========================================================

    btc = check_btc_market()

    btc_status = btc.get(
        "status",
        "UNKNOWN"
    )

    print(
        f"\n[BTC] "
        f"{btc.get('reason', 'UNKNOWN')}"
    )

    # ========================================================
    # CRASH
    # ========================================================

    if btc_status == "CRASH":

        print(
            "🚫 BTC 급락"
        )

        print(
            "신규 진입 차단"
        )

        return

    if btc_status == "UNKNOWN":

        print(
            "🚫 BTC 상태 확인 실패"
        )

        return

    if btc_status == "WEAK":

        print(
            "⚠️ BTC 약세"
        )

        print(
            "개별 강세 코인만 허용"
        )

    elif btc_status == "NEUTRAL":

        print(
            "🟡 BTC 중립"
        )

    elif btc_status == "BULL":

        print(
            "🟢 BTC 강세"
        )

    # ========================================================
    # ALL MARKETS
    # ========================================================

    market_names = get_market_names()

    if not market_names:

        print(
            "[ERROR] KRW 마켓 조회 실패"
        )

        return

    markets = list(
        market_names.keys()
    )

    print(
        f"\n[SCAN] "
        f"업비트 KRW 전체 "
        f"{len(markets)}개 마켓"
    )

    # ========================================================
    # ALL TICKERS
    # ========================================================

    tickers = get_all_tickers(
        markets
    )

    if not tickers:

        print(
            "[ERROR] ticker 조회 실패"
        )

        return

    # ========================================================
    # FAST SCAN
    # ========================================================

    candidates = build_candidates(
        tickers,
        market_names
    )

    if not candidates:

        print(
            "[SCAN] 후보 없음"
        )

        return

    # ========================================================
    # DEEP SCAN
    # ========================================================

    deep_candidates = candidates[
        :MAX_DEEP_SCAN
    ]

    print(
        f"\n[DEEP SCAN] "
        f"{len(deep_candidates)}개 정밀분석"
    )

    signal_count = 0

    rejected_count = 0

    for index, item in enumerate(
        deep_candidates,
        start=1
    ):

        ticker = item[
            "market"
        ]

        name = item[
            "name"
        ]

        price = item[
            "price"
        ]

        print(
            f"[{index}/{len(deep_candidates)}] "
            f"{name} {ticker} "
            f"| 24H "
            f"{item['change_pct']:+.2f}% "
            f"| FAST "
            f"{item['fast_score']}"
        )

        try:

            result = analyze_coin(
                ticker,
                price,
                btc_status
            )

            if not result:

                rejected_count += 1

                print(
                    "   → 정밀조건 FAIL"
                )

                time.sleep(
                    0.10
                )

                continue

            print(
                f"   → "
                f"FINAL SCORE "
                f"{result['score']} "
                f"| VOL "
                f"{result['volume_ratio']:.0f}% "
                f"| RR "
                f"{result['rr_tp1']:.2f}"
            )

            # ------------------------------------------------
            # Duplicate
            # ------------------------------------------------

            if not can_create_new_signal(
                ticker,
                result,
                cache
            ):

                print(
                    "   → "
                    "중복/쿨다운"
                )

                continue

            # ------------------------------------------------
            # FINAL SIGNAL
            # ------------------------------------------------

            create_position(
                item,
                result,
                cache
            )

            signal_count += 1

            print(
                f"   🚀 "
                f"FINAL SIGNAL "
                f"{ticker}"
            )

        except Exception as e:

            print(
                f"   [ANALYSIS ERROR] "
                f"{ticker}: {e}"
            )

        time.sleep(
            0.12
        )

    print(
        "\n=========================================="
    )

    print(
        f"[SCAN COMPLETE]"
    )

    print(
        f"전체 KRW 마켓: "
        f"{len(markets)}"
    )

    print(
        f"1차 후보: "
        f"{len(candidates)}"
    )

    print(
        f"정밀 분석: "
        f"{len(deep_candidates)}"
    )

    print(
        f"최종 신호: "
        f"{signal_count}"
    )

    print(
        f"정밀 탈락: "
        f"{rejected_count}"
    )

    print(
        "=========================================="
    )


# ============================================================
# 1M CANDLES
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
# TRACK POSITIONS
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
        f"{len(positions)}개 포지션"
    )

    completed = []

    for ticker in list(
        positions.keys()
    ):

        position = positions[
            ticker
        ]

        try:

            ticker_data = api_get(
                "/ticker",
                {
                    "markets":
                    ticker
                }
            )

            if (
                not isinstance(
                    ticker_data,
                    list
                )
                or
                not ticker_data
            ):

                continue

            current_price = (
                ticker_data[0]
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

            stage = position.get(
                "stage",
                "ENTRY"
            )

            hit_sl = False
            hit_tp1 = False
            hit_tp2 = False
            hit_tp3 = False

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
            # SL
            # =================================================

            if hit_sl:

                pnl_pct = (
                    (
                        sl - entry
                    )
                    / entry
                ) * 100

                if stage == "ENTRY":

                    result_type = (
                        "STOP_LOSS"
                    )

                    emoji = "🛑"

                else:

                    result_type = (
                        "PROTECTED"
                    )

                    emoji = "🛡️"

                message = (

                    f"{emoji} *[POSITION CLOSED]*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"

                    f"▪ 자산: `{ticker}`\n"

                    f"▪ ENTRY: "
                    f"`{format_price(entry)}원`\n"

                    f"▪ EXIT: "
                    f"`{format_price(sl)}원`\n"

                    f"▪ 결과: "
                    f"`{result_type}`\n"

                    f"▪ 손익률: "
                    f"`{pnl_pct:+.2f}%`\n"

                    f"▪ 단계: "
                    f"`{stage}`\n"

                    f"━━━━━━━━━━━━━━━━━━"
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
                stage == "TP1"
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

                    f"▪ TP2: "
                    f"`{format_price(tp2)}원`\n"

                    f"▪ 수익률: "
                    f"`+{pnl_pct:.2f}%`\n"

                    f"▪ 보호 SL: "
                    f"`{format_price(tp1)}원`\n\n"

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

                    f"▪ TP1: "
                    f"`{format_price(tp1)}원`\n"

                    f"▪ 수익률: "
                    f"`+{pnl_pct:.2f}%`\n"

                    f"▪ 보호 SL: "
                    f"`{format_price(entry)}원`\n\n"

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

    history.append({

        "ticker":
            ticker,

        "entry":
            position.get(
                "entry"
            ),

        "exit":
            exit_price,

        "result":
            result_type,

        "pnl_pct":
            pnl_pct,

        "score":
            position.get(
                "score"
            ),

        "entry_time":
            position.get(
                "entry_time"
            ),

        "exit_time":
            datetime.now(
                timezone.utc
            ).isoformat()
    })

    cache[
        "history"
    ] = history[-500:]


# ============================================================
# STATISTICS
# ============================================================

def send_daily_statistics(cache):

    history = cache.get(
        "history",
        []
    )

    if not history:

        return

    total = len(
        history
    )

    tp3 = sum(
        1
        for x in history
        if x.get(
            "result"
        ) == "TP3"
    )

    protected = sum(
        1
        for x in history
        if x.get(
            "result"
        ) == "PROTECTED"
    )

    sl = sum(
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

        f"📊 *[TRACKING STATISTICS]*\n"
        f"━━━━━━━━━━━━━━━━━━\n"

        f"▪ 종료: `{total}`\n"
        f"▪ TP3: `{tp3}`\n"
        f"▪ 보호종료: `{protected}`\n"
        f"▪ SL: `{sl}`\n"
        f"▪ 단순 누적: `{total_pnl:+.2f}%`\n"
        f"▪ 추적중: "
        f"`{len(cache.get('positions', {}))}`\n"

        f"━━━━━━━━━━━━━━━━━━\n"

        f"⚠️ 실제 포트폴리오 수익률과는 다를 수 있습니다."
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
        "UPBIT ALL-MARKET SMART SIGNAL BOT"
    )

    print("=" * 65)

    telegram_status()

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

    cache = load_cache()

    # ========================================================
    # STEP 2
    # ========================================================

    print(
        "\n[STEP 2] 업비트 전체 시장 신규 신호 탐색"
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
