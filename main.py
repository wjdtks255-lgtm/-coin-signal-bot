import os
import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

# ============================================================
# UPBIT SMART SIGNAL BOT V5.1
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

STATE_FILE = "tracked_coins.json"

UPBIT_API = "https://api.upbit.com/v1"

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

KST = timezone(timedelta(hours=9))


# ============================================================
# TIME
# ============================================================

def now_kst():
    return datetime.now(KST)


# ============================================================
# FORMAT
# ============================================================

def fmt_price(price):
    if price is None:
        return "-"

    price = float(price)

    if price >= 1000:
        return f"{price:,.0f}"
    elif price >= 100:
        return f"{price:,.1f}"
    elif price >= 10:
        return f"{price:,.2f}"
    elif price >= 1:
        return f"{price:,.3f}"
    else:
        return f"{price:,.6f}"


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram 설정 없음")
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
        print("Telegram 전송 오류:", e)

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

        data.setdefault("positions", {})
        data.setdefault("history", [])
        data.setdefault("sent_signal_ids", [])
        data.setdefault("legacy_positions", {})

        return data

    except Exception as e:

        print("상태 파일 읽기 오류:", e)

        return default_state()


def save_state(state):

    tmp_file = STATE_FILE + ".tmp"

    try:

        with open(
            tmp_file,
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
            tmp_file,
            STATE_FILE
        )

    except Exception as e:

        print("상태 저장 오류:", e)


# ============================================================
# LEGACY POSITION MIGRATION
# ============================================================

def first_value(data, keys):

    for key in keys:

        if key in data:

            value = data.get(key)

            if value is not None:

                try:
                    return float(value)
                except:
                    pass

    return None


def normalize_position(position):

    if not isinstance(position, dict):
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

    # --------------------------------------------------------
    # 기존 포지션에 TP1은 있지만 SL이 없는 경우 복구
    # --------------------------------------------------------

    if stop is None and tp1 is not None and tp1 > entry:

        risk = (tp1 - entry) / 1.5

        stop = entry - risk

    # --------------------------------------------------------
    # TP1이 없는 경우 SL 또는 기본 위험값으로 복구
    # --------------------------------------------------------

    if tp1 is None:

        if stop is not None and stop < entry:

            risk = entry - stop

        else:

            risk = entry * 0.03

        tp1 = entry + risk * 1.5

    # --------------------------------------------------------
    # TP2 / TP3 복구
    # --------------------------------------------------------

    if tp2 is None:

        risk = tp1 - entry
        tp2 = entry + (risk / 1.5) * 2.0

    if tp3 is None:

        risk = tp1 - entry
        tp3 = entry + (risk / 1.5) * 3.0

    if stop is None:

        risk = (tp1 - entry) / 1.5
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

    for market, position in list(positions.items()):

        normalized = normalize_position(position)

        if normalized is None:

            invalid[market] = position
            del positions[market]

            continue

        positions[market] = normalized

    if invalid:

        legacy = state.setdefault(
            "legacy_positions",
            {}
        )

        for market, position in invalid.items():

            legacy[market] = position

    return state


# ============================================================
# SIGNAL HISTORY
# ============================================================

def signal_already_sent(state, signal_id):

    ids = state.get(
        "sent_signal_ids",
        []
    )

    return signal_id in ids


def remember_signal(state, signal_id):

    ids = state.setdefault(
        "sent_signal_ids",
        []
    )

    if signal_id not in ids:

        ids.append(signal_id)

    # 너무 커지지 않도록 최근 1000개만 유지
    state["sent_signal_ids"] = ids[-1000:]


# ============================================================
# UPBIT API
# ============================================================

def get_markets():

    url = f"{UPBIT_API}/market/all"

    try:

        response = requests.get(
            url,
            params={
                "isDetails": "false"
            },
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        return [
            x["market"]
            for x in data
            if x["market"].startswith("KRW-")
        ]

    except Exception as e:

        print("마켓 조회 오류:", e)

        return []


def get_tickers(markets):

    if not markets:
        return []

    result = []

    for i in range(
        0,
        len(markets),
        100
    ):

        chunk = markets[i:i + 100]

        try:

            response = requests.get(
                f"{UPBIT_API}/ticker",
                params={
                    "markets": ",".join(chunk)
                },
                timeout=15
            )

            response.raise_for_status()

            result.extend(
                response.json()
            )

        except Exception as e:

            print("Ticker 오류:", e)

    return result


def get_candles(
    market,
    unit,
    count=200
):

    # 일봉
    if unit == 1440:

        url = (
            f"{UPBIT_API}/candles/days"
        )

        params = {
            "market": market,
            "count": min(count, 200)
        }

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
            f"{UPBIT_API}/candles/minutes/{unit}"
        )

        params = {
            "market": market,
            "count": min(count, 200)
        }

    try:

        response = requests.get(
            url,
            params=params,
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)

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
        ).reset_index(drop=True)

        return df

    except Exception as e:

        print(
            f"캔들 오류 {market} {unit}:",
            e
        )

        return pd.DataFrame()


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

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan
    )

    result = 100 - (
        100 / (1 + rs)
    )

    return result.fillna(50)


def atr(df, length=14):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ],
        axis=1
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()


# ============================================================
# TIMEFRAME ANALYSIS
# ============================================================

def timeframe_trend(df):

    if df.empty or len(df) < 60:
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

    last = close.iloc[-1]

    if (
        last > ema20.iloc[-1]
        and ema20.iloc[-1] > ema50.iloc[-1]
    ):
        return "PASS"

    return "FAIL"


# ============================================================
# BTC REGIME
# ============================================================

def get_btc_regime():

    df15 = get_candles(
        "KRW-BTC",
        15,
        100
    )

    df60 = get_candles(
        "KRW-BTC",
        60,
        100
    )

    if (
        df15.empty
        or df60.empty
        or len(df15) < 20
        or len(df60) < 20
    ):

        return "NEUTRAL", 0, 0

    btc15 = (
        df15["close"].iloc[-1]
        /
        df15["close"].iloc[-2]
        - 1
    ) * 100

    btc1h = (
        df60["close"].iloc[-1]
        /
        df60["close"].iloc[-2]
        - 1
    ) * 100

    if (
        btc15 <= BTC_15M_CRASH_PCT
        or btc1h <= BTC_1H_CRASH_PCT
    ):

        return (
            "CRASH",
            btc15,
            btc1h
        )

    if (
        btc15 < -0.7
        or btc1h < -1.0
    ):

        return (
            "WEAK",
            btc15,
            btc1h
        )

    if (
        btc15 > 0.7
        and btc1h > 1.0
    ):

        return (
            "BULL",
            btc15,
            btc1h
        )

    return (
        "NEUTRAL",
        btc15,
        btc1h
    )


# ============================================================
# FAST CANDIDATES
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

        if trade_value < MIN_FINAL_24H_TRADE_VALUE:
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
        key=lambda x: x["trade_value"],
        reverse=True
    )

    return candidates


# ============================================================
# DEEP ANALYSIS
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
        x.empty
        for x in [
            df1d,
            df4h,
            df1h,
            df15
        ]
    ):

        return None

    if min(
        len(df1d),
        len(df4h),
        len(df1h),
        len(df15)
    ) < 60:

        return None

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
        return None

    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    price = float(
        df15["close"].iloc[-1]
    )

    ema20 = float(
        ema(
            df15["close"],
            20
        ).iloc[-1]
    )

    ema50 = float(
        ema(
            df15["close"],
            50
        ).iloc[-1]
    )

    atr15 = float(
        atr(
            df15,
            14
        ).iloc[-1]
    )

    if atr15 <= 0:
        return None

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

    avg_volume = float(
        df15["volume"]
        .iloc[-21:-1]
        .mean()
    )

    current_volume = float(
        df15["volume"].iloc[-1]
    )

    if avg_volume <= 0:
        return None

    volume_ratio = (
        current_volume
        /
        avg_volume
    ) * 100

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
        return None

    body = abs(
        float(candle["close"])
        -
        float(candle["open"])
    )

    body_ratio = (
        body /
        candle_range
    )

    close_position = (
        float(candle["close"])
        -
        float(candle["low"])
    ) / candle_range

    candle_pass = (
        body_ratio >= MIN_BODY_RATIO
        and close_position >= MIN_CLOSE_POSITION
        and candle["close"] > candle["open"]
    )

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    previous_high = float(
        df15["high"]
        .iloc[-21:-1]
        .max()
    )

    breakout_pass = (
        price > previous_high
    )

    # --------------------------------------------------------
    # 1H MOMENTUM
    # --------------------------------------------------------

    change_1h = (
        df1h["close"].iloc[-1]
        /
        df1h["close"].iloc[-2]
        - 1
    ) * 100

    # --------------------------------------------------------
    # EMA DISTANCE
    # --------------------------------------------------------

    ema_distance = (
        abs(price - ema20)
        /
        ema20
    ) * 100

    if ema_distance > MAX_ENTRY_DISTANCE_FROM_EMA20:
        return None

    # --------------------------------------------------------
    # RISK / TARGET
    # --------------------------------------------------------

    support = float(
        df15["low"]
        .iloc[-15:-1]
        .min()
    )

    stop = support - (
        atr15 * 0.25
    )

    if stop >= price:
        return None

    stop_loss_pct = (
        (price - stop)
        /
        price
    ) * 100

    if (
        stop_loss_pct < MIN_STOP_LOSS_PCT
        or stop_loss_pct > MAX_STOP_LOSS_PCT
    ):

        return None

    risk = price - stop

    tp1 = price + risk * 1.5
    tp2 = price + risk * 2.0
    tp3 = price + risk * 3.0

    tp1_distance = (
        (tp1 - price)
        /
        price
    ) * 100

    if tp1_distance > MAX_TP1_DISTANCE_PCT:
        return None

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 0

    # 1D
    if trend1d == "PASS":
        score += 20

    # 4H
    if trend4h == "PASS":
        score += 20

    # 1H
    if trend1h == "PASS":
        score += 15

    # 15M
    if trend15 == "PASS":
        score += 15

    # volume
    if volume_ratio >= STRONG_VOLUME_RATIO:
        score += 15

    elif volume_ratio >= MIN_VOLUME_RATIO:
        score += 10

    # candle
    if candle_pass:
        score += 10

    # breakout
    if breakout_pass:
        score += 10

    # risk
    if (
        MIN_STOP_LOSS_PCT
        <= stop_loss_pct
        <= MAX_STOP_LOSS_PCT
    ):
        score += 5

    # --------------------------------------------------------
    # PENALTIES
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

    # BTC environment
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
    # FINAL FILTER
    # --------------------------------------------------------

    if btc_regime == "CRASH":

        if score < STRONG_SCORE:
            return None

    elif btc_regime == "WEAK":

        if score < WEAK_BTC_SCORE:
            return None

        if volume_ratio < STRONG_VOLUME_RATIO:
            return None

    else:

        if score < SIGNAL_SCORE:
            return None

    if not candle_pass:
        return None

    if volume_ratio < MIN_VOLUME_RATIO:
        return None

    if not breakout_pass:
        return None

    # --------------------------------------------------------
    # SIGNAL CANDLE
    # --------------------------------------------------------

    signal_candle = str(
        df15["candle_date_time_kst"].iloc[-1]
    )

    return {
        "market": market,

        "price": price,
        "entry": price,

        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,

        "score": score,

        "rsi": rsi_value,
        "volume_ratio": volume_ratio,

        "stop_loss_pct": stop_loss_pct,

        "btc_regime": btc_regime,

        "signal_candle": signal_candle,

        "change_1h": change_1h,

        "ema_distance": ema_distance,

        "trend1d": trend1d,
        "trend4h": trend4h,
        "trend1h": trend1h,
        "trend15": trend15
    }


# ============================================================
# COOLDOWN
# ============================================================

def can_create_new_signal(
    state,
    market,
    price
):

    now = now_kst()

    for item in reversed(
        state.get("history", [])
    ):

        if item.get("market") != market:
            continue

        created = item.get(
            "created_at"
        )

        if not created:
            continue

        try:

            dt = datetime.fromisoformat(
                created
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=KST
                )

            age = (
                now - dt
            ).total_seconds() / 3600

            if age < SIGNAL_COOLDOWN_HOURS:

                old_price = float(
                    item.get(
                        "entry",
                        price
                    )
                )

                distance = (
                    abs(price - old_price)
                    /
                    old_price
                ) * 100

                if distance < MIN_NEW_SIGNAL_PRICE_DISTANCE:
                    return False

        except:
            continue

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
        f"{market}|"
        f"{signal_candle}"
    )

    # 최신 상태 재로드
    latest = load_state()

    migrate_positions(
        latest
    )

    # 이미 보낸 시그널인지 확인
    if signal_already_sent(
        latest,
        signal_id
    ):

        print(
            f"[중복 차단] {signal_id}"
        )

        return False

    # 이미 포지션 존재
    if market in latest.get(
        "positions",
        {}
    ):

        print(
            f"[기존 포지션] {market}"
        )

        return False

    # 쿨다운
    if not can_create_new_signal(
        latest,
        market,
        analysis["price"]
    ):

        print(
            f"[쿨다운 차단] {market}"
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

    latest[
        "positions"
    ][market] = position

    remember_signal(
        latest,
        signal_id
    )

    history_item = {
        "market": market,
        "entry": analysis["entry"],
        "stop": analysis["stop"],
        "tp1": analysis["tp1"],
        "tp2": analysis["tp2"],
        "tp3": analysis["tp3"],
        "score": analysis["score"],
        "signal_id": signal_id,
        "created_at": now_kst().isoformat()
    }

    latest.setdefault(
        "history",
        []
    ).append(
        history_item
    )

    latest["history"] = latest[
        "history"
    ][-1000:]

    # Telegram 전에 저장
    save_state(
        latest
    )

    # ========================================================
    # 한국어 알림
    # ========================================================

    message = (
        "🟢 <b>롱 시그널 발생</b>\n"
        "\n"
        f"종목: <b>{market}</b>\n"
        f"점수: <b>{analysis['score']}/100</b>\n"
        f"BTC 상태: {analysis['btc_regime']}\n"
        "\n"
        f"진입가: <b>{fmt_price(analysis['entry'])}</b>\n"
        f"손절가: <b>{fmt_price(analysis['stop'])}</b>\n"
        f"익절1: <b>{fmt_price(analysis['tp1'])}</b>\n"
        f"익절2: <b>{fmt_price(analysis['tp2'])}</b>\n"
        f"익절3: <b>{fmt_price(analysis['tp3'])}</b>\n"
        "\n"
        f"손절폭: {analysis['stop_loss_pct']:.2f}%\n"
        f"RSI: {analysis['rsi']:.1f}\n"
        f"거래량: {analysis['volume_ratio']:.0f}%\n"
        "\n"
        "15분봉 확정 시그널"
    )

    sent = send_telegram(
        message
    )

    if sent:

        print(
            f"[신규 롱 시그널] "
            f"{market} | "
            f"점수 {analysis['score']}"
        )

    return True


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

        # ====================================================
        # SL FIRST
        # ====================================================

        if current <= stop:

            pnl_pct = (
                (current - entry)
                /
                entry
            ) * 100

            send_telegram(
                f"🔴 <b>손절 발생</b>\n"
                f"\n"
                f"종목: <b>{market}</b>\n"
                f"진입가: {fmt_price(entry)}\n"
                f"청산가: {fmt_price(current)}\n"
                f"수익률: <b>{pnl_pct:+.2f}%</b>"
            )

            state.setdefault(
                "history",
                []
            ).append(
                {
                    "market": market,
                    "result": "SL",
                    "entry": entry,
                    "exit": current,
                    "pnl_pct": pnl_pct,
                    "closed_at": now_kst().isoformat()
                }
            )

            del positions[
                market
            ]

            changed = True

            continue

        # ====================================================
        # TP1
        # ====================================================

        if (
            stage == "ENTRY"
            and current >= tp1
        ):

            if MOVE_SL_TO_ENTRY_AFTER_TP1:

                position[
                    "stop"
                ] = entry

            position[
                "stage"
            ] = "TP1"

            changed = True

            send_telegram(
                f"🟢 <b>익절 1단계 도달</b>\n"
                f"\n"
                f"종목: <b>{market}</b>\n"
                f"진입가: {fmt_price(entry)}\n"
                f"익절1: <b>{fmt_price(tp1)}</b>\n"
                f"수익률: <b>"
                f"{((tp1 / entry) - 1) * 100:+.2f}%"
                f"</b>\n"
                f"\n"
                f"손절가 → 진입가"
            )

            continue

        # ====================================================
        # TP2
        # ====================================================

        if (
            stage == "TP1"
            and current >= tp2
        ):

            if MOVE_SL_TO_TP1_AFTER_TP2:

                position[
                    "stop"
                ] = tp1

            position[
                "stage"
            ] = "TP2"

            changed = True

            send_telegram(
                f"🟢 <b>익절 2단계 도달</b>\n"
                f"\n"
                f"종목: <b>{market}</b>\n"
                f"진입가: {fmt_price(entry)}\n"
                f"익절2: <b>{fmt_price(tp2)}</b>\n"
                f"수익률: <b>"
                f"{((tp2 / entry) - 1) * 100:+.2f}%"
                f"</b>\n"
                f"\n"
                f"손절가 → 익절1"
            )

            continue

        # ====================================================
        # TP3
        # ====================================================

        if (
            stage == "TP2"
            and current >= tp3
        ):

            pnl_pct = (
                (tp3 - entry)
                /
                entry
            ) * 100

            send_telegram(
                f"🔵 <b>최종 익절 도달</b>\n"
                f"\n"
                f"종목: <b>{market}</b>\n"
                f"진입가: {fmt_price(entry)}\n"
                f"최종 익절가: <b>{fmt_price(tp3)}</b>\n"
                f"최종 수익률: <b>{pnl_pct:+.2f}%</b>"
            )

            state.setdefault(
                "history",
                []
            ).append(
                {
                    "market": market,
                    "result": "TP3",
                    "entry": entry,
                    "exit": tp3,
                    "pnl_pct": pnl_pct,
                    "closed_at": now_kst().isoformat()
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
# MAIN SCANNER
# ============================================================

def run_scan():

    print("=" * 50)
    print("UPBIT SMART SIGNAL BOT V5.1")
    print("=" * 50)

    print(
        "시작 시간:",
        now_kst().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    # --------------------------------------------------------
    # 기존 포지션 먼저 추적
    # --------------------------------------------------------

    track_positions()

    # --------------------------------------------------------
    # BTC 상태
    # --------------------------------------------------------

    btc_regime, btc15, btc1h = (
        get_btc_regime()
    )

    print(
        f"BTC 상태: {btc_regime}"
    )

    print(
        f"BTC 15M: {btc15:.2f}%"
    )

    print(
        f"BTC 1H : {btc1h:.2f}%"
    )

    # --------------------------------------------------------
    # CRASH
    # --------------------------------------------------------

    if btc_regime == "CRASH":

        print(
            "BTC 급락 상태 → 신규 진입 최소화"
        )

    # --------------------------------------------------------
    # FAST SCAN
    # --------------------------------------------------------

    candidates = fast_candidate_scan()

    print(
        f"KRW 마켓 후보: {len(candidates)}"
    )

    if not candidates:
        return

    # 거래대금 상위
    candidates = candidates[
        :MAX_DEEP_SCAN
    ]

    print(
        f"정밀 분석: {len(candidates)}개"
    )

    state = load_state()

    migrate_positions(
        state
    )

    # --------------------------------------------------------
    # DEEP SCAN
    # --------------------------------------------------------

    signals = []

    for i, item in enumerate(
        candidates,
        start=1
    ):

        market = item[
            "market"
        ]

        print(
            f"[{i}/{len(candidates)}] "
            f"{market}",
            end=" "
        )

        try:

            analysis = analyze_market(
                market,
                item,
                btc_regime
            )

            if analysis is None:

                print("FAIL")

                continue

            print(
                f"PASS "
                f"Score={analysis['score']}"
            )

            signals.append(
                analysis
            )

        except Exception as e:

            print(
                "ERROR:",
                e
            )

        time.sleep(
            0.05
        )

    # --------------------------------------------------------
    # SIGNAL SORT
    # --------------------------------------------------------

    signals.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    print("=" * 50)
    print(
        f"최종 시그널: {len(signals)}"
    )

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    for analysis in signals:

        try:

            create_position(
                state,
                analysis
            )

        except Exception as e:

            print(
                f"시그널 생성 오류 "
                f"{analysis.get('market')}:",
                e
            )

    print("=" * 50)
    print("스캔 완료")


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

        # 오류가 발생해도 Telegram으로 알려줌
        send_telegram(
            "⚠️ <b>봇 실행 오류</b>\n\n"
            f"{str(e)[:1000]}"
        )

        raise
