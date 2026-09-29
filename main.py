import os
import json
import time
import math
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta


# ============================================================
# UPBIT SMART SIGNAL BOT V5
# ============================================================
#
# FIXED:
# 1. Upbit daily candle API
# 2. Legacy position migration
# 3. Never silently delete old positions
# 4. Score capped at 100
# 5. Duplicate signal protection
# 6. ENTRY / TP1 / TP2 / TP3 / SL tracking
# 7. BTC market regime
# 8. Full KRW fast scan + deep scan
#
# ============================================================


# ============================================================
# CONFIG
# ============================================================

CACHE_FILE = "tracked_coins.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

UPBIT_BASE_URL = "https://api.upbit.com/v1"

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
            value = (
                value
                .replace(",", "")
                .replace("%", "")
                .strip()
            )

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

    return (
        (new / old) - 1.0
    ) * 100.0


def clamp_score(score):
    return max(
        0,
        min(
            100,
            int(round(score))
        )
    )


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


# ============================================================
# STATE
# ============================================================

def default_state():
    return {
        "positions": {},
        "history": [],
        "sent_signal_ids": [],
        "legacy_positions": {}
    }


def load_state():

    if not os.path.exists(CACHE_FILE):
        return default_state()

    try:

        with open(
            CACHE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if not isinstance(data, dict):
            return default_state()

        data.setdefault(
            "positions",
            {}
        )

        data.setdefault(
            "history",
            []
        )

        data.setdefault(
            "sent_signal_ids",
            []
        )

        data.setdefault(
            "legacy_positions",
            {}
        )

        if not isinstance(
            data["positions"],
            dict
        ):
            data["positions"] = {}

        if not isinstance(
            data["history"],
            list
        ):
            data["history"] = []

        if not isinstance(
            data["sent_signal_ids"],
            list
        ):
            data["sent_signal_ids"] = []

        if not isinstance(
            data["legacy_positions"],
            dict
        ):
            data["legacy_positions"] = {}

        return data

    except Exception as e:

        print(
            f"[STATE ERROR] {e}"
        )

        return default_state()


def save_state(state):

    temp_file = (
        CACHE_FILE +
        ".tmp"
    )

    try:

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                state,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            temp_file,
            CACHE_FILE
        )

    except Exception as e:

        print(
            f"[STATE SAVE ERROR] {e}"
        )


# ============================================================
# LEGACY POSITION MIGRATION
# ============================================================

def first_existing(
    raw,
    keys,
    default=0
):

    for key in keys:

        if key in raw:

            value = raw.get(key)

            if value not in (
                None,
                "",
                0,
                "0"
            ):

                return value

    return default


def normalize_position(
    market,
    raw
):

    if not isinstance(
        raw,
        dict
    ):

        print(
            f"[TRACK WAIT] "
            f"{market}: invalid position"
        )

        return None

    # --------------------------------------------------------
    # ENTRY
    # --------------------------------------------------------

    entry = safe_float(
        first_existing(
            raw,
            [
                "entry",
                "entry_price",
                "buy_price",
                "price",
                "avg_price",
                "average_price"
            ]
        )
    )

    if entry <= 0:

        print(
            f"[TRACK WAIT] "
            f"{market}: entry missing"
        )

        return None

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    stop = safe_float(
        first_existing(
            raw,
            [
                "stop",
                "sl",
                "stop_loss",
                "stop_price",
                "stopPrice"
            ]
        )
    )

    # --------------------------------------------------------
    # TP1
    # --------------------------------------------------------

    tp1 = safe_float(
        first_existing(
            raw,
            [
                "tp1",
                "target1",
                "target_1",
                "take_profit_1",
                "takeProfit1",
                "take_profit"
            ]
        )
    )

    # --------------------------------------------------------
    # TP2
    # --------------------------------------------------------

    tp2 = safe_float(
        first_existing(
            raw,
            [
                "tp2",
                "target2",
                "target_2",
                "take_profit_2",
                "takeProfit2"
            ]
        )
    )

    # --------------------------------------------------------
    # TP3
    # --------------------------------------------------------

    tp3 = safe_float(
        first_existing(
            raw,
            [
                "tp3",
                "target3",
                "target_3",
                "take_profit_3",
                "takeProfit3"
            ]
        )
    )

    # --------------------------------------------------------
    # OLD POSITION REPAIR
    #
    # If SL missing but ENTRY + TP1 exist:
    #
    # TP1 = ENTRY + 1.5R
    #
    # therefore:
    #
    # R = (TP1 - ENTRY) / 1.5
    # SL = ENTRY - R
    # --------------------------------------------------------

    if stop <= 0 and tp1 > entry:

        risk = (
            tp1 - entry
        ) / 1.5

        if risk > 0:

            stop = (
                entry - risk
            )

            print(
                f"[MIGRATION] "
                f"{market}: "
                f"SL reconstructed"
            )

    # --------------------------------------------------------
    # If still missing stop
    # --------------------------------------------------------

    if stop <= 0:

        print(
            f"[TRACK WAIT] "
            f"{market}: "
            f"SL unavailable"
        )

        return None

    # --------------------------------------------------------
    # If TP1 missing, reconstruct
    # --------------------------------------------------------

    risk = abs(
        entry - stop
    )

    if risk <= 0:

        print(
            f"[TRACK WAIT] "
            f"{market}: invalid risk"
        )

        return None

    if tp1 <= 0:

        tp1 = (
            entry +
            risk * 1.5
        )

        print(
            f"[MIGRATION] "
            f"{market}: "
            f"TP1 reconstructed"
        )

    # --------------------------------------------------------
    # TP2
    # --------------------------------------------------------

    if tp2 <= 0:

        tp2 = (
            entry +
            risk * 2.0
        )

    # --------------------------------------------------------
    # TP3
    # --------------------------------------------------------

    if tp3 <= 0:

        tp3 = (
            entry +
            risk * 3.0
        )

    # --------------------------------------------------------
    # STAGE
    # --------------------------------------------------------

    stage = str(
        raw.get(
            "stage",
            raw.get(
                "status",
                "ENTRY"
            )
        )
    ).upper()

    if stage in (
        "OPEN",
        "ACTIVE",
        "NEW"
    ):

        stage = "ENTRY"

    if stage not in (
        "ENTRY",
        "TP1",
        "TP2"
    ):

        stage = "ENTRY"

    # --------------------------------------------------------
    # SIGNAL CANDLE
    # --------------------------------------------------------

    signal_candle = raw.get(
        "signal_candle",
        raw.get(
            "candle_time",
            raw.get(
                "candle",
                ""
            )
        )
    )

    # --------------------------------------------------------
    # SIGNAL ID
    # --------------------------------------------------------

    signal_id = raw.get(
        "signal_id",
        ""
    )

    if not signal_id and signal_candle:

        signal_id = (
            f"{market}|"
            f"{signal_candle}"
        )

    # --------------------------------------------------------
    # CREATED TIME
    # --------------------------------------------------------

    created_at = raw.get(
        "created_at",
        raw.get(
            "entry_time",
            raw.get(
                "time",
                now_kst().isoformat()
            )
        )
    )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = safe_int(
        raw.get(
            "score",
            raw.get(
                "signal_score",
                0
            )
        )
    )

    # --------------------------------------------------------
    # CANONICAL RECORD
    # --------------------------------------------------------

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

        "score": clamp_score(
            score
        ),

        "direction": raw.get(
            "direction",
            "LONG"
        )
    }

    return normalized


def migrate_positions(state):

    positions = state.get(
        "positions",
        {}
    )

    if not isinstance(
        positions,
        dict
    ):

        return

    migrated = {}

    legacy = state.get(
        "legacy_positions",
        {}
    )

    if not isinstance(
        legacy,
        dict
    ):

        legacy = {}

    original_count = len(
        positions
    )

    for market, raw in positions.items():

        normalized = normalize_position(
            market,
            raw
        )

        if normalized is not None:

            migrated[
                market
            ] = normalized

        else:

            # IMPORTANT:
            # Never silently delete.
            legacy[
                market
            ] = raw

            print(
                f"[LEGACY KEEP] "
                f"{market}"
            )

    state[
        "positions"
    ] = migrated

    state[
        "legacy_positions"
    ] = legacy

    print(
        f"[MIGRATION] "
        f"{original_count} → "
        f"{len(migrated)} active"
    )

    if legacy:

        print(
            f"[LEGACY] "
            f"{len(legacy)} "
            f"positions preserved"
        )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    message
):

    if (
        not TELEGRAM_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "[TELEGRAM] "
            "TOKEN/CHAT_ID missing"
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}"
        "/sendMessage"
    )

    payload = {

        "chat_id":
            TELEGRAM_CHAT_ID,

        "text":
            message,

        "parse_mode":
            "HTML"
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if response.ok:

            print(
                "[TELEGRAM] "
                "전송 성공"
            )

            return True

        print(
            f"[TELEGRAM ERROR] "
            f"{response.status_code}: "
            f"{response.text[:300]}"
        )

    except Exception as e:

        print(
            f"[TELEGRAM ERROR] "
            f"{e}"
        )

    return False


# ============================================================
# UPBIT API
# ============================================================

def upbit_get(
    endpoint,
    params=None
):

    url = (
        UPBIT_BASE_URL +
        endpoint
    )

    try:

        response = requests.get(
            url,
            params=params or {},
            timeout=15
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:

        print(
            f"[API ERROR] "
            f"{endpoint}: {e}"
        )

        return None


def get_markets():

    data = upbit_get(
        "/market/all",
        {
            "isDetails": "false"
        }
    )

    if not data:

        return []

    return [
        x["market"]
        for x in data
        if x.get(
            "market",
            ""
        ).startswith("KRW-")
    ]


def get_tickers(
    markets
):

    if not markets:

        return []

    result = []

    for i in range(
        0,
        len(markets),
        100
    ):

        batch = markets[
            i:i + 100
        ]

        data = upbit_get(
            "/ticker",
            {
                "markets":
                    ",".join(batch)
            }
        )

        if data:

            result.extend(
                data
            )

        time.sleep(
            0.05
        )

    return result


# ============================================================
# CANDLE API
# ============================================================

def get_candles(
    market,
    unit,
    count=200
):

    try:

        # ----------------------------------------------------
        # DAILY CANDLES
        # ----------------------------------------------------

        if unit in (
            1440,
            "1D",
            "D",
            "day",
            "days"
        ):

            endpoint = (
                "/candles/days"
            )

            params = {

                "market":
                    market,

                "count":
                    min(
                        int(count),
                        200
                    )
            }

        # ----------------------------------------------------
        # MINUTE CANDLES
        # ----------------------------------------------------

        else:

            minute_unit = int(
                unit
            )

            allowed = {
                1,
                3,
                5,
                10,
                15,
                30,
                60,
                240
            }

            if (
                minute_unit
                not in allowed
            ):

                print(
                    f"[CANDLE ERROR] "
                    f"Unsupported unit "
                    f"{minute_unit}"
                )

                return None

            endpoint = (
                "/candles/minutes/"
                f"{minute_unit}"
            )

            params = {

                "market":
                    market,

                "count":
                    min(
                        int(count),
                        200
                    )
            }

        data = upbit_get(
            endpoint,
            params
        )

        if not data:

            return None

        df = pd.DataFrame(
            data
        )

        if df.empty:

            return None

        # newest -> oldest
        # convert oldest -> newest
        df = (
            df.iloc[::-1]
            .reset_index(
                drop=True
            )
        )

        df.rename(
            columns={

                "opening_price":
                    "open",

                "high_price":
                    "high",

                "low_price":
                    "low",

                "trade_price":
                    "close",

                "candle_acc_trade_volume":
                    "volume",

                "candle_date_time_kst":
                    "time"
            },
            inplace=True
        )

        required = [
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]

        for col in required:

            if col not in df.columns:

                print(
                    f"[CANDLE ERROR] "
                    f"{market}: "
                    f"{col} missing"
                )

                return None

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df.dropna(
            subset=required,
            inplace=True
        )

        df.reset_index(
            drop=True,
            inplace=True
        )

        if df.empty:

            return None

        return df

    except Exception as e:

        print(
            f"[CANDLE ERROR] "
            f"{market}/{unit}: "
            f"{e}"
        )

        return None


# ============================================================
# INDICATORS
# ============================================================

def ema(
    series,
    length
):

    return series.ewm(
        span=length,
        adjust=False
    ).mean()


def rsi(
    series,
    length=14
):

    delta = (
        series.diff()
    )

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()

    rs = (
        avg_gain /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    result = (
        100 -
        (
            100 /
            (1 + rs)
        )
    )

    return result.fillna(50)


def atr(
    df,
    length=14
):

    previous_close = (
        df["close"].shift(1)
    )

    tr = pd.concat(
        [

            df["high"] -
            df["low"],

            (
                df["high"] -
                previous_close
            ).abs(),

            (
                df["low"] -
                previous_close
            ).abs()

        ],
        axis=1
    ).max(
        axis=1
    )

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
        or len(df15) < 60
        or len(df1h) < 60
    ):

        return {

            "regime":
                "NEUTRAL",

            "score":
                50,

            "change15":
                0,

            "change1h":
                0
        }

    change15 = pct_change(
        df15["close"].iloc[-10],
        df15["close"].iloc[-2]
    )

    change1h = pct_change(
        df1h["close"].iloc[-5],
        df1h["close"].iloc[-2]
    )

    df15["ema20"] = ema(
        df15["close"],
        20
    )

    df15["ema50"] = ema(
        df15["close"],
        50
    )

    df1h["ema20"] = ema(
        df1h["close"],
        20
    )

    df1h["ema50"] = ema(
        df1h["close"],
        50
    )

    score = 50

    if (
        df15["ema20"].iloc[-2]
        >
        df15["ema50"].iloc[-2]
    ):

        score += 10

    else:

        score -= 10

    if (
        df1h["ema20"].iloc[-2]
        >
        df1h["ema50"].iloc[-2]
    ):

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

    score = max(
        0,
        min(
            100,
            score
        )
    )

    if (
        change15 <=
        BTC_15M_CRASH_PCT
        or
        change1h <=
        BTC_1H_CRASH_PCT
    ):

        regime = "CRASH"

    elif score >= 65:

        regime = "BULL"

    elif score <= 35:

        regime = "WEAK"

    else:

        regime = "NEUTRAL"

    print(
        f"[BTC] BTC "
        f"{regime} / Score "
        f"{score}"
    )

    if regime == "CRASH":

        print(
            "🔴 BTC 급락"
        )

    elif regime == "WEAK":

        print(
            "🟠 BTC 약세"
        )

    elif regime == "BULL":

        print(
            "🟢 BTC 강세"
        )

    else:

        print(
            "🟡 BTC 중립"
        )

    return {

        "regime":
            regime,

        "score":
            score,

        "change15":
            change15,

        "change1h":
            change1h
    }


# ============================================================
# FAST SCORE
# ============================================================

def fast_score(
    ticker
):

    change = (
        safe_float(
            ticker.get(
                "signed_change_rate"
            )
        )
        * 100
    )

    trade_value = safe_float(
        ticker.get(
            "acc_trade_price_24h"
        )
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

    if trade_value >= 50_000_000_000:

        score += 40

    elif trade_value >= 20_000_000_000:

        score += 35

    elif trade_value >= 10_000_000_000:

        score += 30

    elif trade_value >= 5_000_000_000:

        score += 25

    elif trade_value >= 1_000_000_000:

        score += 15

    return clamp_score(
        score
    )


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
        df is None
        or len(df) < 60
        for df in [
            df1d,
            df4h,
            df1h,
            df15
        ]
    ):

        return None

    # --------------------------------------------------------
    # INDICATORS
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
    # LAST COMPLETED CANDLES
    # --------------------------------------------------------

    i1d = -2
    i4h = -2
    i1h = -2
    i15 = -2

    price = safe_float(
        ticker.get(
            "trade_price"
        )
    )

    if price <= 0:

        price = safe_float(
            df15["close"].iloc[i15]
        )

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    d1_close = (
        df1d["close"].iloc[i1d]
    )

    d1_ema20 = (
        df1d["ema20"].iloc[i1d]
    )

    d1_ema50 = (
        df1d["ema50"].iloc[i1d]
    )

    h4_close = (
        df4h["close"].iloc[i4h]
    )

    h4_ema20 = (
        df4h["ema20"].iloc[i4h]
    )

    h4_ema50 = (
        df4h["ema50"].iloc[i4h]
    )

    h1_close = (
        df1h["close"].iloc[i1h]
    )

    h1_ema20 = (
        df1h["ema20"].iloc[i1h]
    )

    h1_ema50 = (
        df1h["ema50"].iloc[i1h]
    )

    m15_close = (
        df15["close"].iloc[i15]
    )

    m15_ema20 = (
        df15["ema20"].iloc[i15]
    )

    d1_ema_pass = (
        d1_close >
        d1_ema20
    )

    d1_align = (
        d1_ema20 >
        d1_ema50
    )

    h4_ema_pass = (
        h4_close >
        h4_ema20
    )

    h4_align = (
        h4_ema20 >
        h4_ema50
    )

    h1_ema_pass = (
        h1_close >
        h1_ema20
    )

    h1_align = (
        h1_ema20 >
        h1_ema50
    )

    m15_ema_pass = (
        m15_close >
        m15_ema20
    )

    # --------------------------------------------------------
    # MOMENTUM
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
    # VOLUME
    # --------------------------------------------------------

    recent_volume = safe_float(
        df15["volume"].iloc[i15]
    )

    volume_base = safe_float(
        df15["volume"].iloc[-22:-2].mean()
    )

    if volume_base > 0:

        volume_ratio = (
            recent_volume /
            volume_base *
            100
        )

    else:

        volume_ratio = 0

    # --------------------------------------------------------
    # CANDLE
    # --------------------------------------------------------

    candle = df15.iloc[i15]

    candle_range = (
        candle["high"] -
        candle["low"]
    )

    if candle_range > 0:

        body_ratio = (
            abs(
                candle["close"] -
                candle["open"]
            )
            /
            candle_range
        )

        close_position = (
            candle["close"] -
            candle["low"]
        ) / candle_range

    else:

        body_ratio = 0
        close_position = 0

    candle_pass = (
        candle["close"]
        >
        candle["open"]
        and
        body_ratio
        >=
        MIN_BODY_RATIO
        and
        close_position
        >=
        MIN_CLOSE_POSITION
    )

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    previous_high = safe_float(
        df15["high"].iloc[-22:-2].max()
    )

    breakout_pass = (
        candle["close"]
        >
        previous_high
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    current_rsi = safe_float(
        df15["rsi"].iloc[i15],
        50
    )

    # --------------------------------------------------------
    # ATR
    # --------------------------------------------------------

    current_atr = safe_float(
        df15["atr"].iloc[i15]
    )

    if current_atr <= 0:

        current_atr = (
            price * 0.03
        )

    # --------------------------------------------------------
    # TARGETS
    # --------------------------------------------------------

    stop = (
        price -
        current_atr * 1.5
    )

    risk = (
        price -
        stop
    )

    stop_loss_pct = (
        risk /
        price *
        100
    )

    tp1 = (
        price +
        risk * 1.5
    )

    tp2 = (
        price +
        risk * 2.0
    )

    tp3 = (
        price +
        risk * 3.0
    )

    tp1_distance_pct = (
        (tp1 / price - 1)
        * 100
    )

    # --------------------------------------------------------
    # EMA DISTANCE
    # --------------------------------------------------------

    ema_distance_pct = (
        abs(
            price -
            m15_ema20
        )
        /
        price
        * 100
    )

    # ========================================================
    # SCORE
    # ========================================================

    score = 0

    details = []

    # --------------------------------------------------------
    # 1D = 20
    # --------------------------------------------------------

    if d1_ema_pass:

        score += 7

        details.append(
            "1D EMA20 PASS"
        )

    else:

        details.append(
            "1D EMA20 FAIL"
        )

    if d1_align:

        score += 7

        details.append(
            "1D 정배열 PASS"
        )

    else:

        details.append(
            "1D 정배열 FAIL"
        )

    d1_rising = (
        df1d["ema20"].iloc[i1d]
        >
        df1d["ema20"].iloc[i1d - 3]
    )

    if d1_rising:

        score += 6

        details.append(
            "1D 상승 PASS"
        )

    else:

        details.append(
            "1D 상승 FAIL"
        )

    # --------------------------------------------------------
    # 4H = 20
    # --------------------------------------------------------

    if h4_ema_pass:

        score += 7

        details.append(
            "4H EMA20 PASS"
        )

    else:

        details.append(
            "4H EMA20 FAIL"
        )

    if h4_align:

        score += 7

        details.append(
            "4H 정배열 PASS"
        )

    else:

        details.append(
            "4H 정배열 FAIL"
        )

    h4_rising = (
        df4h["ema20"].iloc[i4h]
        >
        df4h["ema20"].iloc[i4h - 3]
    )

    if h4_rising:

        score += 6

        details.append(
            "4H 상승 PASS"
        )

    else:

        details.append(
            "4H 상승 FAIL"
        )

    # --------------------------------------------------------
    # 1H = 15
    # --------------------------------------------------------

    if h1_ema_pass:

        score += 5

        details.append(
            "1H EMA20 PASS"
        )

    else:

        details.append(
            "1H EMA20 FAIL"
        )

    if h1_align:

        score += 5

        details.append(
            "1H 정배열 PASS"
        )

    else:

        details.append(
            "1H 정배열 FAIL"
        )

    if h1_momentum > 0:

        score += 5

        details.append(
            f"1H 모멘텀 PASS "
            f"{h1_momentum:+.2f}%"
        )

    else:

        details.append(
            f"1H 모멘텀 FAIL "
            f"{h1_momentum:+.2f}%"
        )

    # --------------------------------------------------------
    # 15M = 15
    # --------------------------------------------------------

    if m15_ema_pass:

        score += 5

        details.append(
            "15M EMA20 PASS"
        )

    else:

        details.append(
            "15M EMA20 FAIL"
        )

    m15_ema_rising = (
        df15["ema20"].iloc[i15]
        >
        df15["ema20"].iloc[i15 - 3]
    )

    if m15_ema_rising:

        score += 5

        details.append(
            "15M EMA 상승 PASS"
        )

    else:

        details.append(
            "15M EMA 상승 FAIL"
        )

    if m15_momentum > 0:

        score += 5

        details.append(
            f"15M 모멘텀 PASS "
            f"{m15_momentum:+.2f}%"
        )

    else:

        details.append(
            f"15M 모멘텀 FAIL "
            f"{m15_momentum:+.2f}%"
        )

    # --------------------------------------------------------
    # VOLUME = 15
    # --------------------------------------------------------

    if volume_ratio >= STRONG_VOLUME_RATIO:

        score += 15

        details.append(
            f"거래량 "
            f"{volume_ratio:.0f}% "
            f"(15/15)"
        )

    elif volume_ratio >= MIN_VOLUME_RATIO:

        score += 10

        details.append(
            f"거래량 "
            f"{volume_ratio:.0f}% "
            f"(10/15)"
        )

    else:

        details.append(
            f"거래량 "
            f"{volume_ratio:.0f}% "
            f"(0/15)"
        )

    # --------------------------------------------------------
    # CANDLE = 10
    # --------------------------------------------------------

    if candle_pass:

        score += 10

        details.append(
            "캔들 10/10"
        )

    else:

        details.append(
            "캔들 0/10"
        )

    # --------------------------------------------------------
    # BREAKOUT = 10
    # --------------------------------------------------------

    if breakout_pass:

        score += 10

        details.append(
            "15M 돌파 PASS"
        )

    else:

        details.append(
            "15M 돌파 FAIL"
        )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    if current_rsi >= 82:

        score -= 5

        details.append(
            f"RSI 과열 -5 "
            f"({current_rsi:.1f})"
        )

    elif current_rsi >= 70:

        details.append(
            f"RSI 강세 "
            f"({current_rsi:.1f})"
        )

    elif current_rsi < 50:

        score -= 2

        details.append(
            f"RSI 약세 -2 "
            f"({current_rsi:.1f})"
        )

    else:

        details.append(
            f"RSI 중립 "
            f"({current_rsi:.1f})"
        )

    # --------------------------------------------------------
    # BTC
    # --------------------------------------------------------

    btc_regime = btc[
        "regime"
    ]

    if btc_regime == "BULL":

        score += 5

        details.append(
            "BTC 강세 +5"
        )

    elif btc_regime == "WEAK":

        score -= 3

        details.append(
            "BTC 약세 -3"
        )

    elif btc_regime == "CRASH":

        score -= 15

        details.append(
            "BTC 급락 -15"
        )

    else:

        details.append(
            "BTC 중립"
        )

    # --------------------------------------------------------
    # 4H OVERHEAT
    # --------------------------------------------------------

    h4_change = pct_change(
        df4h["close"].iloc[-10],
        df4h["close"].iloc[i4h]
    )

    if h4_change > 18:

        score -= 8

        details.append(
            f"4H 과열 -8 "
            f"{h4_change:+.2f}%"
        )

    # --------------------------------------------------------
    # EMA DISTANCE
    # --------------------------------------------------------

    if (
        ema_distance_pct
        >
        MAX_ENTRY_DISTANCE_FROM_EMA20
    ):

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

    # --------------------------------------------------------
    # TP1 DISTANCE
    # --------------------------------------------------------

    if (
        tp1_distance_pct
        >
        MAX_TP1_DISTANCE_PCT
    ):

        score -= 8

        details.append(
            f"TP1 거리 과다 -8 "
            f"{tp1_distance_pct:.2f}%"
        )

    # --------------------------------------------------------
    # RISK
    # --------------------------------------------------------

    if (
        stop_loss_pct
        >
        MAX_STOP_LOSS_PCT
    ):

        details.append(
            f"SL 과다 FAIL "
            f"{stop_loss_pct:.2f}%"
        )

    elif (
        stop_loss_pct
        <
        MIN_STOP_LOSS_PCT
    ):

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

    # ========================================================
    # FINAL SCORE
    # ========================================================

    raw_score = score

    score = clamp_score(
        score
    )

    # ========================================================
    # FINAL SIGNAL CONDITIONS
    # ========================================================

    final_pass = True

    if (
        stop_loss_pct
        >
        MAX_STOP_LOSS_PCT
    ):

        final_pass = False

    if (
        tp1_distance_pct
        >
        MAX_TP1_DISTANCE_PCT
    ):

        final_pass = False

    if risk <= 0:

        final_pass = False

    if tp1 <= price:

        final_pass = False

    if tp2 <= tp1:

        final_pass = False

    if tp3 <= tp2:

        final_pass = False

    # --------------------------------------------------------
    # BTC CRASH
    # --------------------------------------------------------

    if btc_regime == "CRASH":

        if score < STRONG_SCORE:

            final_pass = False

    # --------------------------------------------------------
    # BTC WEAK
    # --------------------------------------------------------

    elif btc_regime == "WEAK":

        if score < WEAK_BTC_SCORE:

            final_pass = False

    # --------------------------------------------------------
    # NORMAL
    # --------------------------------------------------------

    else:

        if score < SIGNAL_SCORE:

            final_pass = False

    return {

        "market":
            market,

        "price":
            price,

        "score":
            score,

        "raw_score":
            raw_score,

        "entry":
            price,

        "stop":
            stop,

        "tp1":
            tp1,

        "tp2":
            tp2,

        "tp3":
            tp3,

        "stop_loss_pct":
            stop_loss_pct,

        "tp1_distance_pct":
            tp1_distance_pct,

        "ema_distance_pct":
            ema_distance_pct,

        "rsi":
            current_rsi,

        "volume_ratio":
            volume_ratio,

        "h1_momentum":
            h1_momentum,

        "m15_momentum":
            m15_momentum,

        "btc_regime":
            btc_regime,

        "signal_candle":
            str(
                df15["time"].iloc[i15]
            ),

        "final_pass":
            final_pass,

        "details":
            details
    }


# ============================================================
# SIGNAL DUPLICATE PROTECTION
# ============================================================

def signal_already_sent(
    state,
    signal_id
):

    if not signal_id:

        return True

    sent = state.get(
        "sent_signal_ids",
        []
    )

    if signal_id in sent:

        return True

    for pos in state.get(
        "positions",
        {}
    ).values():

        if (
            pos.get(
                "signal_id"
            )
            ==
            signal_id
        ):

            return True

    for item in state.get(
        "history",
        []
    ):

        if (
            item.get(
                "signal_id"
            )
            ==
            signal_id
        ):

            return True

    return False


def remember_signal(
    state,
    signal_id
):

    if not signal_id:

        return

    sent = state.setdefault(
        "sent_signal_ids",
        []
    )

    if (
        signal_id
        not in sent
    ):

        sent.append(
            signal_id
        )

    if len(sent) > 1000:

        state[
            "sent_signal_ids"
        ] = sent[-1000:]


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

    if market in positions:

        return False

    cutoff = (
        datetime.now(timezone.utc)
        -
        timedelta(
            hours=
            SIGNAL_COOLDOWN_HOURS
        )
    )

    for item in state.get(
        "history",
        []
    ):

        if (
            item.get(
                "market"
            )
            != market
        ):

            continue

        created = item.get(
            "created_at",
            ""
        )

        try:

            dt = datetime.fromisoformat(
                str(created).replace(
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
                item.get(
                    "entry"
                )
            )

            if old_price <= 0:

                return False

            distance = (
                abs(
                    price /
                    old_price
                    - 1
                )
                * 100
            )

            if (
                distance
                <
                MIN_NEW_SIGNAL_PRICE_DISTANCE
            ):

                return False

    return True


# ============================================================
# CREATE POSITION
# ============================================================

def create_position(
    state,
    analysis
):

    market = analysis[
        "market"
    ]

    signal_candle = analysis[
        "signal_candle"
    ]

    signal_id = (
        f"{market}|"
        f"{signal_candle}"
    )

    # --------------------------------------------------------
    # Reload state immediately
    # --------------------------------------------------------

    latest = load_state()

    migrate_positions(
        latest
    )

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

        "market":
            market,

        "entry":
            analysis["entry"],

        "stop":
            analysis["stop"],

        "tp1":
            analysis["tp1"],

        "tp2":
            analysis["tp2"],

        "tp3":
            analysis["tp3"],

        "stage":
            "ENTRY",

        "score":
            analysis["score"],

        "signal_id":
            signal_id,

        "signal_candle":
            signal_candle,

        "direction":
            "LONG",

        "created_at":
            now_kst().isoformat()
    }

    latest[
        "positions"
    ][market] = position

    remember_signal(
        latest,
        signal_id
    )

    # SAVE BEFORE TELEGRAM
    save_state(
        latest
    )

    message = (
        "🟢 <b>UPBIT LONG SIGNAL</b>\n"
        "\n"
        f"<b>{market}</b>\n"
        f"Score: <b>"
        f"{analysis['score']}/100"
        f"</b>\n"
        f"BTC: "
        f"{analysis['btc_regime']}\n"
        "\n"
        f"ENTRY: <b>"
        f"{fmt_price(analysis['entry'])}"
        f"</b>\n"
        f"SL: <b>"
        f"{fmt_price(analysis['stop'])}"
        f"</b>\n"
        f"TP1: <b>"
        f"{fmt_price(analysis['tp1'])}"
        f"</b>\n"
        f"TP2: <b>"
        f"{fmt_price(analysis['tp2'])}"
        f"</b>\n"
        f"TP3: <b>"
        f"{fmt_price(analysis['tp3'])}"
        f"</b>\n"
        "\n"
        f"SL Risk: "
        f"{analysis['stop_loss_pct']:.2f}%\n"
        f"RSI: "
        f"{analysis['rsi']:.1f}\n"
        f"Volume: "
        f"{analysis['volume_ratio']:.0f}%\n"
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
            f"Score "
            f"{analysis['score']}"
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

    # Update existing record
    for item in history:

        if (
            signal_id
            and
            item.get(
                "signal_id"
            )
            ==
            signal_id
        ):

            item.update({

                "result":
                    result,

                "exit_price":
                    exit_price,

                "pnl_pct":
                    pnl_pct,

                "closed_at":
                    now_kst().isoformat()
            })

            return

    # New history record
    history.append({

        "market":
            pos.get(
                "market",
                ""
            ),

        "signal_id":
            signal_id,

        "entry":
            pos.get(
                "entry",
                0
            ),

        "exit_price":
            exit_price,

        "result":
            result,

        "pnl_pct":
            pnl_pct,

        "score":
            pos.get(
                "score",
                0
            ),

        "created_at":
            pos.get(
                "created_at",
                ""
            ),

        "closed_at":
            now_kst().isoformat()
    })


# ============================================================
# TRACK POSITIONS
# ============================================================

def track_positions(
    state
):

    positions = state.get(
        "positions",
        {}
    )

    if not positions:

        print(
            "[TRACKING] "
            "활성 포지션 없음"
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

            # ------------------------------------------------
            # Normalize current position
            # ------------------------------------------------

            normalized = normalize_position(
                market,
                positions[market]
            )

            if normalized is None:

                print(
                    f"[TRACK WAIT] "
                    f"{market}"
                )

                continue

            positions[
                market
            ] = normalized

            pos = normalized

            # ------------------------------------------------
            # 1M candles
            # ------------------------------------------------

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
                    f"1M unavailable"
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
            # STOP FIRST
            # ------------------------------------------------

            if low <= stop:

                pnl_pct = (
                    (
                        stop /
                        entry
                    )
                    - 1
                ) * 100

                if stage == "ENTRY":

                    result = (
                        "STOP_LOSS"
                    )

                else:

                    result = (
                        "PROTECTED"
                    )

                add_history_result(
                    state,
                    pos,
                    result,
                    stop,
                    pnl_pct
                )

                send_telegram(
                    f"🔴 <b>{market} "
                    f"{result}</b>\n"
                    f"Entry: "
                    f"{fmt_price(entry)}\n"
                    f"Exit: "
                    f"{fmt_price(stop)}\n"
                    f"PnL: "
                    f"{pnl_pct:+.2f}%"
                )

                del positions[
                    market
                ]

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

            if (
                stage == "ENTRY"
                and
                high >= tp1
            ):

                pos["stage"] = "TP1"

                if (
                    MOVE_SL_TO_ENTRY_AFTER_TP1
                ):

                    pos["stop"] = entry

                add_history_result(
                    state,
                    pos,
                    "TP1",
                    tp1,
                    (
                        tp1 /
                        entry
                        - 1
                    ) * 100
                )

                send_telegram(
                    f"🟢 <b>{market} "
                    f"TP1 HIT</b>\n"
                    f"TP1: "
                    f"{fmt_price(tp1)}\n"
                    f"PnL: "
                    f"{(tp1 / entry - 1) * 100:+.2f}%\n"
                    f"SL → ENTRY"
                )

                changed = True

                print(
                    f"[TP1] "
                    f"{market}"
                )

                continue

            # ------------------------------------------------
            # TP2
            # ------------------------------------------------

            if (
                stage == "TP1"
                and
                high >= tp2
            ):

                pos["stage"] = "TP2"

                if (
                    MOVE_SL_TO_TP1_AFTER_TP2
                ):

                    pos["stop"] = tp1

                add_history_result(
                    state,
                    pos,
                    "TP2",
                    tp2,
                    (
                        tp2 /
                        entry
                        - 1
                    ) * 100
                )

                send_telegram(
                    f"🟢 <b>{market} "
                    f"TP2 HIT</b>\n"
                    f"TP2: "
                    f"{fmt_price(tp2)}\n"
                    f"PnL: "
                    f"{(tp2 / entry - 1) * 100:+.2f}%\n"
                    f"SL → TP1"
                )

                changed = True

                print(
                    f"[TP2] "
                    f"{market}"
                )

                continue

            # ------------------------------------------------
            # TP3
            # ------------------------------------------------

            if (
                stage == "TP2"
                and
                high >= tp3
            ):

                pnl_pct = (
                    (
                        tp3 /
                        entry
                    )
                    - 1
                ) * 100

                add_history_result(
                    state,
                    pos,
                    "TP3",
                    tp3,
                    pnl_pct
                )

                send_telegram(
                    f"🔵 <b>{market} "
                    f"TP3 HIT</b>\n"
                    f"Entry: "
                    f"{fmt_price(entry)}\n"
                    f"TP3: "
                    f"{fmt_price(tp3)}\n"
                    f"PnL: "
                    f"{pnl_pct:+.2f}%"
                )

                del positions[
                    market
                ]

                changed = True

                print(
                    f"[TP3] "
                    f"{market} | "
                    f"{pnl_pct:+.2f}%"
                )

                continue

        except Exception as e:

            print(
                f"[TRACK ERROR] "
                f"{market}: {e}"
            )

    if changed:

        save_state(
            state
        )


# ============================================================
# MARKET SCAN
# ============================================================

def scan_market(
    state,
    btc
):

    print(
        "\n[SCAN] "
        "업비트 전체 시장 신규 신호 탐색"
    )

    markets = get_markets()

    if not markets:

        print(
            "[SCAN ERROR] "
            "KRW market unavailable"
        )

        return

    print(
        f"[MARKET] "
        f"전체 KRW 마켓 "
        f"{len(markets)}개 확인"
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
            <
            MIN_FINAL_24H_TRADE_VALUE
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

            "market":
                market,

            "ticker":
                ticker,

            "trade_value":
                trade_value,

            "change":
                change,

            "score":
                score
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
        f"1차 후보 "
        f"{len(candidates)}개"
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
            f"24H "
            f"{item['change']:+.2f}% | "
            f"Score "
            f"{item['score']} | "
            f"{item['trade_value'] / 100000000:.1f}억"
        )

    deep_candidates = (
        candidates[:MAX_DEEP_SCAN]
    )

    print(
        f"[DEEP SCAN] "
        f"{len(deep_candidates)}개 정밀분석"
    )

    signals_found = 0

    for index, item in enumerate(
        deep_candidates,
        start=1
    ):

        market = item[
            "market"
        ]

        print(
            f"\n[{index}/"
            f"{len(deep_candidates)}] "
            f"{market} | "
            f"24H "
            f"{item['change']:+.2f}% "
            f"| FAST "
            f"{item['score']}"
        )

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
                    "   → "
                    "최종 SIGNAL FAIL"
                )

                continue

            print(
                "   → ★ "
                "FINAL SIGNAL PASS"
            )

            created = create_position(
                state,
                analysis
            )

            if created:

                signals_found += 1

                state.clear()

                state.update(
                    load_state()
                )

        except Exception as e:

            print(
                f"   → "
                f"ANALYSIS ERROR: "
                f"{e}"
            )

    print(
        f"\n[SCAN COMPLETE] "
        f"신규 신호 "
        f"{signals_found}개"
    )


# ============================================================
# STATISTICS
# ============================================================

def print_statistics(
    state
):

    history = state.get(
        "history",
        []
    )

    if not history:

        return

    result_counts = {}

    for item in history:

        result = item.get(
            "result",
            "UNKNOWN"
        )

        result_counts[
            result
        ] = (
            result_counts.get(
                result,
                0
            )
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
        f"History: "
        f"{len(history)}"
    )

    for result, count in sorted(
        result_counts.items()
    ):

        print(
            f"{result}: "
            f"{count}"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "================================================================="
    )

    print(
        "UPBIT SMART SIGNAL BOT V5"
    )

    print(
        "================================================================="
    )

    print(
        f"[TELEGRAM] "
        f"TOKEN="
        f"{'OK' if TELEGRAM_TOKEN else 'MISSING'} "
        f"CHAT_ID="
        f"{'OK' if TELEGRAM_CHAT_ID else 'MISSING'}"
    )

    # --------------------------------------------------------
    # LOAD
    # --------------------------------------------------------

    state = load_state()

    # --------------------------------------------------------
    # MIGRATION
    # --------------------------------------------------------

    print(
        "\n[STATE] "
        "기존 포지션 확인"
    )

    migrate_positions(
        state
    )

    save_state(
        state
    )

    # --------------------------------------------------------
    # STEP 1
    # --------------------------------------------------------

    print(
        "\n[STEP 1] "
        "기존 포지션 추적"
    )

    track_positions(
        state
    )

    # --------------------------------------------------------
    # RELOAD
    # --------------------------------------------------------

    state = load_state()

    # --------------------------------------------------------
    # STEP 2
    # --------------------------------------------------------

    print(
        "\n[STEP 2] "
        "업비트 전체 시장 신규 신호 탐색"
    )

    btc = get_btc_regime()

    scan_market(
        state,
        btc
    )

    # --------------------------------------------------------
    # FINAL STATE
    # --------------------------------------------------------

    state = load_state()

    print_statistics(
        state
    )

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
