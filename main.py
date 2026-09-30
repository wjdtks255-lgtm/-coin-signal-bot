import os
import json
import hashlib
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

VERSION = "5.6"

BASE = "https://api.upbit.com/v1"

STATE_FILE = "tracked_coins.json"
LOG_FILE = "bot_log.json"

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# SIGNAL SETTINGS
# ============================================================

MAX_DEEP_SCAN = 80
MIN_24H_VALUE = 1_000_000_000

# V5.5 = 75
# V5.6 = 80
SIGNAL_SCORE = 80
WEAK_SCORE = 85
CRASH_SCORE = 90

# V5.6 stronger volume requirement
MIN_VOLUME_RATIO = 150
STRONG_VOLUME_RATIO = 220

# Do not chase price too far above EMA20
MAX_EMA_DISTANCE = 3.5

# RSI entry zone
MIN_RSI = 52
MAX_RSI = 72

# Minimum ADX
MIN_ADX = 18

# Stop settings
MIN_SL = 1.2
MAX_SL = 6.0

# Maximum TP1 distance
MAX_TP1 = 12.0

# Cooldown
COOLDOWN_HOURS = 8

# Minimum price distance from previous signal
MIN_PRICE_DISTANCE = 2.5

# BTC regime
BTC_15M_CRASH = -1.5
BTC_1H_CRASH = -2.0

# Pullback settings
PULLBACK_LOOKBACK = 8

# Prevent very large candle chasing
MAX_CANDLE_BODY = 2.5

S = requests.Session()

MARKET_INFO = {}


# ============================================================
# BASIC
# ============================================================

def now():
    return datetime.now(timezone.utc)


def fp(x):
    x = float(x)

    if x >= 1000:
        return f"{x:,.2f}"

    if x >= 1:
        return f"{x:,.3f}"

    if x >= 0.01:
        return f"{x:,.4f}"

    return f"{x:,.8f}"


def sf(x, default=0):
    try:
        return float(x)
    except Exception:
        return default


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(path, data):
    temp = path + ".tmp"

    with open(
        temp,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        temp,
        path
    )


def log(event, data=None):

    items = load(
        LOG_FILE,
        []
    )

    if not isinstance(
        items,
        list
    ):
        items = []

    items.append({
        "time": now().isoformat(),
        "event": event,
        "data": data or {}
    })

    save(
        LOG_FILE,
        items[-1000:]
    )


# ============================================================
# UPBIT API
# ============================================================

def up(path, params=None):

    try:

        r = S.get(
            BASE + path,
            params=params,
            timeout=15
        )

        r.raise_for_status()

        return r.json()

    except Exception as e:

        print(
            "Upbit API:",
            e
        )

        return None


# ============================================================
# DYNAMIC MARKET DISCOVERY
# ============================================================

def refresh_market_info():

    global MARKET_INFO

    data = up(
        "/market/all",
        {
            "isDetails": "true"
        }
    ) or []

    info = {}

    for item in data:

        market = item.get(
            "market",
            ""
        )

        if not market.startswith(
            "KRW-"
        ):
            continue

        ticker = market.replace(
            "KRW-",
            ""
        ).upper()

        info[market] = {
            "ticker": ticker,
            "korean_name":
                item.get(
                    "korean_name"
                ) or ticker,
            "english_name":
                item.get(
                    "english_name"
                ) or ticker
        }

    MARKET_INFO = info

    return info


def display_name(market):

    info = MARKET_INFO.get(
        market
    )

    if info:

        return (
            f'{info["korean_name"]} '
            f'({info["ticker"]})'
        )

    return market.replace(
        "KRW-",
        ""
    ).upper()


def markets():

    refresh_market_info()

    return list(
        MARKET_INFO.keys()
    )


def tickers(market_list):

    if not market_list:
        return []

    return up(
        "/ticker",
        {
            "markets":
                ",".join(
                    market_list
                )
        }
    ) or []


# ============================================================
# TELEGRAM
# ============================================================

def tg(message):

    if not TOKEN or not CHAT:

        print(
            "Telegram credentials missing."
        )

        return False

    try:

        r = S.post(
            f"https://api.telegram.org/"
            f"bot{TOKEN}/sendMessage",

            json={
                "chat_id": CHAT,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            },

            timeout=15
        )

        if r.ok:
            print("Telegram sent.")
        else:
            print(
                "Telegram error:",
                r.status_code
            )

        return r.ok

    except Exception as e:

        print(
            "Telegram error:",
            e
        )

        return False


def tv(market):

    ticker = market.replace(
        "KRW-",
        ""
    ).upper()

    return (
        "https://www.tradingview.com/"
        f"symbols/UPBIT-{ticker}KRW/"
    )


# ============================================================
# CANDLES
# ============================================================

def candles(
    market,
    unit=15,
    count=200
):

    if unit == 1440:
        path = "/candles/days"
    else:
        path = (
            f"/candles/minutes/{unit}"
        )

    data = up(
        path,
        {
            "market": market,
            "count": count
        }
    )

    if (
        not isinstance(data, list)
        or len(data) < 50
    ):
        return None

    df = pd.DataFrame(
        data
    )

    df = df.rename(
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

            "candle_acc_trade_price":
                "trade_value"
        }
    )

    required = [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]

    if any(
        c not in df.columns
        for c in required
    ):
        return None

    df[required] = df[
        required
    ].apply(
        pd.to_numeric,
        errors="coerce"
    )

    df = df.dropna(
        subset=required
    )

    return (
        df.iloc[::-1]
        .reset_index(drop=True)
    )


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
        avg_gain /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    return (
        100 -
        100 / (1 + rs)
    ).fillna(50)


def atr(
    df,
    length=14
):

    previous_close = (
        df.close.shift()
    )

    tr = pd.concat(
        [
            df.high - df.low,

            (
                df.high -
                previous_close
            ).abs(),

            (
                df.low -
                previous_close
            ).abs()
        ],
        axis=1
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()


def adx(
    df,
    length=14
):

    up_move = df.high.diff()

    down_move = -df.low.diff()

    plus_dm = pd.Series(
        np.where(
            (
                (up_move > down_move)
                &
                (up_move > 0)
            ),
            up_move,
            0
        ),
        index=df.index
    )

    minus_dm = pd.Series(
        np.where(
            (
                (down_move > up_move)
                &
                (down_move > 0)
            ),
            down_move,
            0
        ),
        index=df.index
    )

    previous_close = (
        df.close.shift()
    )

    tr = pd.concat(
        [
            df.high - df.low,

            (
                df.high -
                previous_close
            ).abs(),

            (
                df.low -
                previous_close
            ).abs()
        ],
        axis=1
    ).max(axis=1)

    atr_value = tr.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()

    plus_di = (
        100 *
        plus_dm.ewm(
            alpha=1 / length,
            adjust=False
        ).mean()
        /
        atr_value.replace(
            0,
            np.nan
        )
    )

    minus_di = (
        100 *
        minus_dm.ewm(
            alpha=1 / length,
            adjust=False
        ).mean()
        /
        atr_value.replace(
            0,
            np.nan
        )
    )

    dx = (
        100 *
        (plus_di - minus_di).abs()
        /
        (
            plus_di +
            minus_di
        ).replace(
            0,
            np.nan
        )
    )

    return dx.ewm(
        alpha=1 / length,
        adjust=False
    ).mean().fillna(0)


# ============================================================
# TIMEFRAME STATE
# ============================================================

def timeframe_state(df):

    if (
        df is None
        or len(df) < 80
    ):
        return None

    close = df.close

    e20 = ema(
        close,
        20
    )

    e50 = ema(
        close,
        50
    )

    rr = rsi(
        close
    )

    aa = atr(
        df
    )

    dd = adx(
        df
    )

    price = float(
        close.iloc[-1]
    )

    ema20_value = float(
        e20.iloc[-1]
    )

    ema50_value = float(
        e50.iloc[-1]
    )

    rsi_value = float(
        rr.iloc[-1]
    )

    atr_value = float(
        aa.iloc[-1]
    )

    adx_value = float(
        dd.iloc[-1]
    )

    long_score = 0
    short_score = 0

    if (
        price > ema20_value
        and
        ema20_value > ema50_value
    ):
        long_score += 2

    if (
        price < ema20_value
        and
        ema20_value < ema50_value
    ):
        short_score += 2

    if rsi_value >= 50:
        long_score += 1

    if rsi_value < 50:
        short_score += 1

    if (
        price >
        float(close.iloc[-2])
    ):
        long_score += 1

    if (
        price <
        float(close.iloc[-2])
    ):
        short_score += 1

    return {
        "price": price,
        "ema20": ema20_value,
        "ema50": ema50_value,
        "rsi": rsi_value,
        "atr": atr_value,
        "adx": adx_value,
        "long_score": min(
            long_score,
            4
        ),
        "short_score": min(
            short_score,
            4
        )
    }


# ============================================================
# BTC REGIME
# ============================================================

def btc_regime():

    try:

        btc15 = yf.download(
            "BTC-USD",
            period="5d",
            interval="15m",
            auto_adjust=False,
            progress=False,
            threads=False
        )

        btc1h = yf.download(
            "BTC-USD",
            period="10d",
            interval="1h",
            auto_adjust=False,
            progress=False,
            threads=False
        )

        if isinstance(
            btc15.columns,
            pd.MultiIndex
        ):

            btc15.columns = (
                btc15.columns
                .get_level_values(0)
            )

        if isinstance(
            btc1h.columns,
            pd.MultiIndex
        ):

            btc1h.columns = (
                btc1h.columns
                .get_level_values(0)
            )

        x = btc15.Close.dropna()

        y = btc1h.Close.dropna()

        change15 = (
            float(x.iloc[-1])
            /
            float(x.iloc[-2])
            -
            1
        ) * 100

        change1h = (
            float(y.iloc[-1])
            /
            float(y.iloc[-2])
            -
            1
        ) * 100

        if (
            change15 <= BTC_15M_CRASH
            or
            change1h <= BTC_1H_CRASH
        ):

            regime = "CRASH"

        elif (
            change15 < -0.5
            or
            change1h < -0.8
        ):

            regime = "WEAK"

        elif (
            change15 > 0.5
            and
            change1h > 0.8
        ):

            regime = "BULL"

        else:

            regime = "NEUTRAL"

        return {
            "state": regime,
            "c15": change15,
            "c1": change1h
        }

    except Exception as e:

        print(
            "BTC error:",
            e
        )

        return {
            "state": "NEUTRAL",
            "c15": 0,
            "c1": 0
        }


# ============================================================
# V5.6 SIGNAL ANALYSIS
# ============================================================

def analyze(
    market,
    btc
):

    try:

        d15 = candles(
            market,
            15,
            200
        )

        d1 = candles(
            market,
            60,
            200
        )

        d4 = candles(
            market,
            240,
            200
        )

        dd = candles(
            market,
            1440,
            200
        )

        a15 = timeframe_state(
            d15
        )

        a1 = timeframe_state(
            d1
        )

        a4 = timeframe_state(
            d4
        )

        ad = timeframe_state(
            dd
        )

        if any(
            x is None
            for x in [
                a15,
                a1,
                a4,
                ad
            ]
        ):

            return {
                "market": market,
                "pass": False,
                "reason": "데이터 부족"
            }

        # ====================================================
        # 1. Higher timeframe alignment
        # ====================================================

        if not (
            ad["long_score"] >= 3
            and
            a4["long_score"] >= 3
            and
            a1["long_score"] >= 3
            and
            a15["long_score"] >= 2
        ):

            return {
                "market": market,
                "pass": False,
                "reason": "상위 추세 불충족"
            }

        # ====================================================
        # 2. RSI
        # ====================================================

        rsi15 = a15["rsi"]

        if rsi15 < MIN_RSI:

            return {
                "market": market,
                "pass": False,
                "reason": (
                    f"RSI 약함 "
                    f"({rsi15:.1f})"
                )
            }

        if rsi15 > MAX_RSI:

            return {
                "market": market,
                "pass": False,
                "reason": (
                    f"RSI 과열 "
                    f"({rsi15:.1f})"
                )
            }

        # ====================================================
        # 3. ADX
        # ====================================================

        if a15["adx"] < MIN_ADX:

            return {
                "market": market,
                "pass": False,
                "reason": (
                    f"추세 약함 "
                    f"ADX {a15['adx']:.1f}"
                )
            }

        # ====================================================
        # 4. EMA distance
        # ====================================================

        price = a15["price"]

        ema_distance = (
            price -
            a15["ema20"]
        ) / a15["ema20"] * 100

        # 가격이 EMA20보다 너무 멀리 올라간 경우
        # 추격 진입 금지
        if ema_distance > MAX_EMA_DISTANCE:

            return {
                "market": market,
                "pass": False,
                "reason": (
                    f"추격진입 차단 "
                    f"(EMA +{ema_distance:.2f}%)"
                )
            }

        # ====================================================
        # 5. Volume
        # ====================================================

        volume = d15.volume

        average_volume = float(
            volume.iloc[-21:-1].mean()
        )

        volume_ratio = (
            float(volume.iloc[-1])
            /
            average_volume
            * 100
            if average_volume > 0
            else 0
        )

        minimum_volume = (
            STRONG_VOLUME_RATIO
            if btc["state"]
            in ("WEAK", "CRASH")
            else MIN_VOLUME_RATIO
        )

        if volume_ratio < minimum_volume:

            return {
                "market": market,
                "pass": False,
                "reason": (
                    f"거래량 부족 "
                    f"({volume_ratio:.0f}% "
                    f"< {minimum_volume}%)"
                ),
                "volume_ratio": volume_ratio
            }

        # ====================================================
        # 6. Current candle
        # ====================================================

        current_open = float(
            d15.open.iloc[-1]
        )

        current_high = float(
            d15.high.iloc[-1]
        )

        current_low = float(
            d15.low.iloc[-1]
        )

        current_close = float(
            d15.close.iloc[-1]
        )

        candle_range = max(
            current_high -
            current_low,
            1e-9
        )

        body = abs(
            current_close -
            current_open
        )

        body_pct = (
            body /
            current_open *
            100
        )

        body_ratio = (
            body /
            candle_range
        )

        close_position = (
            current_close -
            current_low
        ) / candle_range

        # ====================================================
        # 7. Do not chase giant candle
        # ====================================================

        if body_pct > MAX_CANDLE_BODY:

            return {
                "market": market,
                "pass": False,
                "reason": (
                    f"급등 추격 차단 "
                    f"(캔들 {body_pct:.2f}%)"
                ),
                "volume_ratio": volume_ratio
            }

        # ====================================================
        # 8. Pullback detection
        #
        # We want:
        # previous candles pulled toward EMA20
        # while current candle recovers upward.
        # ====================================================

        ema20_series = ema(
            d15.close,
            20
        )

        previous_low = float(
            d15.low.iloc[
                -PULLBACK_LOOKBACK:-1
            ].min()
        )

        previous_close = float(
            d15.close.iloc[-2]
        )

        ema20_now = float(
            ema20_series.iloc[-1]
        )

        ema20_prev = float(
            ema20_series.iloc[-2]
        )

        pullback_touched = (
            previous_low
            <=
            ema20_prev * 1.012
        )

        recovery = (
            current_close
            >
            previous_close
            and
            current_close
            >
            ema20_now
        )

        strong_recovery = (
            current_close
            >
            current_open
            and
            body_ratio >= 0.40
            and
            close_position >= 0.60
        )

        pullback_signal = (
            pullback_touched
            and
            recovery
            and
            strong_recovery
        )

        # ====================================================
        # 9. Breakout confirmation
        # ====================================================

        previous_high = float(
            d15.high.iloc[
                -21:-1
            ].max()
        )

        breakout = (
            current_close
            >
            previous_high
        )

        # We allow either:
        # pullback recovery
        # OR clean breakout with strong candle.
        if not (
            pullback_signal
            or
            (
                breakout
                and
                strong_recovery
            )
        ):

            return {
                "market": market,
                "pass": False,
                "reason": "눌림/재상승 조건 불충족",
                "volume_ratio": volume_ratio
            }

        # ====================================================
        # 10. Additional trend strength
        # ====================================================

        score = 0

        # Daily
        if ad["long_score"] >= 3:
            score += 15

        # 4H
        if a4["long_score"] >= 3:
            score += 20

        # 1H
        if a1["long_score"] >= 3:
            score += 20

        # 15M
        if a15["long_score"] >= 2:
            score += 10

        # Volume
        if volume_ratio >= 220:
            score += 15
        elif volume_ratio >= 180:
            score += 12
        else:
            score += 8

        # Pullback recovery
        if pullback_signal:
            score += 10

        # Breakout
        if breakout:
            score += 10

        # ADX
        if a15["adx"] >= 25:
            score += 5

        # RSI ideal zone
        if 55 <= rsi15 <= 68:
            score += 5

        # BTC regime
        if btc["state"] == "BULL":
            score += 5

        elif btc["state"] == "WEAK":
            score -= 5

        elif btc["state"] == "CRASH":
            score -= 20

        # ====================================================
        # RSI overextension penalty
        # ====================================================

        if rsi15 >= 68:
            score -= 5

        if rsi15 >= 70:
            score -= 8

        score = max(
            0,
            min(
                100,
                int(score)
            )
        )

        # ====================================================
        # Required score
        # ====================================================

        threshold = (
            CRASH_SCORE
            if btc["state"] == "CRASH"
            else
            WEAK_SCORE
            if btc["state"] == "WEAK"
            else
            SIGNAL_SCORE
        )

        if score < threshold:

            return {
                "market": market,
                "pass": False,
                "score": score,
                "reason": (
                    f"점수 부족 "
                    f"({score} < {threshold})"
                ),
                "volume_ratio": volume_ratio
            }

        # ====================================================
        # STOP LOSS
        #
        # Use swing low + ATR.
        # This is more stable than simply using
        # the most recent low.
        # ====================================================

        swing_low = float(
            d15.low.iloc[-9:-1].min()
        )

        atr_value = float(
            atr(
                d15,
                14
            ).iloc[-1]
        )

        atr_stop = (
            price -
            atr_value * 1.25
        )

        structure_stop = (
            swing_low -
            atr_value * 0.20
        )

        # Use the deeper of the two
        # so normal candle noise does not
        # immediately hit SL.
        stop = min(
            atr_stop,
            structure_stop
        )

        risk = price - stop

        # ====================================================
        # SL too tight
        # ====================================================

        minimum_risk = (
            price *
            MIN_SL /
            100
        )

        if risk < minimum_risk:

            risk = minimum_risk

            stop = price - risk

        # ====================================================
        # SL too wide
        # ====================================================

        maximum_risk = (
            price *
            MAX_SL /
            100
        )

        if risk > maximum_risk:

            return {
                "market": market,
                "pass": False,
                "score": score,
                "reason": (
                    f"손절폭 과다 "
                    f"({risk/price*100:.2f}%)"
                ),
                "volume_ratio": volume_ratio
            }

        # ====================================================
        # TP
        # ====================================================

        tp1 = price + risk * 1.5
        tp2 = price + risk * 2.0
        tp3 = price + risk * 3.0

        tp1_pct = (
            tp1 -
            price
        ) / price * 100

        if tp1_pct > MAX_TP1:

            return {
                "market": market,
                "pass": False,
                "score": score,
                "reason": (
                    f"TP1 거리 과다 "
                    f"({tp1_pct:.2f}%)"
                ),
                "volume_ratio": volume_ratio
            }

        # ====================================================
        # Final
        # ====================================================

        return {
            "market": market,
            "pass": True,
            "score": score,

            "price": price,
            "stop": stop,

            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,

            "rsi": rsi15,
            "adx": a15["adx"],

            "volume_ratio":
                volume_ratio,

            "ema_distance_pct":
                ema_distance,

            "btc_state":
                btc["state"],

            "pullback":
                pullback_signal,

            "breakout":
                breakout
        }

    except Exception as e:

        return {
            "market": market,
            "pass": False,
            "reason":
                f"분석 오류: {e}"
        }


# ============================================================
# STATE
# ============================================================

def default_state():

    return {
        "version": VERSION,
        "positions": {},
        "sent_signal_ids": [],
        "last_signals": {}
    }


def get_state():

    state = load(
        STATE_FILE,
        default_state()
    )

    if not isinstance(
        state,
        dict
    ):
        state = default_state()

    state.setdefault(
        "positions",
        {}
    )

    state.setdefault(
        "sent_signal_ids",
        []
    )

    state.setdefault(
        "last_signals",
        {}
    )

    state["version"] = VERSION

    return state


# ============================================================
# DUPLICATE FILTER
# ============================================================

def signal_id(a):

    raw = (
        f'{a["market"]}|'
        f'{a["price"]:.10f}|'
        f'{a["score"]}'
    )

    return hashlib.sha256(
        raw.encode()
    ).hexdigest()[:20]


def signal_allowed(
    state,
    a
):

    market = a["market"]

    if market in state[
        "positions"
    ]:
        return False

    if (
        signal_id(a)
        in state[
            "sent_signal_ids"
        ]
    ):
        return False

    previous = (
        state[
            "last_signals"
        ].get(market)
    )

    if not previous:
        return True

    try:

        previous_time = (
            datetime.fromisoformat(
                previous["time"]
            )
        )

        elapsed = (
            now() -
            previous_time
        ).total_seconds()

        if elapsed < (
            COOLDOWN_HOURS *
            3600
        ):
            return False

        old_price = float(
            previous["price"]
        )

        price_change = (
            abs(
                a["price"] -
                old_price
            )
            /
            old_price
            * 100
        )

        if (
            price_change
            <
            MIN_PRICE_DISTANCE
        ):
            return False

    except Exception:
        pass

    return True


# ============================================================
# TELEGRAM SIGNAL MESSAGE
# ============================================================

def signal_message(a):

    entry = a["price"]

    stop = a["stop"]

    tp1 = a["tp1"]
    tp2 = a["tp2"]
    tp3 = a["tp3"]

    risk = entry - stop

    pullback_text = (
        "PASS"
        if a.get("pullback")
        else "BREAKOUT"
    )

    return f"""🟢 <b>롱 시그널 발생</b>
━━━━━━━━━━━━━━━━━━

💰 <b>{display_name(a["market"])}</b>

📊 신호 점수  <b>{a["score"]} / 100</b>
₿ 비트코인 상태  <b>{a["btc_state"]}</b>

━━━━━━━━━━━━━━━━━━
🎯 <b>매매 계획</b>

진입가      <b>{fp(entry)}</b>
손절가      <b>{fp(stop)}</b>

익절 1      <b>{fp(tp1)}</b>  ({(tp1-entry)/entry*100:+.2f}%)
익절 2      <b>{fp(tp2)}</b>  ({(tp2-entry)/entry*100:+.2f}%)
익절 3      <b>{fp(tp3)}</b>  ({(tp3-entry)/entry*100:+.2f}%)

━━━━━━━━━━━━━━━━━━
🛡️ <b>리스크 관리</b>

손절폭      <b>{(stop-entry)/entry*100:+.2f}%</b>
익절 1 R:R  1 : {(tp1-entry)/risk:.2f}
익절 2 R:R  1 : {(tp2-entry)/risk:.2f}
익절 3 R:R  1 : {(tp3-entry)/risk:.2f}

━━━━━━━━━━━━━━━━━━
📈 <b>시장 상태</b>

RSI         {a["rsi"]:.1f}
ADX         {a["adx"]:.1f}
거래량      <b>{a["volume_ratio"]:.0f}%</b>
EMA20 이격  {a["ema_distance_pct"]:+.2f}%

진입 유형   <b>{pullback_text}</b>

━━━━━━━━━━━━━━━━━━
⏱️ <b>15분봉 확정 시그널</b>

⚠️ 신호 발생 후 추격진입에 주의

<a href="{tv(a["market"])}">📈 TradingView 차트 열기</a>
━━━━━━━━━━━━━━━━━━"""


# ============================================================
# POSITION MONITOR
# ============================================================

def monitor(state):

    changed = False

    for market, position in list(
        state[
            "positions"
        ].items()
    ):

        df = candles(
            market,
            1,
            5
        )

        if df is None:
            continue

        low = float(
            df.low.iloc[-1]
        )

        high = float(
            df.high.iloc[-1]
        )

        current = float(
            df.close.iloc[-1]
        )

        entry = float(
            position["entry"]
        )

        stop = float(
            position["sl"]
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

        # ----------------------------------------------------
        # SL FIRST
        # ----------------------------------------------------

        if low <= stop:

            tg(
                f"""🔴 <b>손절 발생</b>
━━━━━━━━━━━━━━━━━━

💰 <b>{display_name(market)}</b>

진입가       {fp(entry)}
손절 기준가  <b>{fp(stop)}</b>
현재가       {fp(current)}

손절 기준    <b>{(stop-entry)/entry*100:+.2f}%</b>
현재 변동    {(current-entry)/entry*100:+.2f}%

━━━━━━━━━━━━━━━━━━
🛑 <b>손절 조건 도달</b>

포지션 추적을 종료했습니다.

<a href="{tv(market)}">📈 TradingView 차트 열기</a>
━━━━━━━━━━━━━━━━━━"""
            )

            del state[
                "positions"
            ][market]

            changed = True

            continue

        # ----------------------------------------------------
        # TP3
        # ----------------------------------------------------

        if (
            not position.get(
                "tp3_hit"
            )
            and
            high >= tp3
        ):

            tg(
                f"""🏆 <b>최종 익절 완료</b>

💰 <b>{display_name(market)}</b>

진입가       {fp(entry)}
TP3 도달가   <b>{fp(tp3)}</b>

수익 기준    <b>{(tp3-entry)/entry*100:+.2f}%</b>

━━━━━━━━━━━━━━━━━━
🎯 <b>포지션 추적 종료</b>

<a href="{tv(market)}">📈 TradingView 차트 열기</a>"""
            )

            position[
                "tp3_hit"
            ] = True

            del state[
                "positions"
            ][market]

            changed = True

            continue

        # ----------------------------------------------------
        # TP2
        # ----------------------------------------------------

        if (
            not position.get(
                "tp2_hit"
            )
            and
            high >= tp2
        ):

            position[
                "tp2_hit"
            ] = True

            # Move SL to TP1
            position[
                "sl"
            ] = tp1

            tg(
                f"""🎯 <b>TP2 도달</b>

💰 <b>{display_name(market)}</b>

도달가 <b>{fp(tp2)}</b>

수익 기준
<b>{(tp2-entry)/entry*100:+.2f}%</b>

🔒 손절가 → TP1 이동
새 손절가 <b>{fp(tp1)}</b>

<a href="{tv(market)}">📈 TradingView 차트 열기</a>"""
            )

            changed = True

        # ----------------------------------------------------
        # TP1
        # ----------------------------------------------------

        elif (
            not position.get(
                "tp1_hit"
            )
            and
            high >= tp1
        ):

            position[
                "tp1_hit"
            ] = True

            # Move SL to entry
            position[
                "sl"
            ] = entry

            tg(
                f"""🎯 <b>TP1 도달</b>

💰 <b>{display_name(market)}</b>

도달가 <b>{fp(tp1)}</b>

수익 기준
<b>{(tp1-entry)/entry*100:+.2f}%</b>

🔒 손절가 → 진입가 이동
새 손절가 <b>{fp(entry)}</b>

<a href="{tv(market)}">📈 TradingView 차트 열기</a>"""
            )

            changed = True

    if changed:

        save(
            STATE_FILE,
            state
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)

    print(
        f" UPBIT SPOT SMART SIGNAL BOT V{VERSION}"
    )

    print("=" * 60)

    state = get_state()

    # --------------------------------------------------------
    # Monitor existing positions
    # --------------------------------------------------------

    monitor(
        state
    )

    # --------------------------------------------------------
    # BTC regime
    # --------------------------------------------------------

    btc = btc_regime()

    print(
        f'BTC: {btc["state"]} | '
        f'15M {btc["c15"]:+.2f}% | '
        f'1H {btc["c1"]:+.2f}%'
    )

    # --------------------------------------------------------
    # Dynamic Upbit market discovery
    # --------------------------------------------------------

    market_list = markets()

    print(
        "Upbit KRW markets discovered:",
        len(market_list)
    )

    if not market_list:

        print(
            "No KRW markets found."
        )

        return

    # --------------------------------------------------------
    # 24H liquidity filter
    # --------------------------------------------------------

    ticker_data = tickers(
        market_list
    )

    candidates = []

    for ticker in ticker_data:

        value = sf(
            ticker.get(
                "acc_trade_price_24h"
            )
        )

        if (
            value >=
            MIN_24H_VALUE
        ):

            candidates.append(
                ticker
            )

    candidates.sort(
        key=lambda x:
            x.get(
                "acc_trade_price_24h",
                0
            ),
        reverse=True
    )

    candidates = candidates[
        :MAX_DEEP_SCAN
    ]

    print(
        "Deep scan candidates:",
        len(candidates)
    )

    # --------------------------------------------------------
    # Deep scan
    # --------------------------------------------------------

    results = []

    for ticker in candidates:

        market = ticker[
            "market"
        ]

        if market in state[
            "positions"
        ]:

            print(
                "SKIP ACTIVE",
                display_name(
                    market
                )
            )

            continue

        result = analyze(
            market,
            btc
        )

        if result.get(
            "pass"
        ):

            results.append(
                result
            )

            print(
                f'PASS '
                f'{display_name(market)} '
                f'score={result["score"]} '
                f'volume={result["volume_ratio"]:.0f}%'
            )

        else:

            print(
                f'FAIL '
                f'{display_name(market)} | '
                f'{result.get("reason", "")}'
            )

    # --------------------------------------------------------
    # Rank candidates
    # --------------------------------------------------------

    results.sort(
        key=lambda x: (
            x["score"],
            x["volume_ratio"]
        ),
        reverse=True
    )

    if not results:

        print(
            "No valid new signal."
        )

        save(
            STATE_FILE,
            state
        )

        return

    best = results[0]

    print(
        f'BEST CANDIDATE: '
        f'{display_name(best["market"])} '
        f'score={best["score"]}'
    )

    # --------------------------------------------------------
    # Duplicate / cooldown
    # --------------------------------------------------------

    if not signal_allowed(
        state,
        best
    ):

        print(
            "Cooldown / duplicate "
            "filter blocked signal."
        )

        return

    # --------------------------------------------------------
    # Telegram first
    # --------------------------------------------------------

    message = signal_message(
        best
    )

    if not tg(
        message
    ):

        print(
            "Telegram failed."
        )

        print(
            "Position was NOT created."
        )

        log(
            "TELEGRAM_FAILED",
            best
        )

        return

    # --------------------------------------------------------
    # Create active position
    # --------------------------------------------------------

    market = best[
        "market"
    ]

    state[
        "positions"
    ][market] = {

        "market": market,

        "direction": "LONG",

        "entry":
            best["price"],

        "sl":
            best["stop"],

        "tp1":
            best["tp1"],

        "tp2":
            best["tp2"],

        "tp3":
            best["tp3"],

        "tp1_hit":
            False,

        "tp2_hit":
            False,

        "tp3_hit":
            False,

        "created_at":
            now().isoformat()
    }

    # --------------------------------------------------------
    # Duplicate protection
    # --------------------------------------------------------

    state[
        "sent_signal_ids"
    ] = (
        state[
            "sent_signal_ids"
        ]
        +
        [
            signal_id(best)
        ]
    )[-500:]

    state[
        "last_signals"
    ][market] = {

        "time":
            now().isoformat(),

        "price":
            best["price"],

        "score":
            best["score"]
    }

    state[
        "version"
    ] = VERSION

    save(
        STATE_FILE,
        state
    )

    log(
        "LONG_SIGNAL",
        best
    )

    print(
        "NEW SIGNAL:",
        display_name(market)
    )


if __name__ == "__main__":
    main()
