import os
import time
import json
import math
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ============================================================
# UPBIT SMART SIGNAL BOT V4
# SCORE-BASED ALTMARKET SIGNAL SYSTEM
# ============================================================

print("=" * 65)
print("UPBIT SMART SIGNAL BOT V4")
print("=" * 65)

# ============================================================
# CONFIG
# ============================================================

CACHE_FILE = "tracked_coins.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

UPBIT_BASE_URL = "https://api.upbit.com/v1"

# ------------------------------------------------------------
# MARKET SCAN
# ------------------------------------------------------------

MAX_DEEP_SCAN = 80

# 최소 24H 거래대금
MIN_FINAL_24H_TRADE_VALUE = 1_000_000_000

# ------------------------------------------------------------
# SIGNAL SCORE
# ------------------------------------------------------------

SIGNAL_SCORE = 75

# BTC 약세일 때 필요한 최소 점수
WEAK_BTC_SCORE = 82

# 아주 강한 신호
STRONG_SCORE = 88

# ------------------------------------------------------------
# VOLUME
# ------------------------------------------------------------

MIN_VOLUME_RATIO = 130
STRONG_VOLUME_RATIO = 200

# ------------------------------------------------------------
# CANDLE
# ------------------------------------------------------------

MIN_BODY_RATIO = 0.45
MIN_CLOSE_POSITION = 0.62

# ------------------------------------------------------------
# ENTRY / RISK
# ------------------------------------------------------------

MAX_ENTRY_DISTANCE_FROM_EMA20 = 6.0

MIN_STOP_LOSS_PCT = 1.0
MAX_STOP_LOSS_PCT = 7.0

MIN_RR_TP1 = 1.5
MIN_RR_TP2 = 2.0

MAX_TP1_DISTANCE_PCT = 15.0

# ------------------------------------------------------------
# SIGNAL COOLDOWN
# ------------------------------------------------------------

SIGNAL_COOLDOWN_HOURS = 6

# 같은 코인이라도 가격이 일정 이상 달라야 새 신호
MIN_NEW_SIGNAL_PRICE_DISTANCE = 2.0

# ------------------------------------------------------------
# BTC MARKET
# ------------------------------------------------------------

BTC_15M_CRASH_PCT = -1.5
BTC_1H_CRASH_PCT = -2.0

# ------------------------------------------------------------
# POSITION MANAGEMENT
# ------------------------------------------------------------

MOVE_SL_TO_ENTRY_AFTER_TP1 = True
MOVE_SL_TO_TP1_AFTER_TP2 = True

# ============================================================
# SESSION
# ============================================================

session = requests.Session()
session.headers.update({
    "User-Agent": "UpbitSmartSignalBot/4.0"
})

KST = timezone(timedelta(hours=9))


# ============================================================
# UTILITY
# ============================================================

def now_kst():
    return datetime.now(KST)


def fmt_time(dt=None):
    if dt is None:
        dt = now_kst()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {
            "positions": {},
            "history": [],
            "sent_signal_ids": []
        }

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            data = {}

        data.setdefault("positions", {})
        data.setdefault("history", [])
        data.setdefault("sent_signal_ids", [])

        return data

    except Exception as e:
        print(f"[CACHE LOAD ERROR] {e}")

        return {
            "positions": {},
            "history": [],
            "sent_signal_ids": []
        }


def save_cache(cache):
    temp_file = CACHE_FILE + ".tmp"

    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

        os.replace(temp_file, CACHE_FILE)

    except Exception as e:
        print(f"[CACHE SAVE ERROR] {e}")


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] 환경변수가 없습니다.")
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

        result = response.json()

        if not result.get("ok"):
            print(f"[TELEGRAM ERROR] {result}")
            return False

        print("[TELEGRAM] 전송 성공")
        return True

    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return False


def telegram_status():
    token_status = "OK" if TELEGRAM_TOKEN else "MISSING"
    chat_status = "OK" if TELEGRAM_CHAT_ID else "MISSING"

    print(
        f"[TELEGRAM] TOKEN={token_status} "
        f"CHAT_ID={chat_status}"
    )


# ============================================================
# UPBIT API
# ============================================================

def upbit_get(endpoint, params=None, timeout=15):

    url = UPBIT_BASE_URL + endpoint

    try:
        response = session.get(
            url,
            params=params,
            timeout=timeout
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:
        print(f"[UPBIT ERROR] {endpoint} | {e}")
        return None


# ============================================================
# MARKETS
# ============================================================

def get_market_names():

    data = upbit_get(
        "/market/all",
        {"is_details": "true"}
    )

    if not data:
        return []

    markets = []

    for item in data:
        market = item.get("market", "")

        if market.startswith("KRW-"):
            markets.append(market)

    return sorted(list(set(markets)))


def get_all_tickers(markets):

    results = []

    for i in range(0, len(markets), 100):

        chunk = markets[i:i + 100]

        data = upbit_get(
            "/ticker",
            {"markets": ",".join(chunk)}
        )

        if data:
            results.extend(data)

        time.sleep(0.12)

    # 중복 제거
    unique = {}

    for item in results:
        market = item.get("market")

        if market:
            unique[market] = item

    return list(unique.values())


# ============================================================
# CANDLES
# ============================================================

def get_candles(market, unit, count=120):

    endpoint = f"/candles/{unit}"

    data = upbit_get(
        endpoint,
        {
            "market": market,
            "count": count
        }
    )

    if not data:
        return None

    df = pd.DataFrame(data)

    if df.empty:
        return None

    df = df.sort_values(
        "candle_date_time_utc"
    ).reset_index(drop=True)

    numeric_columns = [
        "opening_price",
        "high_price",
        "low_price",
        "trade_price",
        "candle_acc_trade_volume",
        "candle_acc_trade_price"
    ]

    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

    return df


# ============================================================
# INDICATORS
# ============================================================

def ema(series, length):

    return series.ewm(
        span=length,
        adjust=False
    ).mean()


def atr(df, length=14):

    high = df["high_price"]
    low = df["low_price"]
    close = df["trade_price"]

    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    return tr.rolling(length).mean()


def rsi(series, length=14):

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(length).mean()
    avg_loss = loss.rolling(length).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    result = 100 - (
        100 / (1 + rs)
    )

    return result


# ============================================================
# BTC REGIME
# ============================================================

def check_btc_market():

    try:

        df15 = get_candles(
            "KRW-BTC",
            "minutes/15",
            100
        )

        df1h = get_candles(
            "KRW-BTC",
            "minutes/60",
            100
        )

        df4h = get_candles(
            "KRW-BTC",
            "minutes/240",
            100
        )

        if any(
            x is None
            for x in [df15, df1h, df4h]
        ):
            return {
                "regime": "NEUTRAL",
                "score": 50,
                "change15": 0,
                "change1h": 0
            }

        c15 = df15["trade_price"].iloc[-1]
        c15_prev = df15["trade_price"].iloc[-2]

        c1h = df1h["trade_price"].iloc[-1]
        c1h_prev = df1h["trade_price"].iloc[-2]

        ema20_1h = ema(
            df1h["trade_price"],
            20
        )

        ema50_1h = ema(
            df1h["trade_price"],
            50
        )

        ema20_4h = ema(
            df4h["trade_price"],
            20
        )

        change15 = (
            c15 / c15_prev - 1
        ) * 100

        change1h = (
            c1h / c1h_prev - 1
        ) * 100

        score = 0

        if (
            df4h["trade_price"].iloc[-1]
            > ema20_4h.iloc[-1]
        ):
            score += 30

        if (
            ema20_1h.iloc[-1]
            > ema50_1h.iloc[-1]
        ):
            score += 30

        if (
            c1h > ema20_1h.iloc[-1]
        ):
            score += 20

        if change15 > -0.5:
            score += 10

        if change15 > 0:
            score += 10

        crash = False

        if change15 <= BTC_15M_CRASH_PCT:
            crash = True

        if (
            change1h <= BTC_1H_CRASH_PCT
            and change15 < -0.8
        ):
            crash = True

        if crash:
            regime = "CRASH"

        elif score >= 70:
            regime = "BULL"

        elif score >= 45:
            regime = "NEUTRAL"

        else:
            regime = "WEAK"

        return {
            "regime": regime,
            "score": score,
            "change15": change15,
            "change1h": change1h
        }

    except Exception as e:

        print(f"[BTC ERROR] {e}")

        return {
            "regime": "NEUTRAL",
            "score": 50,
            "change15": 0,
            "change1h": 0
        }


# ============================================================
# FAST CANDIDATE SCORE
# ============================================================

def fast_candidate_score(item):

    change = safe_float(
        item.get("signed_change_rate")
    ) * 100

    trade_value = safe_float(
        item.get("acc_trade_price_24h")
    )

    high = safe_float(
        item.get("high_price")
    )

    low = safe_float(
        item.get("low_price")
    )

    price = safe_float(
        item.get("trade_price")
    )

    score = 0

    if change >= 8:
        score += 40

    elif change >= 5:
        score += 30

    elif change >= 3:
        score += 25

    elif change >= 1:
        score += 20

    elif change >= 0:
        score += 15

    else:
        score += 5

    if trade_value >= 30_000_000_000:
        score += 25

    elif trade_value >= 10_000_000_000:
        score += 20

    elif trade_value >= 3_000_000_000:
        score += 15

    elif trade_value >= 1_000_000_000:
        score += 10

    if price > 0 and low > 0:

        volatility = (
            (high - low) / price
        ) * 100

        if volatility >= 5:
            score += 15

        elif volatility >= 3:
            score += 10

        elif volatility >= 1.5:
            score += 5

    return score


def build_candidates(tickers):

    candidates = []

    for item in tickers:

        market = item.get("market")

        if not market:
            continue

        trade_value = safe_float(
            item.get("acc_trade_price_24h")
        )

        if (
            trade_value
            < MIN_FINAL_24H_TRADE_VALUE
        ):
            continue

        score = fast_candidate_score(item)

        change = safe_float(
            item.get("signed_change_rate")
        ) * 100

        candidates.append({
            "market": market,
            "trade_price": safe_float(
                item.get("trade_price")
            ),
            "change": change,
            "trade_value": trade_value,
            "fast_score": score
        })

    candidates.sort(
        key=lambda x: (
            x["fast_score"],
            x["trade_value"]
        ),
        reverse=True
    )

    return candidates


# ============================================================
# SIGNAL ID
# ============================================================

def get_signal_candle(df15):

    if df15 is None or df15.empty:
        return ""

    # 마지막 완성된 15분봉
    index = -2 if len(df15) >= 3 else -1

    return str(
        df15.iloc[index]["candle_date_time_utc"]
    )


def make_signal_id(
    market,
    signal_candle
):

    return (
        f"{market}|"
        f"{signal_candle}"
    )


# ============================================================
# DUPLICATE PROTECTION
# ============================================================

def signal_already_sent(
    cache,
    signal_id,
    market
):

    sent_ids = cache.get(
        "sent_signal_ids",
        []
    )

    if signal_id in sent_ids:
        return True

    # 현재 포지션 확인
    position = cache.get(
        "positions",
        {}
    ).get(market)

    if position:

        if position.get(
            "signal_id"
        ) == signal_id:
            return True

    # history 확인
    for item in cache.get(
        "history",
        []
    ):

        if item.get(
            "signal_id"
        ) == signal_id:
            return True

    return False


def remember_signal(
    cache,
    signal_id
):

    cache.setdefault(
        "sent_signal_ids",
        []
    )

    if signal_id not in cache["sent_signal_ids"]:

        cache["sent_signal_ids"].append(
            signal_id
        )

    # 최근 500개만 보관
    if len(
        cache["sent_signal_ids"]
    ) > 500:

        cache["sent_signal_ids"] = (
            cache["sent_signal_ids"][-500:]
        )


# ============================================================
# NEW SIGNAL COOLDOWN
# ============================================================

def can_create_new_signal(
    cache,
    market,
    price
):

    positions = cache.get(
        "positions",
        {}
    )

    if market in positions:
        return False, "이미 포지션 보유"

    history = cache.get(
        "history",
        []
    )

    now = now_kst()

    for item in reversed(history):

        if item.get("market") != market:
            continue

        try:

            signal_time = datetime.fromisoformat(
                item.get("entry_time", "")
            )

            if signal_time.tzinfo is None:
                signal_time = signal_time.replace(
                    tzinfo=KST
                )

            hours = (
                now - signal_time
            ).total_seconds() / 3600

            if hours < SIGNAL_COOLDOWN_HOURS:

                return (
                    False,
                    f"쿨다운 {hours:.1f}h"
                )

        except Exception:
            pass

        old_price = safe_float(
            item.get("entry")
        )

        if old_price > 0:

            distance = abs(
                price / old_price - 1
            ) * 100

            if (
                distance
                < MIN_NEW_SIGNAL_PRICE_DISTANCE
            ):

                return (
                    False,
                    f"가격거리 {distance:.2f}%"
                )

        break

    return True, "OK"


# ============================================================
# SCORE ANALYSIS
# ============================================================

def analyze_coin(
    market,
    btc_info
):

    df1d = get_candles(
        market,
        "days",
        120
    )

    df4h = get_candles(
        market,
        "minutes/240",
        120
    )

    df1h = get_candles(
        market,
        "minutes/60",
        120
    )

    df15 = get_candles(
        market,
        "minutes/15",
        120
    )

    if any(
        x is None
        for x in [df1d, df4h, df1h, df15]
    ):
        return None

    if any(
        len(x) < 60
        for x in [df1d, df4h, df1h, df15]
    ):
        return None

    # --------------------------------------------------------
    # 완성봉 기준
    # --------------------------------------------------------

    d = df1d.iloc[:-1]
    h4 = df4h.iloc[:-1]
    h1 = df1h.iloc[:-1]
    m15 = df15.iloc[:-1]

    if any(
        len(x) < 55
        for x in [d, h4, h1, m15]
    ):
        return None

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    d_ema20 = ema(
        d["trade_price"],
        20
    )

    d_ema50 = ema(
        d["trade_price"],
        50
    )

    h4_ema20 = ema(
        h4["trade_price"],
        20
    )

    h4_ema50 = ema(
        h4["trade_price"],
        50
    )

    h1_ema20 = ema(
        h1["trade_price"],
        20
    )

    h1_ema50 = ema(
        h1["trade_price"],
        50
    )

    m15_ema20 = ema(
        m15["trade_price"],
        20
    )

    # --------------------------------------------------------
    # 현재값
    # --------------------------------------------------------

    price = safe_float(
        m15["trade_price"].iloc[-1]
    )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 0
    reasons = []

    # ========================================================
    # 1D TREND = 20
    # ========================================================

    d_price = d["trade_price"].iloc[-1]

    d_trend = 0

    if d_price > d_ema20.iloc[-1]:
        d_trend += 10
        reasons.append("1D EMA20 PASS")
    else:
        reasons.append("1D EMA20 FAIL")

    if d_ema20.iloc[-1] > d_ema50.iloc[-1]:
        d_trend += 5
        reasons.append("1D 정배열 PASS")
    else:
        reasons.append("1D 정배열 FAIL")

    if d_ema20.iloc[-1] > d_ema20.iloc[-4]:
        d_trend += 5
        reasons.append("1D 상승 PASS")
    else:
        reasons.append("1D 상승 FAIL")

    score += d_trend

    # ========================================================
    # 4H TREND = 20
    # ========================================================

    h4_price = h4["trade_price"].iloc[-1]

    h4_trend = 0

    if h4_price > h4_ema20.iloc[-1]:
        h4_trend += 8
        reasons.append("4H EMA20 PASS")
    else:
        reasons.append("4H EMA20 FAIL")

    if h4_ema20.iloc[-1] > h4_ema50.iloc[-1]:
        h4_trend += 7
        reasons.append("4H 정배열 PASS")
    else:
        reasons.append("4H 정배열 FAIL")

    if h4_ema20.iloc[-1] > h4_ema20.iloc[-4]:
        h4_trend += 5
        reasons.append("4H 상승 PASS")
    else:
        reasons.append("4H 상승 FAIL")

    score += h4_trend

    # ========================================================
    # 1H MOMENTUM = 15
    # ========================================================

    h1_price = h1["trade_price"].iloc[-1]

    h1_change = (
        h1_price
        / h1["trade_price"].iloc[-4]
        - 1
    ) * 100

    h1_momentum = 0

    if h1_price > h1_ema20.iloc[-1]:
        h1_momentum += 6
        reasons.append("1H EMA20 PASS")
    else:
        reasons.append("1H EMA20 FAIL")

    if h1_ema20.iloc[-1] > h1_ema50.iloc[-1]:
        h1_momentum += 4
        reasons.append("1H 정배열 PASS")
    else:
        reasons.append("1H 정배열 FAIL")

    if h1_change > 0:
        h1_momentum += 5
        reasons.append(
            f"1H 모멘텀 PASS {h1_change:+.2f}%"
        )
    else:
        reasons.append(
            f"1H 모멘텀 FAIL {h1_change:+.2f}%"
        )

    score += h1_momentum

    # ========================================================
    # 15M MOMENTUM = 15
    # ========================================================

    m15_price = m15["trade_price"].iloc[-1]

    m15_change = (
        m15_price
        / m15["trade_price"].iloc[-4]
        - 1
    ) * 100

    m15_momentum = 0

    if m15_price > m15_ema20.iloc[-1]:
        m15_momentum += 7
        reasons.append("15M EMA20 PASS")
    else:
        reasons.append("15M EMA20 FAIL")

    if m15_ema20.iloc[-1] > m15_ema20.iloc[-4]:
        m15_momentum += 4
        reasons.append("15M EMA 상승 PASS")
    else:
        reasons.append("15M EMA 상승 FAIL")

    if m15_change > 0:
        m15_momentum += 4
        reasons.append(
            f"15M 모멘텀 PASS {m15_change:+.2f}%"
        )
    else:
        reasons.append(
            f"15M 모멘텀 FAIL {m15_change:+.2f}%"
        )

    score += m15_momentum

    # ========================================================
    # VOLUME = 15
    # ========================================================

    volume_now = safe_float(
        m15["candle_acc_trade_volume"].iloc[-1]
    )

    volume_avg = safe_float(
        m15[
            "candle_acc_trade_volume"
        ].iloc[-21:-1].mean()
    )

    if volume_avg > 0:

        volume_ratio = (
            volume_now
            / volume_avg
        ) * 100

    else:
        volume_ratio = 0

    volume_score = 0

    if volume_ratio >= 200:
        volume_score = 15

    elif volume_ratio >= 160:
        volume_score = 12

    elif volume_ratio >= 130:
        volume_score = 9

    elif volume_ratio >= 100:
        volume_score = 5

    reasons.append(
        f"거래량 {volume_ratio:.0f}% "
        f"({volume_score}/15)"
    )

    score += volume_score

    # ========================================================
    # CANDLE = 10
    # ========================================================

    candle = m15.iloc[-1]

    candle_open = safe_float(
        candle["opening_price"]
    )

    candle_high = safe_float(
        candle["high_price"]
    )

    candle_low = safe_float(
        candle["low_price"]
    )

    candle_close = safe_float(
        candle["trade_price"]
    )

    candle_range = (
        candle_high - candle_low
    )

    body = abs(
        candle_close - candle_open
    )

    body_ratio = (
        body / candle_range
        if candle_range > 0
        else 0
    )

    close_position = (
        (candle_close - candle_low)
        / candle_range
        if candle_range > 0
        else 0
    )

    candle_score = 0

    if candle_close > candle_open:
        candle_score += 4

    if body_ratio >= MIN_BODY_RATIO:
        candle_score += 3

    if close_position >= MIN_CLOSE_POSITION:
        candle_score += 3

    reasons.append(
        f"캔들 {candle_score}/10"
    )

    score += candle_score

    # ========================================================
    # BREAKOUT = 10
    # ========================================================

    previous_high = safe_float(
        m15["high_price"].iloc[-21:-1].max()
    )

    breakout_score = 0

    if candle_close > previous_high:
        breakout_score = 10
        reasons.append(
            "15M 돌파 PASS"
        )
    elif candle_high > previous_high:
        breakout_score = 5
        reasons.append(
            "15M 돌파 근접"
        )
    else:
        reasons.append(
            "15M 돌파 FAIL"
        )

    score += breakout_score

    # ========================================================
    # RSI BONUS / PENALTY
    # ========================================================

    rsi_value = safe_float(
        rsi(
            m15["trade_price"],
            14
        ).iloc[-1],
        50
    )

    # 너무 과열된 상태만 약간 감점
    if rsi_value >= 82:
        score -= 5
        reasons.append(
            f"RSI 과열 -5 ({rsi_value:.1f})"
        )

    elif rsi_value >= 70:
        reasons.append(
            f"RSI 강세 ({rsi_value:.1f})"
        )

    elif rsi_value >= 50:
        reasons.append(
            f"RSI 정상 ({rsi_value:.1f})"
        )

    else:
        score -= 2
        reasons.append(
            f"RSI 약세 -2 ({rsi_value:.1f})"
        )

    # ========================================================
    # BTC ENVIRONMENT BONUS
    # ========================================================

    btc_regime = btc_info["regime"]

    if btc_regime == "BULL":
        score += 5
        reasons.append("BTC 상승환경 +5")

    elif btc_regime == "NEUTRAL":
        reasons.append("BTC 중립")

    elif btc_regime == "WEAK":
        score -= 3
        reasons.append("BTC 약세 -3")

    elif btc_regime == "CRASH":
        score -= 15
        reasons.append("BTC 급락 -15")

    # ========================================================
    # 4H CHANGE
    # ========================================================

    h4_change = (
        h4["trade_price"].iloc[-1]
        / h4["trade_price"].iloc[-5]
        - 1
    ) * 100

    # 너무 급등한 코인은 추격 방지
    if h4_change > 18:
        score -= 8
        reasons.append(
            f"4H 과열 -8 ({h4_change:+.2f}%)"
        )

    # ========================================================
    # ENTRY DISTANCE
    # ========================================================

    ema20_now = safe_float(
        m15_ema20.iloc[-1]
    )

    entry_distance = (
        abs(price / ema20_now - 1)
        * 100
        if ema20_now > 0
        else 99
    )

    if entry_distance > MAX_ENTRY_DISTANCE_FROM_EMA20:
        score -= 7
        reasons.append(
            f"EMA20 거리 과다 -7 "
            f"({entry_distance:.2f}%)"
        )
    else:
        reasons.append(
            f"EMA20 거리 OK "
            f"({entry_distance:.2f}%)"
        )

    # ========================================================
    # ATR / RISK
    # ========================================================

    atr_value = safe_float(
        atr(
            m15,
            14
        ).iloc[-1]
    )

    if atr_value <= 0:
        return None

    # 최근 15M swing low
    swing_low = safe_float(
        m15["low_price"].iloc[-12:].min()
    )

    # ATR 기반 보정
    stop_price = min(
        swing_low,
        price - atr_value * 1.5
    )

    if stop_price <= 0:
        return None

    stop_distance = (
        price - stop_price
    )

    stop_pct = (
        stop_distance / price
    ) * 100

    # SL이 너무 넓으면 감점
    risk_score = 5

    if stop_pct > MAX_STOP_LOSS_PCT:
        risk_score = 0
        reasons.append(
            f"SL 과다 FAIL {stop_pct:.2f}%"
        )

    elif stop_pct < MIN_STOP_LOSS_PCT:
        risk_score = 2
        reasons.append(
            f"SL 너무 좁음 {stop_pct:.2f}%"
        )

    else:
        reasons.append(
            f"Risk PASS SL {stop_pct:.2f}%"
        )

    score += risk_score

    # ========================================================
    # TARGETS
    # ========================================================

    tp1 = price + stop_distance * 1.5
    tp2 = price + stop_distance * 2.0
    tp3 = price + stop_distance * 3.0

    tp1_pct = (
        (tp1 / price - 1)
        * 100
    )

    if tp1_pct > MAX_TP1_DISTANCE_PCT:
        score -= 8
        reasons.append(
            f"TP1 거리 과다 -8 "
            f"{tp1_pct:.2f}%"
        )

    # ========================================================
    # SIGNAL CANDLE
    # ========================================================

    signal_candle = get_signal_candle(
        df15
    )

    # ========================================================
    # BTC CRASH SPECIAL RULE
    # ========================================================

    if btc_regime == "CRASH":

        # BTC 급락 중에는 아주 강한 상대강도만 허용
        if score < 88:
            reasons.append(
                "BTC CRASH → 최소 88점 필요"
            )

            return {
                "score": score,
                "signal": False,
                "reason": reasons,
                "price": price,
                "stop": stop_price,
                "tp1": tp1,
                "tp2": tp2,
                "tp3": tp3,
                "stop_pct": stop_pct,
                "tp1_pct": tp1_pct,
                "volume_ratio": volume_ratio,
                "h1_change": h1_change,
                "h4_change": h4_change,
                "rsi": rsi_value,
                "signal_candle": signal_candle
            }

    # ========================================================
    # FINAL SIGNAL
    # ========================================================

    minimum_score = SIGNAL_SCORE

    if btc_regime == "WEAK":
        minimum_score = WEAK_BTC_SCORE

    signal = (
        score >= minimum_score
        and stop_pct <= MAX_STOP_LOSS_PCT
        and tp1_pct <= MAX_TP1_DISTANCE_PCT
    )

    return {
        "score": int(score),
        "signal": signal,
        "reason": reasons,
        "price": price,
        "stop": stop_price,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "stop_pct": stop_pct,
        "tp1_pct": tp1_pct,
        "volume_ratio": volume_ratio,
        "h1_change": h1_change,
        "h4_change": h4_change,
        "rsi": rsi_value,
        "signal_candle": signal_candle
    }


# ============================================================
# PRICE ROUNDING
# ============================================================

def round_upbit_tick(price):

    if price >= 2_000_000:
        unit = 1000

    elif price >= 1_000_000:
        unit = 500

    elif price >= 500_000:
        unit = 100

    elif price >= 100_000:
        unit = 50

    elif price >= 10_000:
        unit = 10

    elif price >= 1_000:
        unit = 5

    elif price >= 100:
        unit = 1

    elif price >= 10:
        unit = 0.1

    elif price >= 1:
        unit = 0.01

    elif price >= 0.1:
        unit = 0.001

    elif price >= 0.01:
        unit = 0.0001

    elif price >= 0.001:
        unit = 0.00001

    else:
        unit = 0.000001

    return round(
        round(price / unit) * unit,
        8
    )


# ============================================================
# POSITION
# ============================================================

def create_position(
    item,
    result,
    btc_info,
    cache
):

    market = item["market"]

    price = result["price"]

    signal_candle = result[
        "signal_candle"
    ]

    signal_id = make_signal_id(
        market,
        signal_candle
    )

    # --------------------------------------------------------
    # 최종 중복 방어
    # --------------------------------------------------------

    # 최신 cache 다시 확인
    fresh_cache = load_cache()

    if signal_already_sent(
        fresh_cache,
        signal_id,
        market
    ):

        print(
            f"[DUPLICATE BLOCK] "
            f"{market} | {signal_id}"
        )

        return False

    # 현재 포지션
    if market in fresh_cache.get(
        "positions",
        {}
    ):

        print(
            f"[POSITION BLOCK] {market}"
        )

        return False

    # 가격 쿨다운
    allowed, reason = can_create_new_signal(
        fresh_cache,
        market,
        price
    )

    if not allowed:

        print(
            f"[SIGNAL BLOCK] "
            f"{market} | {reason}"
        )

        return False

    # --------------------------------------------------------
    # 다시 저장
    # --------------------------------------------------------

    cache = fresh_cache

    entry = round_upbit_tick(
        price
    )

    stop = round_upbit_tick(
        result["stop"]
    )

    tp1 = round_upbit_tick(
        result["tp1"]
    )

    tp2 = round_upbit_tick(
        result["tp2"]
    )

    tp3 = round_upbit_tick(
        result["tp3"]
    )

    now = now_kst().isoformat()

    position = {
        "market": market,
        "entry": entry,
        "stop": stop,
        "original_stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "stage": "ENTRY",
        "entry_time": now,
        "signal_candle": signal_candle,
        "signal_id": signal_id,
        "score": result["score"],
        "btc_regime": btc_info["regime"],
        "btc_score": btc_info["score"]
    }

    cache.setdefault(
        "positions",
        {}
    )[market] = position

    remember_signal(
        cache,
        signal_id
    )

    # --------------------------------------------------------
    # HISTORY에도 ID 기록
    # --------------------------------------------------------

    cache.setdefault(
        "history",
        []
    )

    cache["history"].append({
        "market": market,
        "entry": entry,
        "entry_time": now,
        "signal_candle": signal_candle,
        "signal_id": signal_id,
        "score": result["score"],
        "status": "OPEN"
    })

    # --------------------------------------------------------
    # 상태 먼저 저장
    # --------------------------------------------------------

    save_cache(cache)

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    coin_name = market.replace(
        "KRW-",
        ""
    )

    trade_value_eok = (
        item["trade_value"]
        / 100_000_000
    )

    message = (
        "🚀 *[UPBIT BUY SIGNAL]*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"▪ 코인: *{coin_name}*\n"
        f"▪ ENTRY: `{entry}`원\n"
        f"▪ 24H 거래대금: "
        f"`{trade_value_eok:.1f}억`\n"
        f"▪ 24H 변화: "
        f"`{item['change']:+.2f}%`\n\n"

        "🌐 *MARKET*\n"
        f"▪ BTC: {btc_icon(btc_info['regime'])} "
        f"{btc_info['regime']}\n"
        f"▪ BTC Score: `{btc_info['score']}`\n\n"

        "📊 *SIGNAL QUALITY*\n"
        f"▪ Score: *{result['score']}/100*\n"
        f"▪ 15M 거래량: "
        f"`{result['volume_ratio']:.0f}%`\n"
        f"▪ 1H 변화: "
        f"`{result['h1_change']:+.2f}%`\n"
        f"▪ 4H 변화: "
        f"`{result['h4_change']:+.2f}%`\n"
        f"▪ RSI: `{result['rsi']:.1f}`\n\n"

        "🎯 *TARGETS*\n"
        f"▪ TP1: `{tp1}`원 "
        f"(+{((tp1 / entry) - 1) * 100:.1f}%)\n"
        f"▪ TP2: `{tp2}`원\n"
        f"▪ TP3: `{tp3}`원\n\n"

        "🛡️ *RISK*\n"
        f"▪ SL: `{stop}`원 "
        f"(-{((entry - stop) / entry) * 100:.1f}%)\n"
        "▪ TP1 R:R: `1:1.5`\n"
        "▪ TP2 R:R: `1:2.0`\n"
        "▪ TP3 R:R: `1:3.0`\n\n"

        "🔎 *SIGNAL*\n"
        "강한 추세 + 모멘텀 + 거래량 + "
        "리스크 조건 종합\n\n"

        f"🕒 Signal Candle\n"
        f"`{signal_candle}`\n\n"

        "📌 TP1 도달 → SL ENTRY\n"
        "📌 TP2 도달 → SL TP1\n"
        "📌 TP3 도달 → 추적 종료"
    )

    sent = send_telegram_message(
        message
    )

    if sent:

        print(
            f"[NEW SIGNAL] "
            f"{market} | "
            f"Score {result['score']}"
        )

    else:

        print(
            f"[SIGNAL CREATED / TELEGRAM FAIL] "
            f"{market}"
        )

    return True


def btc_icon(regime):

    icons = {
        "BULL": "🟢",
        "NEUTRAL": "🟡",
        "WEAK": "🟠",
        "CRASH": "🔴"
    }

    return icons.get(
        regime,
        "⚪"
    )


# ============================================================
# TRACKING
# ============================================================

def track_positions(cache):

    positions = cache.get(
        "positions",
        {}
    )

    if not positions:

        print("[TRACKING] 포지션 없음")
        return

    print(
        f"[TRACKING] "
        f"{len(positions)}개 포지션"
    )

    closed_markets = []

    for market, pos in list(
        positions.items()
    ):

        try:

            df = get_candles(
                market,
                "minutes/1",
                5
            )

            if df is None or df.empty:
                continue

            candle = df.iloc[-1]

            high = safe_float(
                candle["high_price"]
            )

            low = safe_float(
                candle["low_price"]
            )

            entry = safe_float(
                pos["entry"]
            )

            stop = safe_float(
                pos["stop"]
            )

            tp1 = safe_float(
                pos["tp1"]
            )

            tp2 = safe_float(
                pos["tp2"]
            )

            tp3 = safe_float(
                pos["tp3"]
            )

            stage = pos.get(
                "stage",
                "ENTRY"
            )

            # ------------------------------------------------
            # STOP FIRST
            # ------------------------------------------------

            if low <= stop:

                if stage == "ENTRY":
                    result = "STOP_LOSS"
                else:
                    result = "PROTECTED"

                send_tracking_message(
                    market,
                    pos,
                    result,
                    stop
                )

                add_history_result(
                    cache,
                    pos,
                    result,
                    stop
                )

                closed_markets.append(
                    market
                )

                continue

            # ------------------------------------------------
            # TP1
            # ------------------------------------------------

            if (
                stage == "ENTRY"
                and high >= tp1
            ):

                pos["stage"] = "TP1"

                if MOVE_SL_TO_ENTRY_AFTER_TP1:
                    pos["stop"] = entry

                send_tracking_message(
                    market,
                    pos,
                    "TP1",
                    tp1
                )

                continue

            # ------------------------------------------------
            # TP2
            # ------------------------------------------------

            if (
                stage == "TP1"
                and high >= tp2
            ):

                pos["stage"] = "TP2"

                if MOVE_SL_TO_TP1_AFTER_TP2:
                    pos["stop"] = tp1

                send_tracking_message(
                    market,
                    pos,
                    "TP2",
                    tp2
                )

                continue

            # ------------------------------------------------
            # TP3
            # ------------------------------------------------

            if (
                stage == "TP2"
                and high >= tp3
            ):

                send_tracking_message(
                    market,
                    pos,
                    "TP3",
                    tp3
                )

                add_history_result(
                    cache,
                    pos,
                    "TP3",
                    tp3
                )

                closed_markets.append(
                    market
                )

        except Exception as e:

            print(
                f"[TRACK ERROR] "
                f"{market}: {e}"
            )

    for market in closed_markets:

        positions.pop(
            market,
            None
        )

    save_cache(cache)


def send_tracking_message(
    market,
    pos,
    event,
    price
):

    message = (
        f"📊 *[POSITION UPDATE]*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"▪ 코인: *{market}*\n"
        f"▪ 이벤트: *{event}*\n"
        f"▪ 가격: `{price}`원\n"
        f"▪ ENTRY: `{pos['entry']}`원\n"
        f"▪ 현재 SL: `{pos['stop']}`원\n"
        f"▪ Stage: `{pos.get('stage', 'ENTRY')}`"
    )

    send_telegram_message(
        message
    )


def add_history_result(
    cache,
    pos,
    result,
    exit_price
):

    history = cache.setdefault(
        "history",
        []
    )

    for item in reversed(history):

        if item.get(
            "signal_id"
        ) == pos.get(
            "signal_id"
        ):

            item["status"] = result
            item["exit"] = exit_price
            item["exit_time"] = (
                now_kst().isoformat()
            )

            break

    # 기존 형식과 호환
    if not any(
        x.get("signal_id")
        == pos.get("signal_id")
        for x in history
    ):

        history.append({
            "market": pos["market"],
            "entry": pos["entry"],
            "exit": exit_price,
            "entry_time": pos["entry_time"],
            "exit_time": now_kst().isoformat(),
            "signal_candle": pos.get(
                "signal_candle",
                ""
            ),
            "signal_id": pos.get(
                "signal_id",
                ""
            ),
            "status": result,
            "score": pos.get(
                "score",
                0
            )
        })


# ============================================================
# DEEP SCAN
# ============================================================

def scan_new_signals(
    cache
):

    print(
        "\n[STEP 2] "
        "업비트 전체 시장 신규 신호 탐색"
    )

    btc_info = check_btc_market()

    print(
        f"\n[BTC] BTC "
        f"{btc_info['regime']} / "
        f"Score {btc_info['score']}"
    )

    if btc_info["regime"] == "BULL":
        print("🟢 BTC 상승 환경")

    elif btc_info["regime"] == "NEUTRAL":
        print("🟡 BTC 중립")

    elif btc_info["regime"] == "WEAK":
        print("🟠 BTC 약세")

    else:
        print("🔴 BTC 급락")

    markets = get_market_names()

    print(
        f"\n[SCAN] "
        f"업비트 KRW 전체 "
        f"{len(markets)}개 마켓"
    )

    if not markets:
        return

    tickers = get_all_tickers(
        markets
    )

    print(
        f"\n[MARKET] "
        f"전체 KRW 마켓 "
        f"{len(tickers)}개 확인"
    )

    candidates = build_candidates(
        tickers
    )

    print(
        f"[MARKET] "
        f"1차 후보 "
        f"{len(candidates)}개"
    )

    if not candidates:
        print(
            "[SCAN] 후보 없음"
        )
        return

    print("\n[TOP CANDIDATES]")

    for i, item in enumerate(
        candidates[:20],
        1
    ):

        print(
            f"{i:02d}. "
            f"{item['market']} | "
            f"24H "
            f"{item['change']:+.2f}% | "
            f"Score "
            f"{item['fast_score']} | "
            f"{item['trade_value']/100_000_000:.1f}억"
        )

    deep_candidates = candidates[
        :MAX_DEEP_SCAN
    ]

    print(
        f"\n[DEEP SCAN] "
        f"{len(deep_candidates)}개 "
        f"정밀분석"
    )

    final_signals = 0
    failed = 0

    # --------------------------------------------------------
    # 중복 후보 제거
    # --------------------------------------------------------

    seen_markets = set()

    unique_candidates = []

    for item in deep_candidates:

        market = item["market"]

        if market in seen_markets:
            continue

        seen_markets.add(market)

        unique_candidates.append(
            item
        )

    # --------------------------------------------------------
    # DEEP ANALYSIS
    # --------------------------------------------------------

    for idx, item in enumerate(
        unique_candidates,
        1
    ):

        market = item["market"]

        print(
            f"\n[{idx}/{len(unique_candidates)}] "
            f"{market} | "
            f"24H {item['change']:+.2f}% | "
            f"FAST {item['fast_score']}"
        )

        try:

            result = analyze_coin(
                market,
                btc_info
            )

            if result is None:

                print(
                    "   → 데이터 부족"
                )

                failed += 1
                continue

            print(
                f"   → SCORE "
                f"{result['score']}"
            )

            # 조건 요약
            for reason in result[
                "reason"
            ]:

                print(
                    f"      {reason}"
                )

            if not result["signal"]:

                print(
                    "   → 최종 SIGNAL FAIL"
                )

                failed += 1

                continue

            # ------------------------------------------------
            # SIGNAL
            # ------------------------------------------------

            signal_id = make_signal_id(
                market,
                result[
                    "signal_candle"
                ]
            )

            if signal_already_sent(
                cache,
                signal_id,
                market
            ):

                print(
                    "   → DUPLICATE BLOCK"
                )

                continue

            created = create_position(
                item,
                result,
                btc_info,
                cache
            )

            if created:

                final_signals += 1

            # API 보호
            time.sleep(0.2)

        except Exception as e:

            print(
                f"   → 분석 오류: {e}"
            )

            failed += 1

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    print(
        "\n" + "=" * 42
    )

    print(
        "[SCAN COMPLETE]"
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
        f"{len(unique_candidates)}"
    )

    print(
        f"최종 신호: "
        f"{final_signals}"
    )

    print(
        f"정밀 탈락/실패: "
        f"{failed}"
    )

    print(
        "=" * 42
    )


# ============================================================
# MAIN
# ============================================================

def main():

    telegram_status()

    cache = load_cache()

    print(
        "\n[STEP 1] 기존 포지션 추적"
    )

    track_positions(
        cache
    )

    # tracking 후 최신 cache 재로드
    cache = load_cache()

    scan_new_signals(
        cache
    )

    # 마지막 저장
    save_cache(
        cache
    )

    print(
        "\n" + "=" * 65
    )

    print(
        "BOT FINISHED"
    )

    print(
        "=" * 65
    )


if __name__ == "__main__":
    main()
