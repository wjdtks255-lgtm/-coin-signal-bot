import os
import json
import time
import requests
import pandas as pd
import numpy as np

from datetime import datetime, timedelta, timezone


# ============================================================
# UPBIT SMART SIGNAL BOT V5.2
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

STATE_FILE = "tracked_coins.json"

UPBIT_API = "https://api.upbit.com/v1"

KST = timezone(
    timedelta(hours=9)
)


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

MAX_TP1_DISTANCE_PCT = 15.0

SIGNAL_COOLDOWN_HOURS = 6

MIN_NEW_SIGNAL_PRICE_DISTANCE = 2.0

BTC_15M_CRASH_PCT = -1.5
BTC_1H_CRASH_PCT = -2.0

MOVE_SL_TO_ENTRY_AFTER_TP1 = True
MOVE_SL_TO_TP1_AFTER_TP2 = True


# ============================================================
# TIME
# ============================================================

def now_kst():

    return datetime.now(KST)


# ============================================================
# PRICE FORMAT
# ============================================================

def fmt_price(price):

    if price is None:
        return "-"

    price = float(price)

    if price >= 1000:
        return f"{price:,.0f}"

    if price >= 100:
        return f"{price:,.1f}"

    if price >= 10:
        return f"{price:,.2f}"

    if price >= 1:
        return f"{price:,.3f}"

    return f"{price:,.6f}"


def pct_text(value):

    return f"{value:+.2f}%"


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN 없음")
        return False

    if not TELEGRAM_CHAT_ID:
        print("TELEGRAM_CHAT_ID 없음")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if response.status_code == 200:
            return True

        print(
            "Telegram 오류:",
            response.status_code,
            response.text[:500]
        )

    except Exception as e:

        print(
            "Telegram 전송 오류:",
            e
        )

    return False


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

    if not os.path.exists(STATE_FILE):

        return default_state()

    try:

        with open(
            STATE_FILE,
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

        return data

    except Exception as e:

        print(
            "상태 파일 읽기 오류:",
            e
        )

        return default_state()


def save_state(state):

    temp_file = STATE_FILE + ".tmp"

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
            STATE_FILE
        )

    except Exception as e:

        print(
            "상태 저장 오류:",
            e
        )


# ============================================================
# LEGACY POSITION
# ============================================================

def first_value(
    data,
    keys
):

    for key in keys:

        if key not in data:
            continue

        value = data.get(key)

        if value is None:
            continue

        try:
            return float(value)
        except:
            pass

    return None


def normalize_position(position):

    if not isinstance(
        position,
        dict
    ):

        return None

    entry = first_value(
        position,
        [
            "entry",
            "entry_price",
            "buy_price",
            "price",
            "avg_price",
            "average_price"
        ]
    )

    stop = first_value(
        position,
        [
            "stop",
            "sl",
            "stop_loss",
            "stop_price",
            "stopPrice"
        ]
    )

    tp1 = first_value(
        position,
        [
            "tp1",
            "target1",
            "target_1",
            "take_profit_1",
            "takeProfit1",
            "take_profit"
        ]
    )

    tp2 = first_value(
        position,
        [
            "tp2",
            "target2",
            "target_2",
            "take_profit_2",
            "takeProfit2"
        ]
    )

    tp3 = first_value(
        position,
        [
            "tp3",
            "target3",
            "target_3",
            "take_profit_3",
            "takeProfit3"
        ]
    )

    if entry is None:
        return None

    if (
        stop is None
        and tp1 is not None
        and tp1 > entry
    ):

        risk = (
            tp1 - entry
        ) / 1.5

        stop = entry - risk

    if tp1 is None:

        if (
            stop is not None
            and stop < entry
        ):

            risk = entry - stop

        else:

            risk = entry * 0.03

        tp1 = entry + (
            risk * 1.5
        )

    if tp2 is None:

        risk = (
            tp1 - entry
        ) / 1.5

        tp2 = entry + (
            risk * 2.0
        )

    if tp3 is None:

        risk = (
            tp1 - entry
        ) / 1.5

        tp3 = entry + (
            risk * 3.0
        )

    if stop is None:

        risk = (
            tp1 - entry
        ) / 1.5

        stop = entry - risk

    normalized = dict(position)

    normalized["entry"] = entry
    normalized["stop"] = stop
    normalized["tp1"] = tp1
    normalized["tp2"] = tp2
    normalized["tp3"] = tp3

    normalized.setdefault(
        "stage",
        "ENTRY"
    )

    normalized.setdefault(
        "direction",
        "LONG"
    )

    return normalized


def migrate_positions(state):

    positions = state.get(
        "positions",
        {}
    )

    invalid = {}

    for market, position in list(
        positions.items()
    ):

        normalized = normalize_position(
            position
        )

        if normalized is None:

            invalid[market] = position

            del positions[
                market
            ]

            continue

        positions[
            market
        ] = normalized

    if invalid:

        legacy = state.setdefault(
            "legacy_positions",
            {}
        )

        for market, position in invalid.items():

            legacy[
                market
            ] = position

    return state


# ============================================================
# SIGNAL DUPLICATE PROTECTION
# ============================================================

def signal_already_sent(
    state,
    signal_id
):

    return (
        signal_id
        in state.get(
            "sent_signal_ids",
            []
        )
    )


def remember_signal(
    state,
    signal_id
):

    ids = state.setdefault(
        "sent_signal_ids",
        []
    )

    if signal_id not in ids:

        ids.append(
            signal_id
        )

    state[
        "sent_signal_ids"
    ] = ids[-1000:]


# ============================================================
# UPBIT MARKETS
# ============================================================

def get_markets():

    try:

        response = requests.get(
            f"{UPBIT_API}/market/all",
            params={
                "isDetails": "false"
            },
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        return [
            item["market"]
            for item in data
            if item["market"].startswith(
                "KRW-"
            )
        ]

    except Exception as e:

        print(
            "마켓 조회 오류:",
            e
        )

        return []


def get_tickers(markets):

    result = []

    for i in range(
        0,
        len(markets),
        100
    ):

        chunk = markets[
            i:i + 100
        ]

        try:

            response = requests.get(
                f"{UPBIT_API}/ticker",
                params={
                    "markets": ",".join(
                        chunk
                    )
                },
                timeout=15
            )

            response.raise_for_status()

            result.extend(
                response.json()
            )

        except Exception as e:

            print(
                "Ticker 오류:",
                e
            )

    return result


# ============================================================
# CANDLES
# ============================================================

def get_candles(
    market,
    unit,
    count=200
):

    if unit == 1440:

        url = (
            f"{UPBIT_API}/candles/days"
        )

    else:

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

        if unit not in allowed:

            return pd.DataFrame()

        url = (
            f"{UPBIT_API}/candles/"
            f"minutes/{unit}"
        )

    try:

        response = requests.get(
            url,
            params={
                "market": market,
                "count": min(
                    count,
                    200
                )
            },
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        if not data:

            return pd.DataFrame()

        df = pd.DataFrame(
            data
        )

        df = df.rename(
            columns={
                "opening_price": "open",
                "high_price": "high",
                "low_price": "low",
                "trade_price": "close",
                "candle_acc_trade_volume": "volume",
                "candle_acc_trade_price": "value"
            }
        )

        df = df.sort_values(
            "candle_date_time_kst"
        ).reset_index(
            drop=True
        )

        return df

    except Exception as e:

        print(
            f"캔들 오류 "
            f"{market} {unit}:",
            e
        )

        return pd.DataFrame()


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

    delta = series.diff()

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
        avg_gain
        /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    result = (
        100
        -
        (
            100
            /
            (1 + rs)
        )
    )

    return result.fillna(50)


def atr(
    df,
    length=14
):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(
        1
    )

    tr = pd.concat(
        [
            high - low,
            (
                high - previous_close
            ).abs(),
            (
                low - previous_close
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
# TREND
# ============================================================

def timeframe_trend(df):

    if (
        df.empty
        or len(df) < 60
    ):

        return "FAIL"

    close = df["close"]

    ema20 = ema(
        close,
        20
    )

    ema50 = ema(
        close,
        50
    )

    current = close.iloc[-1]

    if (
        current > ema20.iloc[-1]
        and
        ema20.iloc[-1]
        >
        ema50.iloc[-1]
    ):

        return "PASS"

    return "FAIL"


# ============================================================
# BTC REGIME
# ============================================================

def btc_regime_text(
    regime
):

    return {
        "BULL": "강세",
        "WEAK": "약세",
        "CRASH": "급락",
        "NEUTRAL": "중립"
    }.get(
        regime,
        "중립"
    )


def get_btc_regime():

    df15 = get_candles(
        "KRW-BTC",
        15,
        100
    )

    df1h = get_candles(
        "KRW-BTC",
        60,
        100
    )

    if (
        df15.empty
        or df1h.empty
        or len(df15) < 20
        or len(df1h) < 20
    ):

        return (
            "NEUTRAL",
            0,
            0
        )

    change15 = (
        df15["close"].iloc[-1]
        /
        df15["close"].iloc[-2]
        - 1
    ) * 100

    change1h = (
        df1h["close"].iloc[-1]
        /
        df1h["close"].iloc[-2]
        - 1
    ) * 100

    if (
        change15 <= BTC_15M_CRASH_PCT
        or
        change1h <= BTC_1H_CRASH_PCT
    ):

        return (
            "CRASH",
            change15,
            change1h
        )

    if (
        change15 < -0.7
        or
        change1h < -1.0
    ):

        return (
            "WEAK",
            change15,
            change1h
        )

    if (
        change15 > 0.7
        and
        change1h > 1.0
    ):

        return (
            "BULL",
            change15,
            change1h
        )

    return (
        "NEUTRAL",
        change15,
        change1h
    )


# ============================================================
# FAST SCAN
# ============================================================

def fast_candidate_scan():

    markets = get_markets()

    if not markets:

        return []

    tickers = get_tickers(
        markets
    )

    candidates = []

    for ticker in tickers:

        market = ticker.get(
            "market"
        )

        trade_value = float(
            ticker.get(
                "acc_trade_price_24h",
                0
            )
        )

        price = float(
            ticker.get(
                "trade_price",
                0
            )
        )

        change = float(
            ticker.get(
                "signed_change_rate",
                0
            )
        ) * 100

        if price <= 0:
            continue

        if (
            trade_value
            <
            MIN_FINAL_24H_TRADE_VALUE
        ):

            continue

        candidates.append(
            {
                "market": market,
                "price": price,
                "trade_value": trade_value,
                "change": change
            }
        )

    candidates.sort(
        key=lambda x:
        x["trade_value"],
        reverse=True
    )

    return candidates


# ============================================================
# MARKET ANALYSIS
# ============================================================

def analyze_market(
    market,
    ticker,
    btc_regime
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
        120
    )

    df15 = get_candles(
        market,
        15,
        120
    )

    if any(
        df.empty
        for df in [
            df1d,
            df4h,
            df1h,
            df15
        ]
    ):

        return None, "캔들 데이터 부족"

    if min(
        len(df1d),
        len(df4h),
        len(df1h),
        len(df15)
    ) < 60:

        return None, "데이터 개수 부족"

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    trend1d = timeframe_trend(
        df1d
    )

    trend4h = timeframe_trend(
        df4h
    )

    trend1h = timeframe_trend(
        df1h
    )

    trend15 = timeframe_trend(
        df15
    )

    if trend15 != "PASS":

        return None, "15M 추세 FAIL"

    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    price = float(
        df15["close"].iloc[-1]
    )

    ema20_value = float(
        ema(
            df15["close"],
            20
        ).iloc[-1]
    )

    atr_value = float(
        atr(
            df15,
            14
        ).iloc[-1]
    )

    if atr_value <= 0:

        return None, "ATR 오류"

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi_value = float(
        rsi(
            df15["close"],
            14
        ).iloc[-1]
    )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    average_volume = float(
        df15[
            "volume"
        ].iloc[
            -21:-1
        ].mean()
    )

    current_volume = float(
        df15[
            "volume"
        ].iloc[-1]
    )

    if average_volume <= 0:

        return None, "거래량 데이터 오류"

    volume_ratio = (
        current_volume
        /
        average_volume
    ) * 100

    if volume_ratio < MIN_VOLUME_RATIO:

        return (
            None,
            f"거래량 부족 {volume_ratio:.0f}%"
        )

    # --------------------------------------------------------
    # CANDLE
    # --------------------------------------------------------

    candle = df15.iloc[-1]

    candle_range = (
        float(candle["high"])
        -
        float(candle["low"])
    )

    if candle_range <= 0:

        return None, "캔들 범위 오류"

    body = abs(
        float(candle["close"])
        -
        float(candle["open"])
    )

    body_ratio = (
        body
        /
        candle_range
    )

    close_position = (
        float(candle["close"])
        -
        float(candle["low"])
    ) / candle_range

    candle_pass = (
        body_ratio
        >=
        MIN_BODY_RATIO
        and
        close_position
        >=
        MIN_CLOSE_POSITION
        and
        candle["close"]
        >
        candle["open"]
    )

    if not candle_pass:

        return None, "15M 캔들 조건 FAIL"

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    previous_high = float(
        df15[
            "high"
        ].iloc[
            -21:-1
        ].max()
    )

    breakout_pass = (
        price
        >
        previous_high
    )

    if not breakout_pass:

        return None, "돌파 조건 FAIL"

    # --------------------------------------------------------
    # EMA DISTANCE
    # --------------------------------------------------------

    ema_distance = (
        abs(
            price
            -
            ema20_value
        )
        /
        ema20_value
    ) * 100

    if (
        ema_distance
        >
        MAX_ENTRY_DISTANCE_FROM_EMA20
    ):

        return (
            None,
            f"EMA20 이격 {ema_distance:.2f}%"
        )

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    support = float(
        df15[
            "low"
        ].iloc[
            -15:-1
        ].min()
    )

    stop = (
        support
        -
        atr_value * 0.25
    )

    if stop >= price:

        return None, "손절가 계산 오류"

    stop_loss_pct = (
        (
            price
            -
            stop
        )
        /
        price
    ) * 100

    if (
        stop_loss_pct
        <
        MIN_STOP_LOSS_PCT
    ):

        return (
            None,
            f"손절폭 너무 작음 {stop_loss_pct:.2f}%"
        )

    if (
        stop_loss_pct
        >
        MAX_STOP_LOSS_PCT
    ):

        return (
            None,
            f"손절폭 너무 큼 {stop_loss_pct:.2f}%"
        )

    # --------------------------------------------------------
    # TARGETS
    # --------------------------------------------------------

    risk = (
        price
        -
        stop
    )

    tp1 = price + (
        risk * 1.5
    )

    tp2 = price + (
        risk * 2.0
    )

    tp3 = price + (
        risk * 3.0
    )

    tp1_distance = (
        (
            tp1
            -
            price
        )
        /
        price
    ) * 100

    if (
        tp1_distance
        >
        MAX_TP1_DISTANCE_PCT
    ):

        return (
            None,
            f"TP1 거리 과다 {tp1_distance:.2f}%"
        )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 0

    if trend1d == "PASS":
        score += 20

    if trend4h == "PASS":
        score += 20

    if trend1h == "PASS":
        score += 15

    if trend15 == "PASS":
        score += 15

    if volume_ratio >= STRONG_VOLUME_RATIO:

        score += 15

    elif volume_ratio >= MIN_VOLUME_RATIO:

        score += 10

    if candle_pass:
        score += 10

    if breakout_pass:
        score += 10

    score += 5

    # --------------------------------------------------------
    # PENALTY
    # --------------------------------------------------------

    if rsi_value >= 82:

        score -= 5

    elif rsi_value < 50:

        score -= 2

    df4h_change = (
        df4h["close"].iloc[-1]
        /
        df4h["close"].iloc[-2]
        - 1
    ) * 100

    if df4h_change > 18:

        score -= 8

    if ema_distance > 6:

        score -= 7

    if tp1_distance > 15:

        score -= 8

    if btc_regime == "BULL":

        score += 5

    elif btc_regime == "WEAK":

        score -= 3

    elif btc_regime == "CRASH":

        score -= 15

    score = max(
        0,
        min(
            100,
            int(score)
        )
    )

    # --------------------------------------------------------
    # FINAL SCORE
    # --------------------------------------------------------

    if btc_regime == "CRASH":

        if score < STRONG_SCORE:

            return (
                None,
                f"BTC 급락 + 점수 {score}"
            )

    elif btc_regime == "WEAK":

        if score < WEAK_BTC_SCORE:

            return (
                None,
                f"약세 BTC + 점수 {score}"
            )

        if (
            volume_ratio
            <
            STRONG_VOLUME_RATIO
        ):

            return (
                None,
                f"약세 BTC 거래량 부족 {volume_ratio:.0f}%"
            )

    else:

        if score < SIGNAL_SCORE:

            return (
                None,
                f"점수 부족 {score}"
            )

    signal_candle = str(
        df15[
            "candle_date_time_kst"
        ].iloc[-1]
    )

    return (
        {
            "market": market,

            "price": price,

            "entry": price,

            "stop": stop,

            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,

            "score": score,

            "rsi": rsi_value,

            "volume_ratio":
                volume_ratio,

            "stop_loss_pct":
                stop_loss_pct,

            "btc_regime":
                btc_regime,

            "signal_candle":
                signal_candle,

            "ema_distance":
                ema_distance
        },
        "PASS"
    )


# ============================================================
# COOLDOWN
# ============================================================

def can_create_new_signal(
    state,
    market,
    price
):

    current_time = now_kst()

    for item in reversed(
        state.get(
            "history",
            []
        )
    ):

        if item.get(
            "market"
        ) != market:

            continue

        created = item.get(
            "created_at"
        )

        if not created:

            continue

        try:

            created_dt = (
                datetime.fromisoformat(
                    created
                )
            )

            if (
                created_dt.tzinfo
                is None
            ):

                created_dt = (
                    created_dt.replace(
                        tzinfo=KST
                    )
                )

            age_hours = (
                current_time
                -
                created_dt
            ).total_seconds() / 3600

            if (
                age_hours
                <
                SIGNAL_COOLDOWN_HOURS
            ):

                old_price = float(
                    item.get(
                        "entry",
                        price
                    )
                )

                distance = (
                    abs(
                        price
                        -
                        old_price
                    )
                    /
                    old_price
                ) * 100

                if (
                    distance
                    <
                    MIN_NEW_SIGNAL_PRICE_DISTANCE
                ):

                    return False

        except:

            continue

    return True


# ============================================================
# LONG ALERT
# ============================================================

def build_long_alert(
    analysis
):

    entry = analysis[
        "entry"
    ]

    stop = analysis[
        "stop"
    ]

    tp1 = analysis[
        "tp1"
    ]

    tp2 = analysis[
        "tp2"
    ]

    tp3 = analysis[
        "tp3"
    ]

    tp1_pct = (
        (tp1 / entry)
        - 1
    ) * 100

    tp2_pct = (
        (tp2 / entry)
        - 1
    ) * 100

    tp3_pct = (
        (tp3 / entry)
        - 1
    ) * 100

    rr1 = (
        (tp1 - entry)
        /
        (entry - stop)
    )

    rr2 = (
        (tp2 - entry)
        /
        (entry - stop)
    )

    rr3 = (
        (tp3 - entry)
        /
        (entry - stop)
    )

    btc_text = btc_regime_text(
        analysis[
            "btc_regime"
        ]
    )

    return (
        "🟢 <b>UPBIT LONG SIGNAL</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"💰 <b>{analysis['market']}</b>\n"
        f"📊 신호 점수 "
        f"<b>{analysis['score']} / 100</b>\n"
        f"₿ BTC 상태 "
        f"<b>{btc_text}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "🎯 <b>매매 계획</b>\n\n"

        f"ENTRY  <b>{fmt_price(entry)}</b>\n"
        f"SL     <b>{fmt_price(stop)}</b>\n\n"

        f"TP1    <b>{fmt_price(tp1)}</b> "
        f"({tp1_pct:+.2f}%)\n"

        f"TP2    <b>{fmt_price(tp2)}</b> "
        f"({tp2_pct:+.2f}%)\n"

        f"TP3    <b>{fmt_price(tp3)}</b> "
        f"({tp3_pct:+.2f}%)\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "🛡️ <b>리스크</b>\n\n"

        f"손절폭     "
        f"<b>{analysis['stop_loss_pct']:.2f}%</b>\n"

        f"TP1 R:R    "
        f"<b>1 : {rr1:.2f}</b>\n"

        f"TP2 R:R    "
        f"<b>1 : {rr2:.2f}</b>\n"

        f"TP3 R:R    "
        f"<b>1 : {rr3:.2f}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "📈 <b>시장 상태</b>\n\n"

        f"RSI        "
        f"<b>{analysis['rsi']:.1f}</b>\n"

        f"거래량     "
        f"<b>{analysis['volume_ratio']:.0f}%</b>\n"

        f"EMA20 이격 "
        f"<b>{analysis['ema_distance']:.2f}%</b>\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "⏱️ <b>15분봉 확정 시그널</b>\n\n"

        "⚠️ 신호 발생 후 추격진입 주의\n"

        "━━━━━━━━━━━━━━━━━━"
    )


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

    latest = load_state()

    migrate_positions(
        latest
    )

    if signal_already_sent(
        latest,
        signal_id
    ):

        print(
            f"[중복 차단] {signal_id}"
        )

        return False

    if market in latest.get(
        "positions",
        {}
    ):

        print(
            f"[기존 포지션] {market}"
        )

        return False

    if not can_create_new_signal(
        latest,
        market,
        analysis["entry"]
    ):

        print(
            f"[쿨다운 차단] {market}"
        )

        return False

    position = {

        "market": market,

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

    latest.setdefault(
        "history",
        []
    ).append(
        {
            "market": market,
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
            "score":
                analysis["score"],
            "signal_id":
                signal_id,
            "created_at":
                now_kst().isoformat()
        }
    )

    latest[
        "history"
    ] = latest[
        "history"
    ][-1000:]

    save_state(
        latest
    )

    message = build_long_alert(
        analysis
    )

    send_telegram(
        message
    )

    print(
        f"[신규 롱] "
        f"{market} "
        f"Score={analysis['score']}"
    )

    return True


# ============================================================
# TP / SL ALERT
# ============================================================

def send_sl_alert(
    market,
    entry,
    current
):

    pnl = (
        (current - entry)
        /
        entry
    ) * 100

    message = (
        "🔴 <b>STOP LOSS</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"💰 <b>{market}</b>\n\n"

        f"진입가     <b>{fmt_price(entry)}</b>\n"
        f"청산가     <b>{fmt_price(current)}</b>\n"
        f"손익률     <b>{pnl:+.2f}%</b>\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "🛑 <b>손절 처리 완료</b>\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    send_telegram(
        message
    )


def send_tp1_alert(
    market,
    entry,
    tp1
):

    pnl = (
        (tp1 - entry)
        /
        entry
    ) * 100

    message = (
        "🟢 <b>TAKE PROFIT 1</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"💰 <b>{market}</b>\n\n"

        f"진입가     <b>{fmt_price(entry)}</b>\n"
        f"TP1        <b>{fmt_price(tp1)}</b>\n"
        f"수익률     <b>+{pnl:.2f}%</b>\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "🛡️ <b>손절가 → 진입가</b>\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    send_telegram(
        message
    )


def send_tp2_alert(
    market,
    entry,
    tp2,
    tp1
):

    pnl = (
        (tp2 - entry)
        /
        entry
    ) * 100

    message = (
        "🟢 <b>TAKE PROFIT 2</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"💰 <b>{market}</b>\n\n"

        f"진입가     <b>{fmt_price(entry)}</b>\n"
        f"TP2        <b>{fmt_price(tp2)}</b>\n"
        f"수익률     <b>+{pnl:.2f}%</b>\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ <b>손절가 → TP1 "
        f"{fmt_price(tp1)}</b>\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    send_telegram(
        message
    )


def send_tp3_alert(
    market,
    entry,
    tp3
):

    pnl = (
        (tp3 - entry)
        /
        entry
    ) * 100

    message = (
        "🏆 <b>TAKE PROFIT 3</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"💰 <b>{market}</b>\n\n"

        f"진입가     <b>{fmt_price(entry)}</b>\n"
        f"최종익절   <b>{fmt_price(tp3)}</b>\n"
        f"최종수익   <b>+{pnl:.2f}%</b>\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "🎯 <b>최종 목표 도달</b>\n"
        "포지션 추적 종료\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    send_telegram(
        message
    )


# ============================================================
# POSITION TRACKING
# ============================================================

def track_positions():

    state = load_state()

    migrate_positions(
        state
    )

    positions = state.get(
        "positions",
        {}
    )

    if not positions:

        save_state(
            state
        )

        return

    changed = False

    for market in list(
        positions.keys()
    ):

        position = positions[
            market
        ]

        df = get_candles(
            market,
            1,
            3
        )

        if df.empty:

            continue

        current = float(
            df["close"].iloc[-1]
        )

        entry = float(
            position["entry"]
        )

        stop = float(
            position["stop"]
        )

        tp1 = float(
            position["tp1"]
        )

        tp2 = float(
            position["tp2"]
        )

        tp3 = float(
            position["tp3"]
        )

        stage = position.get(
            "stage",
            "ENTRY"
        )

        # ----------------------------------------------------
        # STOP
        # ----------------------------------------------------

        if current <= stop:

            send_sl_alert(
                market,
                entry,
                current
            )

            pnl = (
                (current - entry)
                /
                entry
            ) * 100

            state.setdefault(
                "history",
                []
            ).append(
                {
                    "market": market,
                    "result": "SL",
                    "entry": entry,
                    "exit": current,
                    "pnl_pct": pnl,
                    "closed_at":
                        now_kst().isoformat()
                }
            )

            del positions[
                market
            ]

            changed = True

            continue

        # ----------------------------------------------------
        # TP1
        # ----------------------------------------------------

        if (
            stage == "ENTRY"
            and
            current >= tp1
        ):

            if (
                MOVE_SL_TO_ENTRY_AFTER_TP1
            ):

                position[
                    "stop"
                ] = entry

            position[
                "stage"
            ] = "TP1"

            changed = True

            send_tp1_alert(
                market,
                entry,
                tp1
            )

            continue

        # ----------------------------------------------------
        # TP2
        # ----------------------------------------------------

        if (
            stage == "TP1"
            and
            current >= tp2
        ):

            if (
                MOVE_SL_TO_TP1_AFTER_TP2
            ):

                position[
                    "stop"
                ] = tp1

            position[
                "stage"
            ] = "TP2"

            changed = True

            send_tp2_alert(
                market,
                entry,
                tp2,
                tp1
            )

            continue

        # ----------------------------------------------------
        # TP3
        # ----------------------------------------------------

        if (
            stage == "TP2"
            and
            current >= tp3
        ):

            send_tp3_alert(
                market,
                entry,
                tp3
            )

            pnl = (
                (tp3 - entry)
                /
                entry
            ) * 100

            state.setdefault(
                "history",
                []
            ).append(
                {
                    "market": market,
                    "result": "TP3",
                    "entry": entry,
                    "exit": tp3,
                    "pnl_pct": pnl,
                    "closed_at":
                        now_kst().isoformat()
                }
            )

            del positions[
                market
            ]

            changed = True

            continue

    if changed:

        save_state(
            state
        )


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():

    print("=" * 50)
    print(
        "UPBIT SMART SIGNAL BOT V5.2"
    )
    print("=" * 50)

    print(
        "시작 시간:",
        now_kst().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    # --------------------------------------------------------
    # TRACK EXISTING POSITIONS
    # --------------------------------------------------------

    track_positions()

    # --------------------------------------------------------
    # BTC
    # --------------------------------------------------------

    (
        btc_regime,
        btc15,
        btc1h
    ) = get_btc_regime()

    print(
        f"BTC 상태: "
        f"{btc_regime_text(btc_regime)}"
    )

    print(
        f"BTC 15M: "
        f"{btc15:.2f}%"
    )

    print(
        f"BTC 1H : "
        f"{btc1h:.2f}%"
    )

    # --------------------------------------------------------
    # FAST SCAN
    # --------------------------------------------------------

    candidates = (
        fast_candidate_scan()
    )

    print(
        f"KRW 마켓 후보: "
        f"{len(candidates)}"
    )

    if not candidates:

        print(
            "후보 없음"
        )

        return

    candidates = candidates[
        :MAX_DEEP_SCAN
    ]

    print(
        f"정밀 분석: "
        f"{len(candidates)}개"
    )

    signals = []

    # --------------------------------------------------------
    # DEEP SCAN
    # --------------------------------------------------------

    for i, item in enumerate(
        candidates,
        start=1
    ):

        market = item[
            "market"
        ]

        analysis, reason = (
            analyze_market(
                market,
                item,
                btc_regime
            )
        )

        if analysis is None:

            print(
                f"[{i}/{len(candidates)}] "
                f"{market} "
                f"FAIL - {reason}"
            )

            continue

        print(
            f"[{i}/{len(candidates)}] "
            f"{market} "
            f"PASS "
            f"Score={analysis['score']}"
        )

        signals.append(
            analysis
        )

        time.sleep(
            0.05
        )

    # --------------------------------------------------------
    # SORT
    # --------------------------------------------------------

    signals.sort(
        key=lambda x:
        x["score"],
        reverse=True
    )

    print("=" * 50)

    print(
        f"최종 시그널: "
        f"{len(signals)}"
    )

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    state = load_state()

    migrate_positions(
        state
    )

    for analysis in signals:

        try:

            create_position(
                state,
                analysis
            )

        except Exception as e:

            print(
                f"시그널 생성 오류 "
                f"{analysis['market']}:",
                e
            )

    print("=" * 50)
    print(
        "스캔 완료"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        run_scan()

    except Exception as e:

        print(
            "전체 실행 오류:",
            e
        )

        send_telegram(
            "⚠️ <b>봇 실행 오류</b>\n\n"
            f"{str(e)[:1000]}"
        )

        raise
