import os
import json
from datetime import datetime
import requests
import yfinance as yf
import pandas as pd
import numpy as np

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "signal_state.json"

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    response = requests.post(url, json=payload)
    return response.json()

def calculate_indicators(df):
    # 볼린저 밴드 (20, 2)
    df['sma20'] = df['Close'].rolling(window=20).mean()
    df['std'] = df['Close'].rolling(window=20).std()
    df['bb_upper'] = df['sma20'] + (df['std'] * 2)
    df['bb_lower'] = df['sma20'] - (df['std'] * 2)

    # RSI (14)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))

    return df

def get_market_data():
    try:
        ticker = yf.Ticker("GC=F")
        
        # 15분봉 데이터 (단기 진입 및 스윙 고저점용)
        df_15m = ticker.history(period="5d", interval="15m")
        # 1시간봉 데이터 (중기 추세용)
        df_1h = ticker.history(period="10d", interval="1h")
        # 4시간봉 데이터 (장기 추세용)
        df_4h = ticker.history(period="30d", interval="4h")

        if len(df_15m) < 30 or len(df_1h) < 30 or len(df_4h) < 30:
            return None

        df_15m = calculate_indicators(df_15m)
        df_1h = calculate_indicators(df_1h)
        df_4h = calculate_indicators(df_4h)

        return df_15m, df_1h, df_4h
    except Exception as e:
        print(f"시장 데이터 조회 실패: {e}")
        return None

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return None

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=4)

def clear_state():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)

def main():
    data = get_market_data()
    if not data:
        print("데이터를 충분히 불러오지 못했습니다.")
        return

    df_15m, df_1h, df_4h = data
    
    current_price = df_15m.iloc[-1]['Close']
    low_price = df_15m.iloc[-1]['Low']
    high_price = df_15m.iloc[-1]['High']
    
    state = load_state()
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    chart_link = "[TradingView 차트 보기 (GCZ2026)](https://www.tradingview.com/chart/?symbol=GCZ2026)"

    # =========================================================================
    # [1] 기존 포지션 모니터링 (스윙 고저점 손절 관리)
    # =========================================================================
    if state:
        pos_type = state["type"]
        entry = state["entry"]
        tp1 = state["tp1"]
        tp2 = state["tp2"]
        tp3 = state["tp3"]
        sl = state["sl"]

        tp1_sent = state.get("tp1_sent", False)
        tp2_sent = state.get("tp2_sent", False)
        tp3_sent = state.get("tp3_sent", False)
        sl_sent = state.get("sl_sent", False)

        if pos_type == "LONG":
            if low_price <= sl and not sl_sent:
                send_telegram(f"🛑 **[골드 선물 손절가 도달 (SL)]**\n\n• 진입가: `${entry:,.2f}`\n• 도달 저가: `${low_price:,.2f}`\n❌ **구조적 손절가 터치. 포지션 종료.**\n\n🔗 {chart_link}")
                clear_state()
                return
            if high_price >= tp3 and not tp3_sent:
                send_telegram(f"🎯 **[골드 선물 3차 목표가 도달 (TP3)]**\n\n• 진입가: `${entry:,.2f}`\n🔥 **TP3 최종 익절 달성!** 🚀\n\n🔗 {chart_link}")
                clear_state()
                return
            if high_price >= tp2 and not tp2_sent:
                send_telegram(f"🎯 **[골드 선물 2차 목표가 도달 (TP2)]**\n\n• 진입가: `${entry:,.2f}`\n✨ **본절가로 스탑로스(SL) 이동 추천!**\n\n🔗 {chart_link}")
                state["tp2_sent"] = True
                save_state(state)
            if high_price >= tp1 and not tp1_sent:
                send_telegram(f"🎯 **[골드 선물 1차 목표가 도달 (TP1)]**\n\n• 진입가: `${entry:,.2f}`\n📈 **TP1 도달 (일부 익절)**\n\n🔗 {chart_link}")
                state["tp1_sent"] = True
                save_state(state)

        elif pos_type == "SHORT":
            if high_price >= sl and not sl_sent:
                send_telegram(f"🛑 **[골드 선물 손절가 도달 (SL)]**\n\n• 진입가: `${entry:,.2f}`\n• 도달 고가: `${high_price:,.2f}`\n❌ **구조적 손절가 터치. 포지션 종료.**\n\n🔗 {chart_link}")
                clear_state()
                return
            if low_price <= tp3 and not tp3_sent:
                send_telegram(f"🎯 **[골드 선물 3차 목표가 도달 (TP3)]**\n\n• 진입가: `${entry:,.2f}`\n🔥 **TP3 최종 익절 달성!** 🚀\n\n🔗 {chart_link}")
                clear_state()
                return
            if low_price <= tp2 and not tp2_sent:
                send_telegram(f"🎯 **[골드 선물 2차 목표가 도달 (TP2)]**\n\n• 진입가: `${entry:,.2f}`\n✨ **본절가로 스탑로스(SL) 이동 추천!**\n\n🔗 {chart_link}")
                state["tp2_sent"] = True
                save_state(state)
            if low_price <= tp1 and not tp1_sent:
                send_telegram(f"🎯 **[골드 선물 1차 목표가 도달 (TP1)]**\n\n• 진입가: `${entry:,.2f}`\n📈 **TP1 도달 (일부 익절)**\n\n🔗 {chart_link}")
                state["tp1_sent"] = True
                save_state(state)
        return

    # =========================================================================
    # [2] 멀티 타임프레임 + 볼밴/RSI + 스윙 고저점 신규 진입 필터
    # =========================================================================
    last_15m = df_15m.iloc[-1]
    last_1h = df_1h.iloc[-1]
    last_4h = df_4h.iloc[-1]

    # 다중 타임프레임 추세 판단 (1시간봉 & 4시간봉 이평선 기준)
    is_1h_bullish = last_1h['Close'] > last_1h['sma20']
    is_4h_bullish = last_4h['Close'] > last_4h['sma20']
    is_1h_bearish = last_1h['Close'] < last_1h['sma20']
    is_4h_bearish = last_4h['Close'] < last_4h['sma20']

    # 15분봉 볼린저 밴드 및 RSI 조건
    rsi_15m = last_15m['rsi']
    bb_lower_15m = last_15m['bb_lower']
    bb_upper_15m = last_15m['bb_upper']

    # 최근 5개 봉 기준 스윙 저점(최저가) 및 스윙 고점(최고가) 계산 (안전한 구조적 손절용)
    swing_low = df_15m['Low'].iloc[-5:].min() - 1.5
    swing_high = df_15m['High'].iloc[-5:].max() + 1.5

    pos_type = ""
    reason = ""

    # 롱 조건: 상위 타임프레임(1H, 4H)이 모두 상승세이고, 15분봉에서 하단 밴드 터치 후 RSI가 과매도(40 이하)에서 반등할 때
    if is_1h_bullish and is_4h_bullish and (current_price <= bb_lower_15m or rsi_15m < 40):
        pos_type = "LONG"
        action_text = "🟢 **멀티타임프레임 롱 포지션 (매수 진입)**"
        sl = round(swing_low, 2)
        risk = current_price - sl
        tp1 = round(current_price + (risk * 1.5), 2)
        tp2 = round(current_price + (risk * 2.5), 2)
        tp3 = round(current_price + (risk * 4.0), 2)
        reason = "상위 타임프레임(1H/4H) 상승 정렬 및 15분봉 볼밴 하단/RSI 반등 타점 포착"

    # 숏 조건: 상위 타임프레임(1H, 4H)이 모두 하락세이고, 15분봉에서 상단 밴드 터치 후 RSI가 과매수(60 이상)에서 꺾일 때
    elif is_1h_bearish and is_4h_bearish and (current_price >= bb_upper_15m or rsi_15m > 60):
        pos_type = "SHORT"
        action_text = "🔴 **멀티타임프레임 숏 포지션 (매도 진입)**"
        sl = round(swing_high, 2)
        risk = sl - current_price
        tp1 = round(current_price - (risk * 1.5), 2)
        tp2 = round(current_price - (risk * 2.5), 2)
        tp3 = round(current_price - (risk * 4.0), 2)
        reason = "상위 타임프레임(1H/4H) 하락 정렬 및 15분봉 볼밴 상단/RSI 저항 타점 포착"
    else:
        print("조건에 부합하는 명확한 멀티타임프레임 타점이 없어 진입을 대기합니다.")
        return

    new_state = {
        "type": pos_type,
        "entry": current_price,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "sl": sl,
        "tp1_sent": False,
        "tp2_sent": False,
        "tp3_sent": False,
        "sl_sent": False
    }
    save_state(new_state)

    message = (
        f"👑 **[골드 선물 멀티타임프레임 분석 시그널]** 👑\n"
        f"────────────────────────\n"
        f"⏱ **발행 시간**: `{current_time}`\n"
        f"📊 **분석 기준**: `15분봉 + 1시간봉 + 4시간봉`\n"
        f"📈 **매매 방향**: {action_text}\n"
        f"💰 **추천 진입가**: `${current_price:,.2f}`\n\n"
        f"🎯 **목표가 설정 (TP)**\n"
        f"• **1차 목표 (TP1)**: `${tp1:,.2f}`\n"
        f"• **2차 목표 (TP2)**: `${tp2:,.2f}`\n"
        f"• **3차 목표 (TP3)**: `${tp3:,.2f}`\n\n"
        f"🛡 **구조적 리스크 관리 (스윙 고저점)**\n"
        f"• **손절가 (SL)**: `${sl:,.2f}`\n\n"
        f"📈 **시장 구조 및 진입 근거**\n"
        f"_{reason}_\n"
        f"────────────────────────\n"
        f"🔗 {chart_link}\n"
        f"⚡ *MTF 볼린저밴드 & RSI 정밀 필터 가동 중*"
    )
    
    send_telegram(message)

if __name__ == "__main__":
    main()
