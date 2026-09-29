import os
import json
import time
import math
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone, timedelta


# ============================================================
# UPBIT SMART SIGNAL BOT V5.3
# Korean Alert + Korean Coin Name + Accurate SL/TP Tracking
# ============================================================

UPBIT_BASE_URL = "https://api.upbit.com/v1"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

STATE_FILE = "tracked_coins.json"

KST = timezone(timedelta(hours=9))


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
# KOREAN COIN NAME MAP
# ============================================================

COIN_NAMES = {
    "BTC": "비트코인",
    "ETH": "이더리움",
    "XRP": "리플",
    "DOGE": "도지코인",
    "ADA": "에이다",
    "SOL": "솔라나",
    "AVAX": "아발란체",
    "DOT": "폴카닷",
    "TRX": "트론",
    "LINK": "체인링크",
    "SHIB": "시바이누",
    "SAND": "샌드박스",
    "MANA": "디센트럴랜드",
    "ATOM": "코스모스",
    "NEAR": "니어프로토콜",
    "APT": "앱토스",
    "ARB": "아비트럼",
    "OP": "옵티미즘",
    "STX": "스택스",
    "IMX": "이뮤터블엑스",
    "ETC": "이더리움클래식",
    "BCH": "비트코인캐시",
    "LTC": "라이트코인",
    "EOS": "이오스",
    "XLM": "스텔라루멘",
    "HBAR": "헤데라",
    "ALGO": "알고랜드",
    "ICP": "인터넷컴퓨터",
    "FIL": "파일코인",
    "APT": "앱토스",
    "SUI": "수이",
    "SEI": "세이",
    "INJ": "인젝티브",
    "TIA": "셀레스티아",
    "JUP": "주피터",
    "ONDO": "온도파이낸스",
    "ENA": "에테나",
    "WLD": "월드코인",
    "PEPE": "페페",
    "BONK": "봉크",
    "FLOKI": "플로키",
    "WIF": "도그위프햇",
    "POL": "폴리곤",
    "STG": "스타게이트",
    "GRT": "그래프",
    "AAVE": "에이브",
    "UNI": "유니스왑",
    "MKR": "메이커",
    "CRV": "커브",
    "COMP": "컴파운드",
    "SNX": "신세틱스",
    "1INCH": "1인치",
    "ENS": "이더리움네임서비스",
    "LDO": "리도다오",
    "RUNE": "토르체인",
    "THETA": "쎄타토큰",
    "AXS": "엑시인피니티",
    "GALA": "갈라",
    "FLOW": "플로우",
    "CHZ": "칠리즈",
    "MASK": "마스크네트워크",
    "ZRX": "제로엑스",
    "BAT": "베이직어텐션토큰",
    "QTUM": "퀀텀",
    "KAVA": "카바",
    "KSM": "쿠사마",
    "CELO": "셀로",
    "MINA": "미나",
    "BLUR": "블러",
    "CYBER": "사이버",
    "ARK": "아크",
    "2Z": "투지",
}


# ============================================================
# BASIC HELPERS
# ============================================================

def now_kst():
    return datetime.now(KST)


def now_iso():
    return now_kst().isoformat()


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        x = float(value)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def ticker_name(market):
    """
    KRW-ARK -> 아크 (ARK)
    KRW-JUP -> 주피터 (JUP)
    """
    ticker = market.replace("KRW-", "").upper()
    korean = COIN_NAMES.get(ticker)

    if korean:
        return f"{korean} ({ticker})"

    return ticker


def ticker_only(market):
    return market.replace("KRW-", "").upper()


def fmt_price(price):
    price = safe_float(price)

    if price >= 100000:
        return f"{price:,.0f}"
    if price >= 1000:
        return f"{price:,.1f}"
    if price >= 100:
        return f"{price:,.2f}"
    if price >= 10:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:,.3f}"
    if price >= 0.1:
        return f"{price:,.4f}"
    return f"{price:,.6f}"


def pct(a, b):
    if b == 0:
        return 0.0
    return ((a - b) / b) * 100.0


def escape_html(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram 설정 없음")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        r = requests.post(url, json=payload, timeout=15)

        if r.ok:
            return True

        print("Telegram 오류:", r.text)

    except Exception as e:
        print("Telegram 전송 실패:", e)

    return False


# ============================================================
# STATE
# ============================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "positions": {},
            "history": [],
            "sent_signal_ids": [],
            "legacy_positions": {}
        }

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        if not isinstance(state, dict):
            raise ValueError("state 형식 오류")

    except Exception as e:
        print("상태 파일 읽기 실패:", e)
        state = {}

    state.setdefault("positions", {})
    state.setdefault("history", [])
    state.setdefault("sent_signal_ids", [])
    state.setdefault("legacy_positions", {})

    return state


def save_state(state):
    temp_file = STATE_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp_file, STATE_FILE)


# ============================================================
# POSITION MIGRATION
# ============================================================

def first_existing(data, keys, default=None):
    for key in keys:
        if key in data:
            value = data[key]
            if value is not None:
                return value

    return default


def normalize_position(position):
    if not isinstance(position, dict):
        return None

    market = position.get("market")

    if not market:
        return None

    entry = safe_float(
        first_existing(
            position,
            [
                "entry",
                "entry_price",
                "buy_price",
                "price",
                "avg_price",
                "average_price"
            ]
        ),
        0
    )

    stop = safe_float(
        first_existing(
            position,
            [
                "stop",
                "sl",
                "stop_loss",
                "stop_price",
                "stopPrice"
            ]
        ),
        0
    )

    tp1 = safe_float(
        first_existing(
            position,
            [
                "tp1",
                "target1",
                "target_1",
                "take_profit_1",
                "takeProfit1",
                "take_profit"
            ]
        ),
        0
    )

    tp2 = safe_float(
        first_existing(
            position,
            [
                "tp2",
                "target2",
                "target_2",
                "take_profit_2",
                "takeProfit2"
            ]
        ),
        0
    )

    tp3 = safe_float(
        first_existing(
            position,
            [
                "tp3",
                "target3",
                "target_3",
                "take_profit_3",
                "takeProfit3"
            ]
        ),
        0
    )

    if entry <= 0:
        return None

    # SL이 없지만 TP1이 있으면 역산
    if stop <= 0 and tp1 > entry:
        risk = (tp1 - entry) / 1.5
        stop = entry - risk

    # TP1이 없으면 SL에서 역산
    if tp1 <= 0 and stop > 0 and stop < entry:
        risk = entry - stop
        tp1 = entry + risk * 1.5

    if tp2 <= 0 and stop > 0 and stop < entry:
        risk = entry - stop
        tp2 = entry + risk * 2.0

    if tp3 <= 0 and stop > 0 and stop < entry:
        risk = entry - stop
        tp3 = entry + risk * 3.0

    if stop <= 0 or tp1 <= 0 or tp2 <= 0 or tp3 <= 0:
        return None

    position["market"] = market
    position["entry"] = entry
    position["stop"] = stop
    position["tp1"] = tp1
    position["tp2"] = tp2
    position["tp3"] = tp3

    position.setdefault("stage", 0)
    position.setdefault("created_at", now_iso())

    return position


def migrate_positions(state):
    changed = False

    positions = state.get("positions", {})

    for market in list(positions.keys()):

        original = positions[market]

        normalized = normalize_position(original)

        if normalized is None:
            state.setdefault("legacy_positions", {})[market] = original
            del positions[market]
            changed = True
            continue

        if normalized != original:
            positions[market] = normalized
            changed = True

    return changed


# ============================================================
# UPBIT API
# ============================================================

def upbit_get(path, params=None):
    url = UPBIT_BASE_URL + path

    try:
        r = requests.get(
            url,
            params=params,
            timeout=15
        )

        r.raise_for_status()

        return r.json()

    except Exception as e:
        print("Upbit API 오류:", e)
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
        if x["market"].startswith("KRW-")
    ]


def get_tickers(markets):
    if not markets:
        return []

    data = upbit_get(
        "/ticker",
        {
            "markets": ",".join(markets)
        }
    )

    return data or []


def get_candles(market, unit, count=200):
    """
    unit:
    1 / 3 / 5 / 10 / 15 / 30 / 60 / 240
    1440 = 일봉
    """

    if unit == 1440:
        path = "/candles/days"
        params = {
            "market": market,
            "count": count
        }
    else:
        path = f"/candles/minutes/{unit}"
        params = {
            "market": market,
            "count": count
        }

    data = upbit_get(path, params)

    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data)

    df["timestamp"] = pd.to_datetime(
        df["candle_date_time_kst"]
    )

    df = df.sort_values("timestamp").reset_index(drop=True)

    df.rename(
        columns={
            "opening_price": "open",
            "high_price": "high",
            "low_price": "low",
            "trade_price": "close",
            "candle_acc_trade_volume": "volume",
            "candle_acc_trade_price": "value"
        },
        inplace=True
    )

    return df[
        [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "value"
        ]
    ]


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

    rs = avg_gain / avg_loss.replace(0, np.nan)

    result = 100 - (100 / (1 + rs))

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


def adx(df, length=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where(
            (up_move > down_move) & (up_move > 0),
            up_move,
            0
        ),
        index=df.index
    )

    minus_dm = pd.Series(
        np.where(
            (down_move > up_move) & (down_move > 0),
            down_move,
            0
        ),
        index=df.index
    )

    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ],
        axis=1
    ).max(axis=1)

    atr_value = tr.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()

    plus_di = (
        100
        * plus_dm.ewm(alpha=1 / length, adjust=False).mean()
        / atr_value.replace(0, np.nan)
    )

    minus_di = (
        100
        * minus_dm.ewm(alpha=1 / length, adjust=False).mean()
        / atr_value.replace(0, np.nan)
    )

    dx = (
        100
        * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0, np.nan)
    )

    return dx.ewm(
        alpha=1 / length,
        adjust=False
    ).mean().fillna(0)


# ============================================================
# BTC REGIME
# ============================================================

def get_btc_regime():
    try:
        df15 = yf.download(
            "BTC-USD",
            period="3d",
            interval="15m",
            progress=False,
            auto_adjust=False
        )

        df1h = yf.download(
            "BTC-USD",
            period="7d",
            interval="1h",
            progress=False,
            auto_adjust=False
        )

        if df15.empty or df1h.empty:
            return {
                "state": "NEUTRAL",
                "change15": 0,
                "change1h": 0
            }

        close15 = df15["Close"]

        if isinstance(close15, pd.DataFrame):
            close15 = close15.iloc[:, 0]

        close1h = df1h["Close"]

        if isinstance(close1h, pd.DataFrame):
            close1h = close1h.iloc[:, 0]

        change15 = (
            (float(close15.iloc[-1]) /
             float(close15.iloc[-2]) - 1)
            * 100
        )

        change1h = (
            (float(close1h.iloc[-1]) /
             float(close1h.iloc[-2]) - 1)
            * 100
        )

        if (
            change15 <= BTC_15M_CRASH_PCT
            or change1h <= BTC_1H_CRASH_PCT
        ):
            state = "CRASH"

        elif change15 < -0.5 or change1h < -0.8:
            state = "WEAK"

        elif change15 > 0.7 and change1h > 1.0:
            state = "BULL"

        else:
            state = "NEUTRAL"

        return {
            "state": state,
            "change15": change15,
            "change1h": change1h
        }

    except Exception as e:
        print("BTC 분석 실패:", e)

        return {
            "state": "NEUTRAL",
            "change15": 0,
            "change1h": 0
        }


# ============================================================
# BTC KOREAN
# ============================================================

def btc_state_korean(state):
    return {
        "BULL": "강세",
        "WEAK": "약세",
        "CRASH": "급락",
        "NEUTRAL": "중립"
    }.get(state, "중립")


# ============================================================
# FAST CANDIDATE FILTER
# ============================================================

def fast_filter(ticker):
    price = safe_float(ticker.get("trade_price"))

    if price <= 0:
        return False

    value = safe_float(
        ticker.get("acc_trade_price_24h")
    )

    if value < MIN_FINAL_24H_TRADE_VALUE:
        return False

    return True


# ============================================================
# SIGNAL ANALYSIS
# ============================================================

def analyze_market(market, btc):
    df1d = get_candles(market, 1440, 150)
    df4h = get_candles(market, 240, 150)
    df1h = get_candles(market, 60, 150)
    df15 = get_candles(market, 15, 200)

    if (
        len(df1d) < 50
        or len(df4h) < 50
        or len(df1h) < 50
        or len(df15) < 50
    ):
        return None

    price = safe_float(df15["close"].iloc[-1])

    # --------------------------------------------------------
    # Indicators
    # --------------------------------------------------------

    df1d["ema20"] = ema(df1d["close"], 20)
    df1d["ema50"] = ema(df1d["close"], 50)

    df4h["ema20"] = ema(df4h["close"], 20)
    df4h["ema50"] = ema(df4h["close"], 50)

    df1h["ema20"] = ema(df1h["close"], 20)
    df1h["ema50"] = ema(df1h["close"], 50)

    df15["ema20"] = ema(df15["close"], 20)
    df15["ema50"] = ema(df15["close"], 50)

    df15["rsi"] = rsi(df15["close"], 14)
    df15["atr"] = atr(df15, 14)
    df15["adx"] = adx(df15, 14)

    # --------------------------------------------------------
    # Trend
    # --------------------------------------------------------

    trend_1d = (
        df1d["close"].iloc[-1]
        > df1d["ema20"].iloc[-1]
        > df1d["ema50"].iloc[-1]
    )

    trend_4h = (
        df4h["close"].iloc[-1]
        > df4h["ema20"].iloc[-1]
        > df4h["ema50"].iloc[-1]
    )

    trend_1h = (
        df1h["close"].iloc[-1]
        > df1h["ema20"].iloc[-1]
        > df1h["ema50"].iloc[-1]
    )

    trend_15m = (
        df15["close"].iloc[-1]
        > df15["ema20"].iloc[-1]
    )

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    recent_volume = df15["volume"].iloc[-1]

    avg_volume = (
        df15["volume"]
        .iloc[-21:-1]
        .mean()
    )

    volume_ratio = (
        recent_volume / avg_volume * 100
        if avg_volume > 0
        else 0
    )

    # --------------------------------------------------------
    # Candle strength
    # --------------------------------------------------------

    candle = df15.iloc[-1]

    candle_range = candle["high"] - candle["low"]

    if candle_range <= 0:
        return None

    body = abs(candle["close"] - candle["open"])

    body_ratio = body / candle_range

    close_position = (
        (candle["close"] - candle["low"])
        / candle_range
    )

    strong_candle = (
        candle["close"] > candle["open"]
        and body_ratio >= MIN_BODY_RATIO
        and close_position >= MIN_CLOSE_POSITION
    )

    # --------------------------------------------------------
    # Breakout
    # --------------------------------------------------------

    previous_high = (
        df15["high"]
        .iloc[-21:-1]
        .max()
    )

    breakout = price > previous_high

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi_value = safe_float(
        df15["rsi"].iloc[-1]
    )

    atr_value = safe_float(
        df15["atr"].iloc[-1]
    )

    # --------------------------------------------------------
    # Support / Stop
    # --------------------------------------------------------

    support = float(
        df15["low"]
        .iloc[-15:-1]
        .min()
    )

    stop = support - (
        atr_value * 0.25
    )

    if stop <= 0 or stop >= price:
        return None

    stop_loss_pct = (
        (price - stop) / price
    ) * 100

    if stop_loss_pct < MIN_STOP_LOSS_PCT:
        return None

    if stop_loss_pct > MAX_STOP_LOSS_PCT:
        return None

    # --------------------------------------------------------
    # Targets
    # --------------------------------------------------------

    risk = price - stop

    tp1 = price + risk * 1.5
    tp2 = price + risk * 2.0
    tp3 = price + risk * 3.0

    tp1_distance_pct = (
        (tp1 - price) / price
    ) * 100

    if tp1_distance_pct > MAX_TP1_DISTANCE_PCT:
        return None

    # --------------------------------------------------------
    # Score
    # --------------------------------------------------------

    score = 0

    if trend_1d:
        score += 20

    if trend_4h:
        score += 20

    if trend_1h:
        score += 15

    if trend_15m:
        score += 15

    if volume_ratio >= STRONG_VOLUME_RATIO:
        score += 15
    elif volume_ratio >= MIN_VOLUME_RATIO:
        score += 10

    if strong_candle:
        score += 10

    if breakout:
        score += 10

    if stop_loss_pct <= 4:
        score += 5

    if rsi_value >= 82:
        score -= 5

    if rsi_value < 50:
        score -= 2

    change_4h = pct(
        float(df4h["close"].iloc[-1]),
        float(df4h["close"].iloc[-25])
    )

    if change_4h > 18:
        score -= 8

    ema_distance = (
        (price - float(df15["ema20"].iloc[-1]))
        / float(df15["ema20"].iloc[-1])
    ) * 100

    if ema_distance > MAX_ENTRY_DISTANCE_FROM_EMA20:
        score -= 7

    if tp1_distance_pct > MAX_TP1_DISTANCE_PCT:
        score -= 8

    if btc["state"] == "BULL":
        score += 5

    elif btc["state"] == "WEAK":
        score -= 3

    elif btc["state"] == "CRASH":
        score -= 15

    score = max(
        0,
        min(100, int(score))
    )

    # --------------------------------------------------------
    # Final qualification
    # --------------------------------------------------------

    if btc["state"] == "CRASH":
        qualified = score >= STRONG_SCORE

    elif btc["state"] == "WEAK":
        qualified = (
            score >= WEAK_BTC_SCORE
            and volume_ratio >= STRONG_VOLUME_RATIO
        )

    else:
        qualified = score >= SIGNAL_SCORE

    if not qualified:
        return {
            "market": market,
            "pass": False,
            "score": score,
            "price": price,
            "rsi": rsi_value,
            "volume_ratio": volume_ratio,
            "reason": "최종 점수 부족"
        }

    return {
        "market": market,
        "pass": True,
        "score": score,
        "price": price,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "rsi": rsi_value,
        "volume_ratio": volume_ratio,
        "ema_distance": ema_distance,
        "stop_loss_pct": stop_loss_pct,
        "tp1_distance_pct": tp1_distance_pct,
        "body_ratio": body_ratio,
        "close_position": close_position,
        "breakout": breakout,
        "btc_state": btc["state"],
        "signal_candle": str(
            df15["timestamp"].iloc[-1]
        )
    }


# ============================================================
# DUPLICATE SIGNAL PROTECTION
# ============================================================

def signal_id(analysis):
    return (
        analysis["market"]
        + "_"
        + analysis["signal_candle"]
    )


def signal_already_sent(state, analysis):
    sid = signal_id(analysis)

    return sid in state.get(
        "sent_signal_ids",
        []
    )


def can_create_new_signal(state, analysis):
    market = analysis["market"]
    price = analysis["price"]

    if market in state.get("positions", {}):
        return False

    now = datetime.now(KST)

    for item in reversed(
        state.get("history", [])
    ):

        if item.get("market") != market:
            continue

        created_at = item.get("created_at")

        if not created_at:
            continue

        try:
            dt = datetime.fromisoformat(
                created_at
            )
        except Exception:
            continue

        hours = (
            now - dt
        ).total_seconds() / 3600

        if hours <= SIGNAL_COOLDOWN_HOURS:

            old_price = safe_float(
                item.get("entry")
            )

            if old_price <= 0:
                return False

            distance = abs(
                (price - old_price)
                / old_price
            ) * 100

            if distance < MIN_NEW_SIGNAL_PRICE_DISTANCE:
                return False

    return True


# ============================================================
# LONG SIGNAL MESSAGE
# ============================================================

def build_long_message(analysis):
    market = analysis["market"]

    name = ticker_name(market)

    entry = analysis["price"]
    stop = analysis["stop"]
    tp1 = analysis["tp1"]
    tp2 = analysis["tp2"]
    tp3 = analysis["tp3"]

    stop_pct = analysis["stop_loss_pct"]

    tp1_pct = pct(tp1, entry)
    tp2_pct = pct(tp2, entry)
    tp3_pct = pct(tp3, entry)

    rsi_value = analysis["rsi"]
    volume_ratio = analysis["volume_ratio"]
    ema_distance = analysis["ema_distance"]

    score = analysis["score"]

    btc_state = btc_state_korean(
        analysis["btc_state"]
    )

    return f"""
<b>🟢 롱 시그널 발생</b>
━━━━━━━━━━━━━━━━━━

💰 <b>{escape_html(name)}</b>

📊 신호 점수  <b>{score} / 100</b>
₿ 비트코인 상태  <b>{btc_state}</b>

━━━━━━━━━━━━━━━━━━
<b>🎯 매매 계획</b>

진입가      <b>{fmt_price(entry)}</b>
손절가      <b>{fmt_price(stop)}</b>

익절 1      <b>{fmt_price(tp1)}</b>  (+{tp1_pct:.2f}%)
익절 2      <b>{fmt_price(tp2)}</b>  (+{tp2_pct:.2f}%)
익절 3      <b>{fmt_price(tp3)}</b>  (+{tp3_pct:.2f}%)

━━━━━━━━━━━━━━━━━━
<b>🛡️ 리스크 관리</b>

손절폭      <b>-{stop_pct:.2f}%</b>
익절 1 R:R  <b>1 : 1.50</b>
익절 2 R:R  <b>1 : 2.00</b>
익절 3 R:R  <b>1 : 3.00</b>

━━━━━━━━━━━━━━━━━━
<b>📈 시장 상태</b>

RSI         <b>{rsi_value:.1f}</b>
거래량      <b>{volume_ratio:.0f}%</b>
EMA20 이격  <b>{ema_distance:+.2f}%</b>

━━━━━━━━━━━━━━━━━━
<b>⏱️ 15분봉 확정 시그널</b>

⚠️ 신호 발생 후 추격진입에 주의
━━━━━━━━━━━━━━━━━━
""".strip()


# ============================================================
# STOP LOSS MESSAGE
# ============================================================

def build_stop_message(
    market,
    entry,
    stop,
    current
):
    name = ticker_name(market)

    stop_pct = (
        (stop - entry)
        / entry
    ) * 100

    current_pct = (
        (current - entry)
        / entry
    ) * 100

    return f"""
<b>🔴 손절 발생</b>
━━━━━━━━━━━━━━━━━━

💰 <b>{escape_html(name)}</b>

진입가       <b>{fmt_price(entry)}</b>
손절 기준가  <b>{fmt_price(stop)}</b>
현재가       <b>{fmt_price(current)}</b>

손절 기준    <b>{stop_pct:.2f}%</b>
현재 변동    <b>{current_pct:.2f}%</b>

━━━━━━━━━━━━━━━━━━
🛑 <b>손절 조건 도달</b>

설정된 손절가 기준으로
포지션 추적을 종료했습니다.
━━━━━━━━━━━━━━━━━━
""".strip()


# ============================================================
# TP1 MESSAGE
# ============================================================

def build_tp1_message(
    market,
    entry,
    tp1,
    current
):
    name = ticker_name(market)

    profit = (
        (tp1 - entry)
        / entry
    ) * 100

    return f"""
<b>🟢 익절 1 도달</b>
━━━━━━━━━━━━━━━━━━

💰 <b>{escape_html(name)}</b>

진입가       <b>{fmt_price(entry)}</b>
익절 1       <b>{fmt_price(tp1)}</b>
현재가       <b>{fmt_price(current)}</b>

수익률       <b>+{profit:.2f}%</b>

━━━━━━━━━━━━━━━━━━
🛡️ <b>손절가 → 진입가 이동</b>

이제 남은 포지션의
손실 위험을 줄입니다.
━━━━━━━━━━━━━━━━━━
""".strip()


# ============================================================
# TP2 MESSAGE
# ============================================================

def build_tp2_message(
    market,
    entry,
    tp1,
    tp2,
    current
):
    name = ticker_name(market)

    profit = (
        (tp2 - entry)
        / entry
    ) * 100

    return f"""
<b>🟢 익절 2 도달</b>
━━━━━━━━━━━━━━━━━━

💰 <b>{escape_html(name)}</b>

진입가       <b>{fmt_price(entry)}</b>
익절 2       <b>{fmt_price(tp2)}</b>
현재가       <b>{fmt_price(current)}</b>

수익률       <b>+{profit:.2f}%</b>

━━━━━━━━━━━━━━━━━━
🛡️ <b>손절가 → 익절 1 가격 이동</b>

수익 보호 단계로
전환했습니다.
━━━━━━━━━━━━━━━━━━
""".strip()


# ============================================================
# TP3 MESSAGE
# ============================================================

def build_tp3_message(
    market,
    entry,
    tp3,
    current
):
    name = ticker_name(market)

    profit = (
        (tp3 - entry)
        / entry
    ) * 100

    return f"""
<b>🏆 최종 익절 달성</b>
━━━━━━━━━━━━━━━━━━

💰 <b>{escape_html(name)}</b>

진입가       <b>{fmt_price(entry)}</b>
최종 익절    <b>{fmt_price(tp3)}</b>
현재가       <b>{fmt_price(current)}</b>

최종 수익    <b>+{profit:.2f}%</b>

━━━━━━━━━━━━━━━━━━
🎯 <b>최종 목표 도달</b>

포지션 추적을 종료했습니다.
━━━━━━━━━━━━━━━━━━
""".strip()


# ============================================================
# CREATE POSITION
# ============================================================

def create_position(state, analysis):
    latest_state = load_state()

    migrate_positions(latest_state)

    if signal_already_sent(
        latest_state,
        analysis
    ):
        print(
            "중복 시그널 차단:",
            analysis["market"]
        )
        return False

    if not can_create_new_signal(
        latest_state,
        analysis
    ):
        print(
            "신규 포지션 차단:",
            analysis["market"]
        )
        return False

    market = analysis["market"]

    position = {
        "market": market,
        "entry": analysis["price"],
        "stop": analysis["stop"],
        "tp1": analysis["tp1"],
        "tp2": analysis["tp2"],
        "tp3": analysis["tp3"],
        "stage": 0,
        "score": analysis["score"],
        "signal_id": signal_id(analysis),
        "signal_candle": analysis["signal_candle"],
        "created_at": now_iso()
    }

    latest_state["positions"][market] = position

    latest_state.setdefault(
        "sent_signal_ids",
        []
    ).append(
        position["signal_id"]
    )

    latest_state["sent_signal_ids"] = (
        latest_state["sent_signal_ids"][-500:]
    )

    latest_state.setdefault(
        "history",
        []
    ).append(
        {
            "market": market,
            "entry": analysis["price"],
            "score": analysis["score"],
            "signal_id": position["signal_id"],
            "created_at": position["created_at"],
            "status": "OPEN"
        }
    )

    latest_state["history"] = (
        latest_state["history"][-500:]
    )

    save_state(latest_state)

    message = build_long_message(
        analysis
    )

    send_telegram(message)

    print(
        "신규 포지션 생성:",
        ticker_name(market),
        analysis["score"]
    )

    return True


# ============================================================
# CLOSE POSITION
# ============================================================

def close_position(
    state,
    market,
    position,
    exit_price,
    reason
):
    entry = safe_float(
        position.get("entry")
    )

    if entry <= 0:
        return

    pnl = (
        (exit_price - entry)
        / entry
    ) * 100

    if reason == "SL":
        message = build_stop_message(
            market,
            entry,
            safe_float(position.get("stop")),
            exit_price
        )

    elif reason == "TP3":
        message = build_tp3_message(
            market,
            entry,
            safe_float(position.get("tp3")),
            exit_price
        )

    else:
        message = ""

    if message:
        send_telegram(message)

    state.setdefault(
        "history",
        []
    ).append(
        {
            "market": market,
            "entry": entry,
            "exit": exit_price,
            "pnl": pnl,
            "reason": reason,
            "closed_at": now_iso(),
            "status": "CLOSED"
        }
    )

    state["history"] = (
        state["history"][-500:]
    )

    if market in state.get(
        "positions",
        {}
    ):
        del state["positions"][market]

    save_state(state)

    print(
        f"{market} 종료:",
        reason,
        f"{pnl:.2f}%"
    )


# ============================================================
# TRACK ONE POSITION
# ============================================================

def track_position(
    state,
    market,
    position
):
    df = get_candles(
        market,
        1,
        5
    )

    if df.empty:
        return

    candle = df.iloc[-1]

    current = safe_float(
        candle["close"]
    )

    candle_high = safe_float(
        candle["high"]
    )

    candle_low = safe_float(
        candle["low"]
    )

    entry = safe_float(
        position["entry"]
    )

    stop = safe_float(
        position["stop"]
    )

    tp1 = safe_float(
        position["tp1"]
    )

    tp2 = safe_float(
        position["tp2"]
    )

    tp3 = safe_float(
        position["tp3"]
    )

    stage = int(
        position.get(
            "stage",
            0
        )
    )

    # --------------------------------------------------------
    # SL FIRST
    #
    # 현재가(close)가 아니라 1분봉 LOW가 SL에 닿았는지 확인
    # --------------------------------------------------------

    if candle_low <= stop:

        # 시뮬레이션 청산가격은 현재가가 아니라
        # 설정된 손절가 자체로 기록
        close_position(
            state,
            market,
            position,
            stop,
            "SL"
        )

        return

    # --------------------------------------------------------
    # STAGE 0 -> TP1
    # --------------------------------------------------------

    if stage == 0:

        if candle_high >= tp1:

            position["stage"] = 1

            if MOVE_SL_TO_ENTRY_AFTER_TP1:
                position["stop"] = entry

            state["positions"][market] = position

            save_state(state)

            send_telegram(
                build_tp1_message(
                    market,
                    entry,
                    tp1,
                    current
                )
            )

            print(
                "TP1 도달:",
                ticker_name(market)
            )

            return

    # --------------------------------------------------------
    # STAGE 1 -> TP2
    # --------------------------------------------------------

    if stage == 1:

        if candle_low <= position["stop"]:

            close_position(
                state,
                market,
                position,
                position["stop"],
                "SL"
            )

            return

        if candle_high >= tp2:

            position["stage"] = 2

            if MOVE_SL_TO_TP1_AFTER_TP2:
                position["stop"] = tp1

            state["positions"][market] = position

            save_state(state)

            send_telegram(
                build_tp2_message(
                    market,
                    entry,
                    tp1,
                    tp2,
                    current
                )
            )

            print(
                "TP2 도달:",
                ticker_name(market)
            )

            return

    # --------------------------------------------------------
    # STAGE 2 -> TP3
    # --------------------------------------------------------

    if stage == 2:

        if candle_low <= position["stop"]:

            close_position(
                state,
                market,
                position,
                position["stop"],
                "SL"
            )

            return

        if candle_high >= tp3:

            close_position(
                state,
                market,
                position,
                tp3,
                "TP3"
            )

            return


# ============================================================
# TRACK ALL POSITIONS
# ============================================================

def track_positions(state):
    changed = migrate_positions(state)

    if changed:
        save_state(state)

    positions = dict(
        state.get(
            "positions",
            {}
        )
    )

    if not positions:
        return

    print()
    print("=" * 44)
    print("기존 포지션 추적")
    print("=" * 44)

    for market, position in positions.items():

        try:
            print(
                f"[추적] {ticker_name(market)}"
            )

            track_position(
                state,
                market,
                position
            )

            time.sleep(0.15)

        except Exception as e:
            print(
                "포지션 추적 오류:",
                market,
                e
            )


# ============================================================
# MAIN SCAN
# ============================================================

def main():
    print()
    print("=" * 52)
    print(" UPBIT SMART SIGNAL BOT V5.3")
    print(" 한글 알림 + 종목명 + 정밀 손절/익절 추적")
    print("=" * 52)

    state = load_state()

    # --------------------------------------------------------
    # 기존 포지션 먼저 추적
    # --------------------------------------------------------

    track_positions(state)

    # 상태 다시 읽기
    state = load_state()

    # --------------------------------------------------------
    # BTC
    # --------------------------------------------------------

    print()
    print("비트코인 시장 상태 분석 중...")

    btc = get_btc_regime()

    print(
        f"BTC 상태 : {btc_state_korean(btc['state'])}"
    )

    print(
        f"BTC 15분 : {btc['change15']:+.2f}%"
    )

    print(
        f"BTC 1시간 : {btc['change1h']:+.2f}%"
    )

    # --------------------------------------------------------
    # MARKETS
    # --------------------------------------------------------

    markets = get_markets()

    print()
    print(
        f"KRW 마켓 : {len(markets)}개"
    )

    # --------------------------------------------------------
    # TICKERS
    # --------------------------------------------------------

    tickers = get_tickers(markets)

    if not tickers:
        print("시세 조회 실패")
        return

    candidates = []

    for ticker in tickers:

        market = ticker.get("market")

        if not market:
            continue

        if not fast_filter(ticker):
            continue

        value = safe_float(
            ticker.get(
                "acc_trade_price_24h"
            )
        )

        change_rate = safe_float(
            ticker.get(
                "signed_change_rate"
            )
        ) * 100

        candidates.append(
            {
                "market": market,
                "value": value,
                "change_rate": change_rate
            }
        )

    candidates.sort(
        key=lambda x: (
            x["value"],
            x["change_rate"]
        ),
        reverse=True
    )

    candidates = candidates[
        :MAX_DEEP_SCAN
    ]

    print(
        f"정밀 분석 대상 : {len(candidates)}개"
    )

    # --------------------------------------------------------
    # BTC CRASH
    # --------------------------------------------------------

    if btc["state"] == "CRASH":
        print()
        print(
            "⚠️ 비트코인 급락 상태"
        )
        print(
            "강한 개별 종목만 정밀 검사합니다."
        )

    # --------------------------------------------------------
    # DEEP SCAN
    # --------------------------------------------------------

    best_candidates = []

    for idx, item in enumerate(
        candidates,
        start=1
    ):

        market = item["market"]

        print(
            f"[{idx}/{len(candidates)}] "
            f"{ticker_name(market)} 분석 중..."
        )

        try:

            analysis = analyze_market(
                market,
                btc
            )

            if not analysis:
                print("  → 데이터 부족")
                continue

            if not analysis.get("pass"):
                print(
                    f"  → 탈락 "
                    f"점수={analysis.get('score', 0)}"
                )
                continue

            print(
                f"  → PASS "
                f"점수={analysis['score']}"
            )

            best_candidates.append(
                analysis
            )

        except Exception as e:

            print(
                "  → 분석 오류:",
                e
            )

        time.sleep(0.12)

    # --------------------------------------------------------
    # SCORE SORT
    # --------------------------------------------------------

    best_candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # --------------------------------------------------------
    # CREATE SIGNAL
    # --------------------------------------------------------

    if best_candidates:

        print()
        print("=" * 44)
        print("신규 시그널 후보")
        print("=" * 44)

        for analysis in best_candidates:

            market = analysis["market"]

            print(
                ticker_name(market),
                "Score=",
                analysis["score"]
            )

            latest_state = load_state()

            if signal_already_sent(
                latest_state,
                analysis
            ):
                print(
                    "  → 중복 시그널 차단"
                )
                continue

            if not can_create_new_signal(
                latest_state,
                analysis
            ):
                print(
                    "  → 신규 진입 조건 차단"
                )
                continue

            created = create_position(
                latest_state,
                analysis
            )

            if created:
                print(
                    "  → 신규 롱 시그널 전송 완료"
                )

                # 한 번의 실행에서
                # 지나치게 많은 동시 진입 방지
                break

    else:

        print()
        print(
            "현재 신규 진입 조건을 만족하는 종목이 없습니다."
        )

    # --------------------------------------------------------
    # FINAL STATE
    # --------------------------------------------------------

    final_state = load_state()

    print()
    print("=" * 44)
    print("현재 추적 포지션")
    print("=" * 44)

    positions = final_state.get(
        "positions",
        {}
    )

    if not positions:
        print("없음")

    else:

        for market, position in positions.items():

            print(
                f"{ticker_name(market)} | "
                f"진입 {fmt_price(position.get('entry'))} | "
                f"손절 {fmt_price(position.get('stop'))} | "
                f"TP1 {fmt_price(position.get('tp1'))} | "
                f"TP2 {fmt_price(position.get('tp2'))} | "
                f"TP3 {fmt_price(position.get('tp3'))} | "
                f"단계 {position.get('stage', 0)}"
            )

    print()
    print("=" * 52)
    print("스캔 완료")
    print("=" * 52)


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()
