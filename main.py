import os
import json
import time
import math
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

# ============================================================
# UPBIT SMART SIGNAL BOT V4.1
# - Legacy position compatibility
# - Score capped at 100
# - Duplicate signal protection
# - BTC regime filter
# - Full KRW fast scan + top 80 deep scan
# - Position tracking with ENTRY / TP1 / TP2 / TP3 / SL
# ============================================================

CACHE_FILE = "tracked_coins.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

UPBIT_BASE_URL = "https://api.upbit.com/v1"

# ============================================================
# SETTINGS
# ============================================================

MAX_DEEP_SCAN = 80
MIN_FINAL_24H_TRADE_VALUE = 1_000_000_000

SIGNAL_SCORE = 75
WEAK_BTC_SCORE = 82
STRONG_SCORE = 88

MIN_VOLUME_RATIO = 130
STRONG_VOLUME_RATIO = 200

MIN_BODY_RATIO = 0.45
MIN_CLOSE_POSITION = 0.62

MAX_ENTRY_DISTANCE_FROM_EMA20 = 6.0

MIN_STOP_LOSS_PCT = 1.0
MAX_STOP_LOSS_PCT = 7.0

MIN_RR_TP1 = 1.5
MIN_RR_TP2 = 2.0

MAX_TP1_DISTANCE_PCT = 15.0

SIGNAL_COOLDOWN_HOURS = 6
MIN_NEW_SIGNAL_PRICE_DISTANCE = 2.0

BTC_15M_CRASH_PCT = -1.5
BTC_1H_CRASH_PCT = -2.0

MOVE_SL_TO_ENTRY_AFTER_TP1 = True
MOVE_SL_TO_TP1_AFTER_TP2 = True

KST = timezone(timedelta(hours=9))

# ============================================================
# BASIC HELPERS
# ============================================================

def now_kst():
    return datetime.now(KST)


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        if isinstance(value, str):
            value = value.replace(",", "").replace("%", "").strip()

        result = float(value)

        if not math.isfinite(result):
            return default

        return result

    except Exception:
        return default


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def pct_change(old, new):
    old = safe_float(old)
    new = safe_float(new)

    if old == 0:
        return 0.0

    return (new / old - 1.0) * 100.0


def clamp_score(score):
    return max(0, min(100, int(round(score))))


def fmt_price(price):
    price = safe_float(price)

    if price >= 1000:
        return f"{price:,.0f}"
    if price >= 100:
        return f"{price:,.1f}"
    if price >= 1:
        return f"{price:,.3f}"
    if price >= 0.01:
        return f"{price:,.5f}"

    return f"{price:,.8f}"


def fmt_pct(value):
    return f"{safe_float(value):+.2f}%"


# ============================================================
# JSON STATE
# ============================================================

def load_state():
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

        if not isinstance(data["positions"], dict):
            data["positions"] = {}

        if not isinstance(data["history"], list):
            data["history"] = []

        if not isinstance(data["sent_signal_ids"], list):
            data["sent_signal_ids"] = []

        return data

    except Exception as e:
        print(f"[STATE ERROR] {e}")

        return {
            "positions": {},
            "history": [],
            "sent_signal_ids": []
        }


def save_state(state):
    temp_file = CACHE_FILE + ".tmp"

    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(
                state,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(temp_file, CACHE_FILE)

    except Exception as e:
        print(f"[STATE SAVE ERROR] {e}")


# ============================================================
# LEGACY POSITION MIGRATION
# ============================================================

def first_existing(pos, keys, default=0.0):
    for key in keys:
        if key in pos and pos[key] not in (None, ""):
            return pos[key]

    return default


def normalize_position(market, raw):
    """
    Converts old position formats into the current canonical format.

    Supported examples:

    stop / sl / stop_loss
    tp1 / target1 / take_profit_1
    tp2 / target2 / take_profit_2
    tp3 / target3 / take_profit_3
    entry / entry_price / buy_price
    """

    if not isinstance(raw, dict):
        return None

    entry = safe_float(
        first_existing(
            raw,
            [
                "entry",
                "entry_price",
                "buy_price",
                "price",
                "avg_price"
            ]
        )
    )

    stop = safe_float(
        first_existing(
            raw,
            [
                "stop",
                "sl",
                "stop_loss",
                "stopPrice"
            ]
        )
    )

    tp1 = safe_float(
        first_existing(
            raw,
            [
                "tp1",
                "target1",
                "take_profit_1",
                "takeProfit1"
            ]
        )
    )

    tp2 = safe_float(
        first_existing(
            raw,
            [
                "tp2",
                "target2",
                "take_profit_2",
                "takeProfit2"
            ]
        )
    )

    tp3 = safe_float(
        first_existing(
            raw,
            [
                "tp3",
                "target3",
                "take_profit_3",
                "takeProfit3"
            ]
        )
    )

    stage = str(
        raw.get(
            "stage",
            raw.get(
                "status",
                "ENTRY"
            )
        )
    ).upper()

    if stage in ("OPEN", "ACTIVE", "NEW"):
        stage = "ENTRY"

    if stage not in ("ENTRY", "TP1", "TP2"):
        stage = "ENTRY"

    signal_candle = raw.get(
        "signal_candle",
        raw.get("candle_time", "")
    )

    signal_id = raw.get("signal_id", "")

    if not signal_id and signal_candle:
        signal_id = f"{market}|{signal_candle}"

    created_at = raw.get(
        "created_at",
        raw.get(
            "entry_time",
            now_kst().isoformat()
        )
    )

    score = safe_int(
        raw.get(
            "score",
            raw.get("signal_score", 0)
        )
    )

    # --------------------------------------------------------
    # Invalid legacy record
    # --------------------------------------------------------

    if entry <= 0:
        print(
            f"[TRACK SKIP] {market}: "
            f"entry price missing"
        )
        return None

    if stop <= 0:
        print(
            f"[TRACK SKIP] {market}: "
            f"stop/sl missing"
        )
        return None

    if tp1 <= 0:
        print(
            f"[TRACK SKIP] {market}: "
            f"tp1/target1 missing"
        )
        return None

    # TP2 / TP3 may not exist in some very old records.
    # Reconstruct them from the available risk distance.
    risk = abs(entry - stop)

    if tp2 <= 0:
        if entry > stop:
            tp2 = entry + risk * 2.0
        else:
            tp2 = entry - risk * 2.0

    if tp3 <= 0:
        if entry > stop:
            tp3 = entry + risk * 3.0
        else:
            tp3 = entry - risk * 3.0

    normalized = {
        "market": market,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "stage": stage,
        "signal_id": signal_id,
        "signal_candle": signal_candle,
        "created_at": created_at,
        "score": clamp_score(score),
        "direction": raw.get("direction", "LONG"),
        "original": False
    }

    return normalized


def migrate_positions(state):
    """
    Automatically converts old position schema.
    No manual tracked_coins.json editing required.
    """

    positions = state.get("positions", {})

    if not isinstance(positions, dict):
        state["positions"] = {}
        return

    migrated = {}

    for market, raw in positions.items():

        normalized = normalize_position(
            market,
            raw
        )

        if normalized is not None:
            migrated[market] = normalized

    state["positions"] = migrated


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] TOKEN/CHAT_ID missing")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        r = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if r.ok:
            print("[TELEGRAM] 전송 성공")
            return True

        print(
            f"[TELEGRAM ERROR] "
            f"{r.status_code}: {r.text[:300]}"
        )

    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")

    return False


# ============================================================
# UPBIT API
# ============================================================

def upbit_get(endpoint, params=None):
    url = UPBIT_BASE_URL + endpoint

    try:
        r = requests.get(
            url,
            params=params or {},
            timeout=15
        )

        r.raise_for_status()

        return r.json()

    except Exception as e:
        print(
            f"[API ERROR] {endpoint}: {e}"
        )

        return None


def get_markets():
    data = upbit_get(
        "/market/all",
        {"isDetails": "false"}
    )

    if not data:
        return []

    return [
        x["market"]
        for x in data
        if x.get("market", "").startswith("KRW-")
    ]


def get_tickers(markets):
    if not markets:
        return []

    result = []

    for i in range(0, len(markets), 100):
        batch = markets[i:i + 100]

        data = upbit_get(
            "/ticker",
            {
                "markets": ",".join(batch)
            }
        )

        if data:
            result.extend(data)

        time.sleep(0.05)

    return result


def get_candles(market, unit, count=200):
    data = upbit_get(
        f"/candles/minutes/{unit}",
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

    df = df.iloc[::-1].reset_index(drop=True)

    df.rename(
        columns={
            "opening_price": "open",
            "high_price": "high",
            "low_price": "low",
            "trade_price": "close",
            "candle_acc_trade_volume": "volume",
            "candle_date_time_kst": "time"
        },
        inplace=True
    )

    for col in [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]:
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


def rsi(series, length=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan
    )

    result = 100 - (
        100 / (1 + rs)
    )

    return result.fillna(50)


def atr(df, length=14):
    prev_close = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs()
        ],
        axis=1
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()


# ============================================================
# BTC REGIME
# ============================================================

def get_btc_regime():
    df15 = get_candles(
        "KRW-BTC",
        15,
        120
    )

    df1h = get_candles(
        "KRW-BTC",
        60,
        120
    )

    if (
        df15 is None
        or df1h is None
        or len(df15) < 30
        or len(df1h) < 30
    ):
        return {
            "regime": "NEUTRAL",
            "score": 50,
            "change15": 0,
            "change1h": 0
        }

    change15 = pct_change(
        df15["close"].iloc[-2],
        df15["close"].iloc[-1]
    )

    change1h = pct_change(
        df1h["close"].iloc[-2],
        df1h["close"].iloc[-1]
    )

    e20_15 = ema(
        df15["close"],
        20
    ).iloc[-1]

    e50_15 = ema(
        df15["close"],
        50
    ).iloc[-1]

    e20_1h = ema(
        df1h["close"],
        20
    ).iloc[-1]

    e50_1h = ema(
        df1h["close"],
        50
    ).iloc[-1]

    score = 50

    if e20_15 > e50_15:
        score += 10
    else:
        score -= 10

    if e20_1h > e50_1h:
        score += 15
    else:
        score -= 15

    if change15 > 0:
        score += 5
    else:
        score -= 5

    if change1h > 0:
        score += 10
    else:
        score -= 10

    score = max(0, min(100, score))

    if (
        change15 <= BTC_15M_CRASH_PCT
        or change1h <= BTC_1H_CRASH_PCT
    ):
        regime = "CRASH"

    elif score >= 65:
        regime = "BULL"

    elif score <= 35:
        regime = "WEAK"

    else:
        regime = "NEUTRAL"

    print(
        f"\n[BTC] BTC {regime} / Score {score}"
    )

    if regime == "CRASH":
        print("🔴 BTC 급락")
    elif regime == "WEAK":
        print("🟠 BTC 약세")
    elif regime == "BULL":
        print("🟢 BTC 강세")
    else:
        print("🟡 BTC 중립")

    return {
        "regime": regime,
        "score": score,
        "change15": change15,
        "change1h": change1h
    }


# ============================================================
# FAST MARKET SCORE
# ============================================================

def fast_score(ticker):
    price = safe_float(
        ticker.get("trade_price")
    )

    change = safe_float(
        ticker.get("signed_change_rate")
    ) * 100

    value = safe_float(
        ticker.get("acc_trade_price_24h")
    )

    score = 0

    if change >= 15:
        score += 40
    elif change >= 10:
        score += 35
    elif change >= 7:
        score += 30
    elif change >= 5:
        score += 25
    elif change >= 3:
        score += 18
    elif change >= 1:
        score += 10

    if value >= 50_000_000_000:
        score += 40
    elif value >= 20_000_000_000:
        score += 35
    elif value >= 10_000_000_000:
        score += 30
    elif value >= 5_000_000_000:
        score += 25
    elif value >= 1_000_000_000:
        score += 15

    if price > 0:
        score += 5

    return clamp_score(score)


# ============================================================
# DEEP ANALYSIS
# ============================================================

def analyze_coin(
    market,
    ticker,
    btc
):
    df1d = get_candles(
        market,
        1440,
        120
    )

    df4h = get_candles(
        market,
        240,
        120
    )

    df1h = get_candles(
        market,
        60,
        150
    )

    df15 = get_candles(
        market,
        15,
        200
    )

    if any(
        x is None
        or len(x) < 60
        for x in [
            df1d,
            df4h,
            df1h,
            df15
        ]
    ):
        return None

    # --------------------------------------------------------
    # Indicators
    # --------------------------------------------------------

    for df in [
        df1d,
        df4h,
        df1h,
        df15
    ]:
        df["ema20"] = ema(
            df["close"],
            20
        )

        df["ema50"] = ema(
            df["close"],
            50
        )

    df15["rsi"] = rsi(
        df15["close"],
        14
    )

    df15["atr"] = atr(
        df15,
        14
    )

    # --------------------------------------------------------
    # Last completed candle
    # --------------------------------------------------------

    i15 = -2
    i1h = -2
    i4h = -2
    i1d = -2

    price = safe_float(
        ticker.get("trade_price")
    )

    if price <= 0:
        price = safe_float(
            df15["close"].iloc[i15]
        )

    # --------------------------------------------------------
    # Trend conditions
    # --------------------------------------------------------

    d1_close = df1d["close"].iloc[i1d]
    d1_e20 = df1d["ema20"].iloc[i1d]
    d1_e50 = df1d["ema50"].iloc[i1d]

    h4_close = df4h["close"].iloc[i4h]
    h4_e20 = df4h["ema20"].iloc[i4h]
    h4_e50 = df4h["ema50"].iloc[i4h]

    h1_close = df1h["close"].iloc[i1h]
    h1_e20 = df1h["ema20"].iloc[i1h]
    h1_e50 = df1h["ema50"].iloc[i1h]

    m15_close = df15["close"].iloc[i15]
    m15_e20 = df15["ema20"].iloc[i15]

    d1_ema_pass = d1_close > d1_e20
    d1_align = d1_e20 > d1_e50

    h4_ema_pass = h4_close > h4_e20
    h4_align = h4_e20 > h4_e50

    h1_ema_pass = h1_close > h1_e20
    h1_align = h1_e20 > h1_e50

    m15_ema_pass = m15_close > m15_e20

    # --------------------------------------------------------
    # Momentum
    # --------------------------------------------------------

    h1_momentum = pct_change(
        df1h["close"].iloc[-9],
        df1h["close"].iloc[i1h]
    )

    m15_momentum = pct_change(
        df15["close"].iloc[-9],
        df15["close"].iloc[i15]
    )

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    recent_volume = safe_float(
        df15["volume"].iloc[i15]
    )

    volume_base = safe_float(
        df15["volume"].iloc[-22:-2].mean()
    )

    if volume_base <= 0:
        volume_ratio = 0
    else:
        volume_ratio = (
            recent_volume /
            volume_base *
            100
        )

    # --------------------------------------------------------
    # Candle strength
    # --------------------------------------------------------

    candle = df15.iloc[i15]

    candle_range = (
        candle["high"] -
        candle["low"]
    )

    if candle_range <= 0:
        body_ratio = 0
        close_position = 0
    else:
        body_ratio = (
            abs(
                candle["close"] -
                candle["open"]
            ) /
            candle_range
        )

        close_position = (
            candle["close"] -
            candle["low"]
        ) / candle_range

    candle_pass = (
        candle["close"] > candle["open"]
        and body_ratio >= MIN_BODY_RATIO
        and close_position >= MIN_CLOSE_POSITION
    )

    # --------------------------------------------------------
    # Breakout
    # --------------------------------------------------------

    previous_high = safe_float(
        df15["high"].iloc[-22:-2].max()
    )

    breakout_pass = (
        candle["close"] > previous_high
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    current_rsi = safe_float(
        df15["rsi"].iloc[i15],
        50
    )

    # --------------------------------------------------------
    # ATR / risk
    # --------------------------------------------------------

    current_atr = safe_float(
        df15["atr"].iloc[i15]
    )

    if current_atr <= 0:
        current_atr = (
            price * 0.03
        )

    stop = price - (
        current_atr * 1.5
    )

    risk = price - stop

    stop_loss_pct = (
        risk /
        price *
        100
    )

    tp1 = price + risk * 1.5
    tp2 = price + risk * 2.0
    tp3 = price + risk * 3.0

    tp1_distance_pct = (
        (tp1 / price - 1)
        * 100
    )

    # --------------------------------------------------------
    # EMA distance
    # --------------------------------------------------------

    ema_distance_pct = (
        abs(price - m15_e20)
        / price
        * 100
    )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 0

    details = []

    # 1D trend: 20
    if d1_ema_pass:
        score += 7
        details.append("1D EMA20 PASS")
    else:
        details.append("1D EMA20 FAIL")

    if d1_align:
        score += 7
        details.append("1D 정배열 PASS")
    else:
        details.append("1D 정배열 FAIL")

    d1_rising = (
        df1d["ema20"].iloc[i1d]
        >
        df1d["ema20"].iloc[i1d - 3]
    )

    if d1_rising:
        score += 6
        details.append("1D 상승 PASS")
    else:
        details.append("1D 상승 FAIL")

    # 4H trend: 20
    if h4_ema_pass:
        score += 7
        details.append("4H EMA20 PASS")
    else:
        details.append("4H EMA20 FAIL")

    if h4_align:
        score += 7
        details.append("4H 정배열 PASS")
    else:
        details.append("4H 정배열 FAIL")

    h4_rising = (
        df4h["ema20"].iloc[i4h]
        >
        df4h["ema20"].iloc[i4h - 3]
    )

    if h4_rising:
        score += 6
        details.append("4H 상승 PASS")
    else:
        details.append("4H 상승 FAIL")

    # 1H trend / momentum: 15
    if h1_ema_pass:
        score += 5
        details.append("1H EMA20 PASS")
    else:
        details.append("1H EMA20 FAIL")

    if h1_align:
        score += 5
        details.append("1H 정배열 PASS")
    else:
        details.append("1H 정배열 FAIL")

    if h1_momentum > 0:
        score += 5
        details.append(
            f"1H 모멘텀 PASS {h1_momentum:+.2f}%"
        )
    else:
        details.append(
            f"1H 모멘텀 FAIL {h1_momentum:+.2f}%"
        )

    # 15M momentum: 15
    if m15_ema_pass:
        score += 5
        details.append("15M EMA20 PASS")
    else:
        details.append("15M EMA20 FAIL")

    m15_ema_rising = (
        df15["ema20"].iloc[i15]
        >
        df15["ema20"].iloc[i15 - 3]
    )

    if m15_ema_rising:
        score += 5
        details.append("15M EMA 상승 PASS")
    else:
        details.append("15M EMA 상승 FAIL")

    if m15_momentum > 0:
        score += 5
        details.append(
            f"15M 모멘텀 PASS {m15_momentum:+.2f}%"
        )
    else:
        details.append(
            f"15M 모멘텀 FAIL {m15_momentum:+.2f}%"
        )

    # Volume: 15
    if volume_ratio >= STRONG_VOLUME_RATIO:
        score += 15
        details.append(
            f"거래량 {volume_ratio:.0f}% (15/15)"
        )
    elif volume_ratio >= MIN_VOLUME_RATIO:
        score += 10
        details.append(
            f"거래량 {volume_ratio:.0f}% (10/15)"
        )
    else:
        details.append(
            f"거래량 {volume_ratio:.0f}% (0/15)"
        )

    # Candle: 10
    if candle_pass:
        score += 10
        details.append("캔들 10/10")
    else:
        details.append("캔들 0/10")

    # Breakout: 10
    if breakout_pass:
        score += 10
        details.append("15M 돌파 PASS")
    else:
        details.append("15M 돌파 FAIL")

    # RSI adjustment
    if current_rsi >= 82:
        score -= 5
        details.append(
            f"RSI 과열 -5 ({current_rsi:.1f})"
        )
    elif current_rsi >= 70:
        details.append(
            f"RSI 강세 ({current_rsi:.1f})"
        )
    elif current_rsi < 50:
        score -= 2
        details.append(
            f"RSI 약세 -2 ({current_rsi:.1f})"
        )
    else:
        details.append(
            f"RSI 중립 ({current_rsi:.1f})"
        )

    # BTC adjustment
    btc_regime = btc["regime"]

    if btc_regime == "BULL":
        score += 5
        details.append("BTC 강세 +5")

    elif btc_regime == "WEAK":
        score -= 3
        details.append("BTC 약세 -3")

    elif btc_regime == "CRASH":
        score -= 15
        details.append("BTC 급락 -15")

    else:
        details.append("BTC 중립")

    # 4H overheat
    h4_change = pct_change(
        df4h["close"].iloc[-10],
        df4h["close"].iloc[i4h]
    )

    if h4_change > 18:
        score -= 8
        details.append(
            f"4H 과열 -8 {h4_change:+.2f}%"
        )

    # EMA distance
    if ema_distance_pct > MAX_ENTRY_DISTANCE_FROM_EMA20:
        score -= 7
        details.append(
            f"EMA20 거리 과다 -7 "
            f"{ema_distance_pct:.2f}%"
        )
    else:
        details.append(
            f"EMA20 거리 OK "
            f"({ema_distance_pct:.2f}%)"
        )

    # TP1 distance
    if tp1_distance_pct > MAX_TP1_DISTANCE_PCT:
        score -= 8
        details.append(
            f"TP1 거리 과다 -8 "
            f"{tp1_distance_pct:.2f}%"
        )

    # Risk
    if stop_loss_pct > MAX_STOP_LOSS_PCT:
        details.append(
            f"SL 과다 FAIL "
            f"{stop_loss_pct:.2f}%"
        )
    elif stop_loss_pct < MIN_STOP_LOSS_PCT:
        score -= 3
        details.append(
            f"SL 너무 좁음 -3 "
            f"{stop_loss_pct:.2f}%"
        )
    else:
        score += 5
        details.append(
            f"Risk PASS SL "
            f"{stop_loss_pct:.2f}%"
        )

    # --------------------------------------------------------
    # FINAL SCORE CLAMP
    # --------------------------------------------------------

    raw_score = score
    score = clamp_score(score)

    # --------------------------------------------------------
    # FINAL CONDITIONS
    # --------------------------------------------------------

    final_pass = True

    if stop_loss_pct > MAX_STOP_LOSS_PCT:
        final_pass = False

    if tp1_distance_pct > MAX_TP1_DISTANCE_PCT:
        final_pass = False

    if risk <= 0:
        final_pass = False

    if tp1 <= price:
        final_pass = False

    if tp2 <= tp1:
        final_pass = False

    if tp3 <= tp2:
        final_pass = False

    # BTC crash requires very strong setup
    if btc_regime == "CRASH":
        if score < STRONG_SCORE:
            final_pass = False

    # Weak BTC requires stronger setup
    elif btc_regime == "WEAK":
        if score < WEAK_BTC_SCORE:
            final_pass = False

    else:
        if score < SIGNAL_SCORE:
            final_pass = False

    return {
        "market": market,
        "price": price,

        "score": score,
        "raw_score": raw_score,

        "entry": price,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,

        "stop_loss_pct": stop_loss_pct,
        "tp1_distance_pct": tp1_distance_pct,
        "ema_distance_pct": ema_distance_pct,

        "rsi": current_rsi,
        "volume_ratio": volume_ratio,

        "h1_momentum": h1_momentum,
        "m15_momentum": m15_momentum,

        "btc_regime": btc_regime,

        "signal_candle": str(
            df15["time"].iloc[i15]
        ),

        "final_pass": final_pass,

        "details": details
    }


# ============================================================
# SIGNAL DUPLICATE PROTECTION
# ============================================================

def signal_already_sent(state, signal_id):
    if not signal_id:
        return True

    sent = state.get(
        "sent_signal_ids",
        []
    )

    if signal_id in sent:
        return True

    # Also check active positions
    for pos in state.get(
        "positions",
        {}
    ).values():

        if pos.get("signal_id") == signal_id:
            return True

    # Also check history
    for item in state.get(
        "history",
        []
    ):

        if item.get("signal_id") == signal_id:
            return True

    return False


def remember_signal(state, signal_id):
    if not signal_id:
        return

    sent = state.setdefault(
        "sent_signal_ids",
        []
    )

    if signal_id not in sent:
        sent.append(signal_id)

    # Keep file from growing forever
    if len(sent) > 1000:
        state["sent_signal_ids"] = sent[-1000:]


# ============================================================
# NEW SIGNAL COOLDOWN
# ============================================================

def can_create_new_signal(
    state,
    market,
    price
):
    positions = state.get(
        "positions",
        {}
    )

    # Existing position
    if market in positions:
        return False

    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(
            hours=SIGNAL_COOLDOWN_HOURS
        )
    )

    for item in state.get(
        "history",
        []
    ):

        if item.get("market") != market:
            continue

        created = item.get(
            "created_at",
            ""
        )

        try:
            dt = datetime.fromisoformat(
                created.replace(
                    "Z",
                    "+00:00"
                )
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=timezone.utc
                )

        except Exception:
            continue

        if dt >= cutoff:

            old_price = safe_float(
                item.get("entry")
            )

            if old_price <= 0:
                return False

            distance = abs(
                price / old_price - 1
            ) * 100

            if distance < MIN_NEW_SIGNAL_PRICE_DISTANCE:
                return False

    return True


# ============================================================
# CREATE POSITION
# ============================================================

def create_position(
    state,
    analysis
):
    market = analysis["market"]

    signal_candle = analysis[
        "signal_candle"
    ]

    signal_id = (
        f"{market}|{signal_candle}"
    )

    # --------------------------------------------------------
    # Reload latest state before creating
    # --------------------------------------------------------

    latest = load_state()

    migrate_positions(latest)

    if signal_already_sent(
        latest,
        signal_id
    ):
        print(
            f"[DUPLICATE BLOCK] "
            f"{signal_id}"
        )
        return False

    if market in latest.get(
        "positions",
        {}
    ):
        print(
            f"[POSITION EXISTS] "
            f"{market}"
        )
        return False

    if not can_create_new_signal(
        latest,
        market,
        analysis["price"]
    ):
        print(
            f"[COOLDOWN BLOCK] "
            f"{market}"
        )
        return False

    position = {
        "market": market,

        "entry": analysis["entry"],
        "stop": analysis["stop"],
        "tp1": analysis["tp1"],
        "tp2": analysis["tp2"],
        "tp3": analysis["tp3"],

        "stage": "ENTRY",

        "score": analysis["score"],
        "signal_id": signal_id,
        "signal_candle": signal_candle,

        "direction": "LONG",

        "created_at": now_kst().isoformat()
    }

    latest["positions"][market] = position

    remember_signal(
        latest,
        signal_id
    )

    # --------------------------------------------------------
    # Save BEFORE Telegram
    # --------------------------------------------------------

    save_state(latest)

    message = (
        "🟢 <b>UPBIT LONG SIGNAL</b>\n"
        "\n"
        f"<b>{market}</b>\n"
        f"Score: <b>{analysis['score']}/100</b>\n"
        f"BTC: {analysis['btc_regime']}\n"
        "\n"
        f"ENTRY: <b>{fmt_price(analysis['entry'])}</b>\n"
        f"SL: <b>{fmt_price(analysis['stop'])}</b>\n"
        f"TP1: <b>{fmt_price(analysis['tp1'])}</b>\n"
        f"TP2: <b>{fmt_price(analysis['tp2'])}</b>\n"
        f"TP3: <b>{fmt_price(analysis['tp3'])}</b>\n"
        "\n"
        f"SL Risk: {analysis['stop_loss_pct']:.2f}%\n"
        f"RSI: {analysis['rsi']:.1f}\n"
        f"Volume: {analysis['volume_ratio']:.0f}%\n"
        "\n"
        "15M candle confirmed"
    )

    sent = send_telegram(
        message
    )

    if sent:
        print(
            f"[NEW SIGNAL] "
            f"{market} | "
            f"Score {analysis['score']}"
        )

    return True


# ============================================================
# HISTORY
# ============================================================

def add_history_result(
    state,
    pos,
    result,
    exit_price,
    pnl_pct
):
    history = state.setdefault(
        "history",
        []
    )

    signal_id = pos.get(
        "signal_id",
        ""
    )

    # Update existing record if possible
    for item in history:

        if signal_id and (
            item.get("signal_id")
            == signal_id
        ):
            item.update({
                "result": result,
                "exit_price": exit_price,
                "pnl_pct": pnl_pct,
                "closed_at": now_kst().isoformat()
            })
            return

    history.append({
        "market": pos.get(
            "market",
            ""
        ),

        "signal_id": signal_id,

        "entry": pos.get(
            "entry",
            0
        ),

        "exit_price": exit_price,

        "result": result,

        "pnl_pct": pnl_pct,

        "score": pos.get(
            "score",
            0
        ),

        "created_at": pos.get(
            "created_at",
            ""
        ),

        "closed_at": now_kst().isoformat()
    })


# ============================================================
# POSITION TRACKING
# ============================================================

def track_positions(state):
    positions = state.get(
        "positions",
        {}
    )

    if not positions:
        print(
            "[TRACKING] 활성 포지션 없음"
        )
        return

    print(
        f"[TRACKING] "
        f"{len(positions)}개 포지션"
    )

    changed = False

    for market in list(
        positions.keys()
    ):

        try:
            # Normalize legacy record
            normalized = normalize_position(
                market,
                positions[market]
            )

            if normalized is None:
                print(
                    f"[TRACK SKIP] "
                    f"{market}"
                )
                continue

            positions[market] = normalized
            pos = normalized

            df1m = get_candles(
                market,
                1,
                10
            )

            if (
                df1m is None
                or len(df1m) < 2
            ):
                print(
                    f"[TRACK] "
                    f"{market}: "
                    f"1m data unavailable"
                )
                continue

            candle = df1m.iloc[-1]

            high = safe_float(
                candle["high"]
            )

            low = safe_float(
                candle["low"]
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
            # SL CHECK FIRST
            # ------------------------------------------------
            #
            # If one 1m candle touches both
            # SL and TP, SL is handled first
            # for conservative tracking.
            # ------------------------------------------------

            if low <= stop:

                pnl_pct = (
                    (stop / entry - 1)
                    * 100
                )

                if stage == "ENTRY":
                    result = "STOP_LOSS"
                else:
                    result = "PROTECTED"

                add_history_result(
                    state,
                    pos,
                    result,
                    stop,
                    pnl_pct
                )

                message = (
                    f"🔴 <b>{market} "
                    f"{result}</b>\n"
                    f"Entry: {fmt_price(entry)}\n"
                    f"Exit: {fmt_price(stop)}\n"
                    f"PnL: {pnl_pct:+.2f}%"
                )

                send_telegram(
                    message
                )

                del positions[market]

                changed = True

                print(
                    f"[CLOSED] "
                    f"{market} | "
                    f"{result} | "
                    f"{pnl_pct:+.2f}%"
                )

                continue

            # ------------------------------------------------
            # TP1
            # ------------------------------------------------

            if stage == "ENTRY" and high >= tp1:

                pos["stage"] = "TP1"

                if MOVE_SL_TO_ENTRY_AFTER_TP1:
                    pos["stop"] = entry

                add_history_result(
                    state,
                    pos,
                    "TP1",
                    tp1,
                    ((tp1 / entry) - 1) * 100
                )

                send_telegram(
                    f"🟢 <b>{market} TP1 HIT</b>\n"
                    f"TP1: {fmt_price(tp1)}\n"
                    f"PnL: {((tp1 / entry) - 1) * 100:+.2f}%\n"
                    f"SL → ENTRY"
                )

                changed = True

                print(
                    f"[TP1] {market}"
                )

                # Do not continue to TP2
                # on same polling cycle.
                continue

            # ------------------------------------------------
            # TP2
            # ------------------------------------------------

            if stage == "TP1" and high >= tp2:

                pos["stage"] = "TP2"

                if MOVE_SL_TO_TP1_AFTER_TP2:
                    pos["stop"] = tp1

                add_history_result(
                    state,
                    pos,
                    "TP2",
                    tp2,
                    ((tp2 / entry) - 1) * 100
                )

                send_telegram(
                    f"🟢 <b>{market} TP2 HIT</b>\n"
                    f"TP2: {fmt_price(tp2)}\n"
                    f"PnL: {((tp2 / entry) - 1) * 100:+.2f}%\n"
                    f"SL → TP1"
                )

                changed = True

                print(
                    f"[TP2] {market}"
                )

                continue

            # ------------------------------------------------
            # TP3
            # ------------------------------------------------

            if stage == "TP2" and high >= tp3:

                pnl_pct = (
                    (tp3 / entry - 1)
                    * 100
                )

                add_history_result(
                    state,
                    pos,
                    "TP3",
                    tp3,
                    pnl_pct
                )

                send_telegram(
                    f"🔵 <b>{market} TP3 HIT</b>\n"
                    f"Entry: {fmt_price(entry)}\n"
                    f"TP3: {fmt_price(tp3)}\n"
                    f"PnL: {pnl_pct:+.2f}%"
                )

                del positions[market]

                changed = True

                print(
                    f"[TP3] {market} | "
                    f"{pnl_pct:+.2f}%"
                )

                continue

        except Exception as e:

            print(
                f"[TRACK ERROR] "
                f"{market}: {e}"
            )

    if changed:
        save_state(state)


# ============================================================
# MAIN SCAN
# ============================================================

def scan_market(state, btc):
    print(
        "\n[SCAN] "
        "업비트 전체 시장 신규 신호 탐색"
    )

    markets = get_markets()

    if not markets:
        print(
            "[SCAN ERROR] "
            "KRW markets unavailable"
        )
        return

    print(
        f"[MARKET] "
        f"전체 KRW 마켓 {len(markets)}개 확인"
    )

    tickers = get_tickers(
        markets
    )

    if not tickers:
        print(
            "[SCAN ERROR] "
            "ticker unavailable"
        )
        return

    candidates = []

    for ticker in tickers:

        market = ticker.get(
            "market"
        )

        if not market:
            continue

        trade_value = safe_float(
            ticker.get(
                "acc_trade_price_24h"
            )
        )

        if (
            trade_value
            < MIN_FINAL_24H_TRADE_VALUE
        ):
            continue

        change = (
            safe_float(
                ticker.get(
                    "signed_change_rate"
                )
            )
            * 100
        )

        score = fast_score(
            ticker
        )

        candidates.append({
            "market": market,
            "ticker": ticker,
            "trade_value": trade_value,
            "change": change,
            "score": score
        })

    candidates.sort(
        key=lambda x: (
            x["score"],
            x["trade_value"]
        ),
        reverse=True
    )

    print(
        f"[MARKET] "
        f"1차 후보 {len(candidates)}개"
    )

    print(
        "\n[TOP CANDIDATES]"
    )

    for i, item in enumerate(
        candidates[:10],
        start=1
    ):
        print(
            f"{i:02d}. "
            f"{item['market']} | "
            f"24H {item['change']:+.2f}% | "
            f"Score {item['score']} | "
            f"{item['trade_value']/100000000:.1f}억"
        )

    deep_candidates = candidates[
        :MAX_DEEP_SCAN
    ]

    print(
        f"\n[DEEP SCAN] "
        f"{len(deep_candidates)}개 정밀분석"
    )

    signals_found = 0

    for index, item in enumerate(
        deep_candidates,
        start=1
    ):

        market = item["market"]

        print(
            f"\n[{index}/{len(deep_candidates)}] "
            f"{market} | "
            f"24H {item['change']:+.2f}% "
            f"| FAST {item['score']}"
        )

        # Existing active position
        if market in state.get(
            "positions",
            {}
        ):
            print(
                "   → 이미 활성 포지션"
            )
            continue

        try:
            analysis = analyze_coin(
                market,
                item["ticker"],
                btc
            )

            if analysis is None:
                print(
                    "   → 데이터 부족"
                )
                continue

            print(
                f"   → SCORE "
                f"{analysis['score']}"
            )

            for detail in analysis[
                "details"
            ]:
                print(
                    f"      {detail}"
                )

            if not analysis[
                "final_pass"
            ]:
                print(
                    "   → 최종 SIGNAL FAIL"
                )
                continue

            print(
                "   → ★ FINAL SIGNAL PASS"
            )

            created = create_position(
                state,
                analysis
            )

            if created:
                signals_found += 1

                # Refresh state after creating
                state.clear()
                state.update(
                    load_state()
                )

        except Exception as e:

            print(
                f"   → ANALYSIS ERROR: "
                f"{e}"
            )

    print(
        f"\n[SCAN COMPLETE] "
        f"신규 신호 {signals_found}개"
    )


# ============================================================
# STATISTICS
# ============================================================

def print_statistics(state):
    history = state.get(
        "history",
        []
    )

    if not history:
        return

    results = {}

    for item in history:

        result = item.get(
            "result",
            "UNKNOWN"
        )

        results[result] = (
            results.get(result, 0)
            + 1
        )

    print(
        "\n=============================="
    )
    print(
        "TRACKING STATISTICS"
    )
    print(
        "=============================="
    )

    print(
        f"History: {len(history)}"
    )

    for result, count in sorted(
        results.items()
    ):
        print(
            f"{result}: {count}"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "================================================================="
    )

    print(
        "UPBIT SMART SIGNAL BOT V4.1"
    )

    print(
        "================================================================="
    )

    print(
        f"[TELEGRAM] "
        f"TOKEN={'OK' if TELEGRAM_TOKEN else 'MISSING'} "
        f"CHAT_ID={'OK' if TELEGRAM_CHAT_ID else 'MISSING'}"
    )

    # --------------------------------------------------------
    # Load and automatically migrate old state
    # --------------------------------------------------------

    state = load_state()

    old_position_count = len(
        state.get(
            "positions",
            {}
        )
    )

    migrate_positions(
        state
    )

    new_position_count = len(
        state.get(
            "positions",
            {}
        )
    )

    if old_position_count != new_position_count:
        print(
            f"[MIGRATION] "
            f"{old_position_count} → "
            f"{new_position_count} positions"
        )

    # Save migration immediately
    save_state(
        state
    )

    # --------------------------------------------------------
    # STEP 1
    # --------------------------------------------------------

    print(
        "\n[STEP 1] 기존 포지션 추적"
    )

    track_positions(
        state
    )

    # --------------------------------------------------------
    # Reload after tracking
    # --------------------------------------------------------

    state = load_state()

    migrate_positions(
        state
    )

    # --------------------------------------------------------
    # STEP 2
    # --------------------------------------------------------

    print(
        "\n[STEP 2] 업비트 전체 시장 신규 신호 탐색"
    )

    btc = get_btc_regime()

    scan_market(
        state,
        btc
    )

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    state = load_state()

    print_statistics(
        state
    )

    # --------------------------------------------------------
    # Final save
    # --------------------------------------------------------

    save_state(
        state
    )

    print(
        "\n================================================================="
    )

    print(
        "BOT FINISHED"
    )

    print(
        "================================================================="
    )


if __name__ == "__main__":
    main()
