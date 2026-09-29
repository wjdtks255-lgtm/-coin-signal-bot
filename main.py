import os
import json
import requests
import yfinance as yf
import pandas as pd
import numpy as np

from datetime import datetime, timezone, timedelta


# ============================================================
# GOLD FUTURES SMART SIGNAL BOT
# BALANCED FINAL DEPLOYMENT VERSION
# ============================================================

TICKER = "GC=F"

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"


# ============================================================
# TELEGRAM SETTINGS
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# STRATEGY SETTINGS
# ============================================================

TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00

# SL limits
MIN_SL_ATR = 0.55
MAX_SL_ATR = 2.50

# Entry distance from EMA20
MAX_ENTRY_DISTANCE_ATR = 1.50

# 5M confirmation distance
MAX_SIGNAL_MOVE_ATR = 0.80

# Minimum ADX
MIN_ADX = 18.0

# Minimum score
MIN_SIGNAL_SCORE = 4

# Cooldown after SL / TP3
COOLDOWN_MINUTES = 60


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials are missing.")
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
            timeout=20
        )

        if response.status_code == 200:
            print("Telegram message sent.")
            return True

        print(
            "Telegram error:",
            response.status_code,
            response.text
        )

    except Exception as e:

        print(
            "Telegram exception:",
            e
        )

    return False


# ============================================================
# TIME
# ============================================================

def normalize_timestamp(value):

    ts = pd.Timestamp(value)

    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")

    return ts


def format_kst_time(iso_str):

    try:

        dt_utc = datetime.fromisoformat(
            iso_str
        )

        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(
                tzinfo=timezone.utc
            )

        kst_zone = timezone(
            timedelta(hours=9)
        )

        dt_kst = dt_utc.astimezone(
            kst_zone
        )

        return dt_kst.strftime(
            "%Y-%m-%d %H:%M:%S (KST)"
        )

    except Exception:

        return str(iso_str)


# ============================================================
# STATE
# ============================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            state = json.load(f)

        if isinstance(state, dict):
            return state

        return {}

    except Exception as e:

        print(
            "State load error:",
            e
        )

        return {}


def save_state(state):

    try:

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                state,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        print(
            "State save error:",
            e
        )


# ============================================================
# LOG
# ============================================================

def log_event(event_type, data=None):

    logs = []

    if os.path.exists(LOG_FILE):

        try:

            with open(
                LOG_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                logs = json.load(f)

        except Exception:

            logs = []

    if not isinstance(logs, list):
        logs = []

    logs.append(
        {
            "time": pd.Timestamp.now(
                tz="UTC"
            ).isoformat(),

            "event": event_type,

            "data": data or {}
        }
    )

    # Keep last 500 events
    logs = logs[-500:]

    try:

        with open(
            LOG_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                logs,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        print(
            "Log save error:",
            e
        )


# ============================================================
# DATA CLEANING
# ============================================================

def clean_dataframe(df):

    if df is None or df.empty:
        return None

    df = df.copy()

    if isinstance(
        df.columns,
        pd.MultiIndex
    ):

        df.columns = [
            col[0]
            if isinstance(col, tuple)
            else col
            for col in df.columns
        ]

    required = [
        "Open",
        "High",
        "Low",
        "Close"
    ]

    for col in required:

        if col not in df.columns:
            return None

    df = df[
        required
    ].copy()

    df = df.dropna()

    try:

        if df.index.tz is None:

            df.index = (
                df.index.tz_localize(
                    "UTC"
                )
            )

        else:

            df.index = (
                df.index.tz_convert(
                    "UTC"
                )
            )

    except Exception:
        pass

    return df


# ============================================================
# DOWNLOAD MARKET DATA
# ============================================================

def download_data():

    print("Downloading market data...")

    try:

        h1 = clean_dataframe(
            yf.download(
                TICKER,
                period="30d",
                interval="1h",
                progress=False,
                auto_adjust=False,
                threads=False
            )
        )

        m15 = clean_dataframe(
            yf.download(
                TICKER,
                period="10d",
                interval="15m",
                progress=False,
                auto_adjust=False,
                threads=False
            )
        )

        m5 = clean_dataframe(
            yf.download(
                TICKER,
                period="5d",
                interval="5m",
                progress=False,
                auto_adjust=False,
                threads=False
            )
        )

        m1 = clean_dataframe(
            yf.download(
                TICKER,
                period="5d",
                interval="1m",
                progress=False,
                auto_adjust=False,
                threads=False
            )
        )

        if (
            h1 is None
            or m15 is None
            or m5 is None
            or m1 is None
        ):

            print(
                "One or more datasets unavailable."
            )

            return None

        # Remove unfinished candle
        if len(h1) > 2:
            h1 = h1.iloc[:-1]

        if len(m15) > 2:
            m15 = m15.iloc[:-1]

        if len(m5) > 2:
            m5 = m5.iloc[:-1]

        if len(m1) > 2:
            m1 = m1.iloc[:-1]

        return h1, m15, m5, m1

    except Exception as e:

        print(
            "Download error:",
            e
        )

        return None


# ============================================================
# INDICATORS
# ============================================================

def add_indicators(df):

    df = df.copy()

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    open_price = df["Open"]

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    df["EMA20"] = close.ewm(
        span=20,
        adjust=False
    ).mean()

    df["EMA50"] = close.ewm(
        span=50,
        adjust=False
    ).mean()

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    delta = close.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    rs = (
        avg_gain
        / avg_loss.replace(
            0,
            np.nan
        )
    )

    df["RSI"] = (
        100
        - (
            100
            / (1 + rs)
        )
    )

    # --------------------------------------------------------
    # ATR
    # --------------------------------------------------------

    previous_close = close.shift(1)

    tr1 = high - low

    tr2 = (
        high
        - previous_close
    ).abs()

    tr3 = (
        low
        - previous_close
    ).abs()

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(axis=1)

    df["ATR"] = tr.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    # --------------------------------------------------------
    # ADX
    # --------------------------------------------------------

    up_move = high.diff()

    down_move = -low.diff()

    plus_dm = np.where(
        (
            up_move > down_move
        )
        & (
            up_move > 0
        ),
        up_move,
        0
    )

    minus_dm = np.where(
        (
            down_move > up_move
        )
        & (
            down_move > 0
        ),
        down_move,
        0
    )

    atr_for_adx = tr.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    plus_di = (
        100
        * pd.Series(
            plus_dm,
            index=df.index
        ).ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        / atr_for_adx
    )

    minus_di = (
        100
        * pd.Series(
            minus_dm,
            index=df.index
        ).ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        / atr_for_adx
    )

    denominator = (
        plus_di
        + minus_di
    ).replace(
        0,
        np.nan
    )

    dx = (
        100
        * (
            plus_di
            - minus_di
        ).abs()
        / denominator
    )

    df["ADX"] = dx.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    # --------------------------------------------------------
    # CANDLE STRUCTURE
    # --------------------------------------------------------

    candle_range = (
        high - low
    ).replace(
        0,
        np.nan
    )

    df["BodyRatio"] = (
        (
            close
            - open_price
        ).abs()
        / candle_range
    )

    df["ClosePosition"] = (
        (
            close
            - low
        )
        / candle_range
    )

    return df


# ============================================================
# FIND SIGNAL
# ============================================================

def find_signal(h1, m15, m5):

    h1 = add_indicators(h1)
    m15 = add_indicators(m15)
    m5 = add_indicators(m5)

    if (
        len(h1) < 60
        or len(m15) < 60
        or len(m5) < 50
    ):

        print(
            "Not enough indicator data."
        )

        return None

    # --------------------------------------------------------
    # H1
    # --------------------------------------------------------

    h = h1.iloc[-1]
    h_prev = h1.iloc[-2]

    # --------------------------------------------------------
    # 15M
    # --------------------------------------------------------

    p = m15.iloc[-2]
    c = m15.iloc[-1]

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    latest_5m = m5.iloc[-1]
    previous_5m = m5.iloc[-2]

    price = float(
        c["Close"]
    )

    ema20 = float(
        c["EMA20"]
    )

    ema50 = float(
        c["EMA50"]
    )

    rsi = float(
        c["RSI"]
    )

    adx = float(
        c["ADX"]
    )

    atr = float(
        c["ATR"]
    )

    # --------------------------------------------------------
    # Validate
    # --------------------------------------------------------

    values = [
        price,
        ema20,
        ema50,
        rsi,
        adx,
        atr
    ]

    if not all(
        np.isfinite(v)
        for v in values
    ):

        print(
            "Invalid indicator values."
        )

        return None

    if atr <= 0:

        print(
            "Invalid ATR."
        )

        return None

    # ========================================================
    # H1 TREND
    # ========================================================

    h1_bull = (
        h["Close"]
        > h["EMA20"]
        and
        h["EMA20"]
        > h["EMA50"]
        and
        h["EMA20"]
        >= h_prev["EMA20"]
    )

    h1_bear = (
        h["Close"]
        < h["EMA20"]
        and
        h["EMA20"]
        < h["EMA50"]
        and
        h["EMA20"]
        <= h_prev["EMA20"]
    )

    # ========================================================
    # ENTRY DISTANCE
    # ========================================================

    entry_distance = abs(
        price - ema20
    )

    if (
        entry_distance
        > atr * MAX_ENTRY_DISTANCE_ATR
    ):

        print(
            "Entry rejected: "
            "price too far from EMA20."
        )

        return None

    # ========================================================
    # LONG SCORE / 6
    # ========================================================

    long_score = 0

    # 1. Bullish candle
    if (
        c["Close"] > c["Open"]
        and
        c["BodyRatio"] >= 0.30
        and
        c["ClosePosition"] >= 0.55
    ):

        long_score += 1

    # 2. Break previous high
    if (
        c["Close"]
        > p["High"]
    ):

        long_score += 1

    # 3. RSI
    if 45 <= rsi <= 68:

        long_score += 1

    # 4. ADX
    if adx >= MIN_ADX:

        long_score += 1

    # 5. EMA structure
    if (
        c["Close"] > ema20
        and
        ema20 > ema50
    ):

        long_score += 1

    # 6. 5M confirmation
    five_min_long = (
        latest_5m["Close"]
        > latest_5m["Open"]
        and
        latest_5m["Close"]
        >= latest_5m["EMA20"]
        and
        latest_5m["EMA20"]
        >= previous_5m["EMA20"]
    )

    if five_min_long:

        long_score += 1

    # ========================================================
    # SHORT SCORE / 6
    # ========================================================

    short_score = 0

    # 1. Bearish candle
    if (
        c["Close"] < c["Open"]
        and
        c["BodyRatio"] >= 0.30
        and
        c["ClosePosition"] <= 0.45
    ):

        short_score += 1

    # 2. Break previous low
    if (
        c["Close"]
        < p["Low"]
    ):

        short_score += 1

    # 3. RSI
    if 32 <= rsi <= 52:

        short_score += 1

    # 4. ADX
    if adx >= MIN_ADX:

        short_score += 1

    # 5. EMA structure
    if (
        c["Close"] < ema20
        and
        ema20 < ema50
    ):

        short_score += 1

    # 6. 5M confirmation
    five_min_short = (
        latest_5m["Close"]
        < latest_5m["Open"]
        and
        latest_5m["Close"]
        <= latest_5m["EMA20"]
        and
        latest_5m["EMA20"]
        <= previous_5m["EMA20"]
    )

    if five_min_short:

        short_score += 1

    # ========================================================
    # DIAGNOSTICS
    # ========================================================

    print("")
    print("====================================")
    print(" CURRENT MARKET CHECK")
    print("====================================")

    print(
        f"Price : ${price:,.2f}"
    )

    print(
        f"EMA20 : ${ema20:,.2f}"
    )

    print(
        f"EMA50 : ${ema50:,.2f}"
    )

    print(
        f"RSI   : {rsi:.2f}"
    )

    print(
        f"ADX   : {adx:.2f}"
    )

    print(
        f"ATR   : {atr:.2f}"
    )

    print("")
    print("====================================")
    print(" LONG CHECK")
    print("====================================")

    print(
        f"1H Trend : "
        f"{'PASS' if h1_bull else 'FAIL'}"
    )

    print(
        f"Score    : "
        f"{long_score}/6"
    )

    print(
        f"5M Confirm : "
        f"{'PASS' if five_min_long else 'FAIL'}"
    )

    print("")
    print("====================================")
    print(" SHORT CHECK")
    print("====================================")

    print(
        f"1H Trend : "
        f"{'PASS' if h1_bear else 'FAIL'}"
    )

    print(
        f"Score    : "
        f"{short_score}/6"
    )

    print(
        f"5M Confirm : "
        f"{'PASS' if five_min_short else 'FAIL'}"
    )

    # ========================================================
    # FINAL SIGNAL CONDITION
    # ========================================================

    long_condition = (
        h1_bull
        and
        long_score >= MIN_SIGNAL_SCORE
        and
        five_min_long
    )

    short_condition = (
        h1_bear
        and
        short_score >= MIN_SIGNAL_SCORE
        and
        five_min_short
    )

    if (
        not long_condition
        and
        not short_condition
    ):

        print("")
        print(
            "No valid signal found."
        )

        return None

    # ========================================================
    # DIRECTION
    # ========================================================

    if (
        long_condition
        and
        long_score >= short_score
    ):

        direction = "LONG"

        entry = price

        swing_low = float(
            m15["Low"]
            .iloc[-4:-1]
            .min()
        )

        sl = (
            swing_low
            - atr * 0.35
        )

        reason = (
            f"1시간 상승 추세 + "
            f"15분 점수 {long_score}/6 + "
            f"5분 상승 확인 "
            f"(RSI: {rsi:.1f}, "
            f"ADX: {adx:.1f})"
        )

    else:

        direction = "SHORT"

        entry = price

        swing_high = float(
            m15["High"]
            .iloc[-4:-1]
            .max()
        )

        sl = (
            swing_high
            + atr * 0.35
        )

        reason = (
            f"1시간 하락 추세 + "
            f"15분 점수 {short_score}/6 + "
            f"5분 하락 확인 "
            f"(RSI: {rsi:.1f}, "
            f"ADX: {adx:.1f})"
        )

    # ========================================================
    # RISK
    # ========================================================

    risk = abs(
        entry - sl
    )

    min_risk = (
        atr * MIN_SL_ATR
    )

    max_risk = (
        atr * MAX_SL_ATR
    )

    if risk < min_risk:

        print("")
        print(
            f"Signal rejected: "
            f"SL too close "
            f"({risk:.2f} < "
            f"{min_risk:.2f})"
        )

        return None

    if risk > max_risk:

        print("")
        print(
            f"Signal rejected: "
            f"SL too far "
            f"({risk:.2f} > "
            f"{max_risk:.2f})"
        )

        return None

    # ========================================================
    # 5M PRICE DISTANCE
    # ========================================================

    five_min_price = float(
        latest_5m["Close"]
    )

    if (
        abs(
            five_min_price
            - entry
        )
        > atr * MAX_SIGNAL_MOVE_ATR
    ):

        print("")
        print(
            "Signal rejected: "
            "5M price moved too far."
        )

        return None

    # ========================================================
    # TARGETS
    # ========================================================

    if direction == "LONG":

        tp1 = (
            entry
            + risk * TP1_R
        )

        tp2 = (
            entry
            + risk * TP2_R
        )

        tp3 = (
            entry
            + risk * TP3_R
        )

    else:

        tp1 = (
            entry
            - risk * TP1_R
        )

        tp2 = (
            entry
            - risk * TP2_R
        )

        tp3 = (
            entry
            - risk * TP3_R
        )

    # ========================================================
    # SIGNAL TIME
    # ========================================================

    signal_time = normalize_timestamp(
        m15.index[-1]
    )

    entry_time = (
        signal_time
        + pd.Timedelta(
            minutes=15
        )
    )

    # ========================================================
    # SIGNAL OBJECT
    # ========================================================

    signal = {

        "status": "active",

        "direction": direction,

        "entry": float(entry),

        "sl": float(sl),

        "initial_sl": float(sl),

        "tp1": float(tp1),

        "tp2": float(tp2),

        "tp3": float(tp3),

        "risk": float(risk),

        "rsi": float(rsi),

        "adx": float(adx),

        "atr": float(atr),

        "long_score": int(
            long_score
        ),

        "short_score": int(
            short_score
        ),

        "reason": reason,

        "signal_time": (
            signal_time.isoformat()
        ),

        "entry_time": (
            entry_time.isoformat()
        ),

        "tp1_hit": False,

        "tp2_hit": False,

        "tp3_hit": False,

        "entry_alert_sent": False,

        "created_at": (
            pd.Timestamp.now(
                tz="UTC"
            ).isoformat()
        )
    }

    print("")
    print("====================================")
    print(" SIGNAL CREATED SUCCESSFULLY")
    print("====================================")

    print(
        f"Direction : {direction}"
    )

    print(
        f"Entry     : ${entry:,.2f}"
    )

    print(
        f"SL        : ${sl:,.2f}"
    )

    print(
        f"TP1       : ${tp1:,.2f}"
    )

    print(
        f"TP2       : ${tp2:,.2f}"
    )

    print(
        f"TP3       : ${tp3:,.2f}"
    )

    print(
        f"Risk      : ${risk:,.2f}"
    )

    return signal


# ============================================================
# ENTRY TELEGRAM MESSAGE
# ============================================================

def format_entry_message(signal):

    direction = signal["direction"]

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    title = (
        "롱(매수) 포지션 시그널"
        if direction == "LONG"
        else "숏(매도) 포지션 시그널"
    )

    score = max(
        signal["long_score"],
        signal["short_score"]
    )

    kst_time = format_kst_time(
        signal["signal_time"]
    )

    return f"""
👑 <b>골드 선물 스마트 시그널</b>

{emoji} <b>{title}</b>

━━━━━━━━━━━━━━━━━━

💵 <b>진입가격</b>
<code>${signal['entry']:,.2f}</code>

🛡️ <b>손절가격 (SL)</b>
<code>${signal['sl']:,.2f}</code>

🎯 <b>1차 목표가 (TP1)</b>
<code>${signal['tp1']:,.2f}</code>

🎯 <b>2차 목표가 (TP2)</b>
<code>${signal['tp2']:,.2f}</code>

🎯 <b>3차 목표가 (TP3)</b>
<code>${signal['tp3']:,.2f}</code>

━━━━━━━━━━━━━━━━━━

📊 <b>시장 지표</b>

• RSI : <code>{signal['rsi']:.2f}</code>
• ADX : <code>{signal['adx']:.2f}</code>
• ATR : <code>{signal['atr']:.2f}</code>
• 스마트 점수 : <code>{score}/6</code>
• 위험 폭 : <code>${signal['risk']:.2f}</code>

━━━━━━━━━━━━━━━━━━

🧠 <b>진입 근거</b>

{signal['reason']}

━━━━━━━━━━━━━━━━━━

🕐 <b>발생 시간 (KST)</b>

<code>{kst_time}</code>

📊 <a href="https://www.tradingview.com/symbols/GC1!/">트레이딩뷰 골드 차트 보기</a>
"""


# ============================================================
# POSITION MONITORING
# ============================================================

def monitor_position(state, m1):

    if (
        not state
        or state.get("status")
        != "active"
    ):

        return state

    direction = state["direction"]

    entry = float(
        state["entry"]
    )

    sl = float(
        state["sl"]
    )

    tp1 = float(
        state["tp1"]
    )

    tp2 = float(
        state["tp2"]
    )

    tp3 = float(
        state["tp3"]
    )

    entry_time = normalize_timestamp(
        state["entry_time"]
    )

    data = m1[
        m1.index >= entry_time
    ].copy()

    if data.empty:

        return state

    for idx, row in data.iterrows():

        high = float(
            row["High"]
        )

        low = float(
            row["Low"]
        )

        candle_time = (
            normalize_timestamp(idx)
        )

        kst_time = format_kst_time(
            candle_time.isoformat()
        )

        # ====================================================
        # LONG
        # ====================================================

        if direction == "LONG":

            # ------------------------------------------------
            # SL
            # ------------------------------------------------

            if low <= sl:

                send_telegram(
                    f"""🛑 <b>롱 포지션 손절가 도달 (SL)</b>

진입가:
<code>${entry:,.2f}</code>

손절가:
<code>${sl:,.2f}</code>

시간:
<code>{kst_time}</code>"""
                )

                log_event(
                    "LONG_SL",
                    state
                )

                state["status"] = (
                    "cooldown"
                )

                state[
                    "cooldown_until"
                ] = (
                    pd.Timestamp.now(
                        tz="UTC"
                    )
                    + pd.Timedelta(
                        minutes=COOLDOWN_MINUTES
                    )
                ).isoformat()

                save_state(state)

                return state

            # ------------------------------------------------
            # TP1
            # ------------------------------------------------

            if (
                not state["tp1_hit"]
                and high >= tp1
            ):

                send_telegram(
                    f"""🎯 <b>1차 목표가 도달 (TP1)</b>

1차 TP:
<code>${tp1:,.2f}</code>

진입가:
<code>${entry:,.2f}</code>"""
                )

                state["tp1_hit"] = True

                log_event(
                    "LONG_TP1",
                    state
                )

                save_state(state)

            # ------------------------------------------------
            # TP2
            # ------------------------------------------------

            if (
                not state["tp2_hit"]
                and high >= tp2
            ):

                send_telegram(
                    f"""🎯 <b>2차 목표가 도달 (TP2)</b>

2차 TP:
<code>${tp2:,.2f}</code>

🔒 손절가가 본절가로 이동되었습니다.

본절가:
<code>${entry:,.2f}</code>"""
                )

                state["tp2_hit"] = True

                state["sl"] = entry

                log_event(
                    "LONG_TP2",
                    state
                )

                save_state(state)

            # ------------------------------------------------
            # TP3
            # ------------------------------------------------

            if (
                not state["tp3_hit"]
                and high >= tp3
            ):

                send_telegram(
                    f"""🏆 <b>3차 목표가 도달 (TP3)</b>

3차 TP:
<code>${tp3:,.2f}</code>

포지션이 성공적으로 완료되었습니다. 🔥"""
                )

                state["tp3_hit"] = True

                state["status"] = (
                    "cooldown"
                )

                state[
                    "cooldown_until"
                ] = (
                    candle_time
                    + pd.Timedelta(
                        minutes=COOLDOWN_MINUTES
                    )
                ).isoformat()

                log_event(
                    "LONG_TP3",
                    state
                )

                save_state(state)

                return state

        # ====================================================
        # SHORT
        # ====================================================

        else:

            # ------------------------------------------------
            # SL
            # ------------------------------------------------

            if high >= sl:

                send_telegram(
                    f"""🛑 <b>숏 포지션 손절가 도달 (SL)</b>

진입가:
<code>${entry:,.2f}</code>

손절가:
<code>${sl:,.2f}</code>

시간:
<code>{kst_time}</code>"""
                )

                log_event(
                    "SHORT_SL",
                    state
                )

                state["status"] = (
                    "cooldown"
                )

                state[
                    "cooldown_until"
                ] = (
                    pd.Timestamp.now(
                        tz="UTC"
                    )
                    + pd.Timedelta(
                        minutes=COOLDOWN_MINUTES
                    )
                ).isoformat()

                save_state(state)

                return state

            # ------------------------------------------------
            # TP1
            # ------------------------------------------------

            if (
                not state["tp1_hit"]
                and low <= tp1
            ):

                send_telegram(
                    f"""🎯 <b>1차 목표가 도달 (TP1)</b>

1차 TP:
<code>${tp1:,.2f}</code>

진입가:
<code>${entry:,.2f}</code>"""
                )

                state["tp1_hit"] = True

                log_event(
                    "SHORT_TP1",
                    state
                )

                save_state(state)

            # ------------------------------------------------
            # TP2
            # ------------------------------------------------

            if (
                not state["tp2_hit"]
                and low <= tp2
            ):

                send_telegram(
                    f"""🎯 <b>2차 목표가 도달 (TP2)</b>

2차 TP:
<code>${tp2:,.2f}</code>

🔒 손절가가 본절가로 이동되었습니다.

본절가:
<code>${entry:,.2f}</code>"""
                )

                state["tp2_hit"] = True

                state["sl"] = entry

                log_event(
                    "SHORT_TP2",
                    state
                )

                save_state(state)

            # ------------------------------------------------
            # TP3
            # ------------------------------------------------

            if (
                not state["tp3_hit"]
                and low <= tp3
            ):

                send_telegram(
                    f"""🏆 <b>3차 목표가 도달 (TP3)</b>

3차 TP:
<code>${tp3:,.2f}</code>

포지션이 성공적으로 완료되었습니다. 🔥"""
                )

                state["tp3_hit"] = True

                state["status"] = (
                    "cooldown"
                )

                state[
                    "cooldown_until"
                ] = (
                    candle_time
                    + pd.Timedelta(
                        minutes=COOLDOWN_MINUTES
                    )
                ).isoformat()

                log_event(
                    "SHORT_TP3",
                    state
                )

                save_state(state)

                return state

    save_state(state)

    return state


# ============================================================
# MAIN
# ============================================================

def main():

    print("====================================")
    print(" GOLD FUTURES SMART SIGNAL BOT")
    print(" BALANCED FINAL VERSION")
    print("====================================")

    # --------------------------------------------------------
    # Load state
    # --------------------------------------------------------

    state = load_state()

    # --------------------------------------------------------
    # Download data
    # --------------------------------------------------------

    data = download_data()

    if data is None:

        print(
            "Market data unavailable."
        )

        return

    h1, m15, m5, m1 = data

    # ========================================================
    # ACTIVE POSITION
    # ========================================================

    if state.get("status") == "active":

        print(
            "\nActive position found."
        )

        print(
            "Monitoring position..."
        )

        monitor_position(
            state,
            m1
        )

        return

    # ========================================================
    # COOLDOWN
    # ========================================================

    if state.get("status") == "cooldown":

        cooldown_until = state.get(
            "cooldown_until"
        )

        if cooldown_until:

            now = pd.Timestamp.now(
                tz="UTC"
            )

            cooldown_time = (
                normalize_timestamp(
                    cooldown_until
                )
            )

            if now < cooldown_time:

                print(
                    "\nCooldown active."
                )

                print(
                    f"Until: "
                    f"{cooldown_time}"
                )

                return

        print(
            "\nCooldown finished."
        )

        save_state({})

    # ========================================================
    # FIND NEW SIGNAL
    # ========================================================

    signal = find_signal(
        h1,
        m15,
        m5
    )

    if signal is None:

        return

    # ========================================================
    # DUPLICATE CANDLE PROTECTION
    # ========================================================

    existing_state = load_state()

    last_signal_time = (
        existing_state.get(
            "last_signal_time"
        )
    )

    if (
        last_signal_time
        == signal["signal_time"]
    ):

        print(
            "Duplicate signal candle."
        )

        return

    signal[
        "last_signal_time"
    ] = signal["signal_time"]

    # ========================================================
    # SAVE STATE
    # ========================================================

    save_state(signal)

    log_event(
        "NEW_SIGNAL",
        signal
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    message = format_entry_message(
        signal
    )

    sent = send_telegram(
        message
    )

    if sent:

        signal[
            "entry_alert_sent"
        ] = True

        save_state(signal)

    # ========================================================
    # DONE
    # ========================================================

    print("")
    print("====================================")
    print(" SIGNAL CREATED SUCCESSFULLY")
    print("====================================")


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
