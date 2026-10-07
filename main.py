# UPBIT SPOT QUANT SIGNAL BOT V8.2 (Reset & Multi-Scan)
import os, json, time, requests
from datetime import datetime, timezone, timedelta

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or "").strip()

STATE_FILE = "bot_state.json"
TRACKED_FILE = "tracked_coins.json"
KST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json"
}

# 실행할 때마다 이전 포지션을 강제로 초기화하고 전 종목을 새로 스캔합니다.
FORCE_RESET = True

def now():
    return datetime.now(KST)

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def telegram(text):
    if not TOKEN or not CHAT_ID:
        print("❌ 텔레그램 토큰 설정 미완료")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15
        )
        return r.status_code == 200
    except Exception as e:
        print("Telegram Error:", e)
        return False

def fmt(x):
    if x >= 1000: return f"{x:,.0f}"
    if x >= 100: return f"{x:,.1f}"
    if x >= 1: return f"{x:,.2f}"
    return f"{x:.4f}"

# 업비트 KRW 마켓 전체 목록 수집
def get_upbit_krw_markets():
    try:
        url = "https://api.upbit.com/v1/market/all"
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            markets = [m["market"] for m in res.json() if m["market"].startswith("KRW-")]
            print(f"📊 업비트 KRW 마켓 수집 성공: 총 {len(markets)}개 종목")
            return markets
    except Exception as e:
        print("Upbit Market Fetch Error:", e)
    return []

# 15분 캔들 데이터 수집 및 단순 지표 계산
def fetch_candles(market, count=60):
    try:
        url = f"https://api.upbit.com/v1/candles/minutes/15?market={market}&count={count}"
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if len(data) >= 30:
                closes = [float(x["trade_price"]) for x in reversed(data)]
                highs = [float(x["high_price"]) for x in reversed(data)]
                lows = [float(x["low_price"]) for x in reversed(data)]
                volumes = [float(x["candle_acc_trade_volume"]) for x in reversed(data)]
                return closes, highs, lows, volumes
    except:
        pass
    return None, None, None, None

def calculate_rsi(closes, period=14):
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def main():
    print("=" * 55)
    print("🚀 UPBIT SPOT QUANT SCANNER V8.2 STARTED")
    print("=" * 55)

    # 1. 포지션 강제 초기화
    if FORCE_RESET:
        print("🔄 기존 보유 포지션 데이터 전체 초기화 진행...")
        save_json(STATE_FILE, {"positions": {}, "signals": {}})
        save_json(TRACKED_FILE, {})
        telegram(
            "🔄 <b>[코인 현물 봇] 기존 포지션 초기화 완료</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• 기존 12개 보유 포지션이 비워졌습니다.\n"
            "• 전 종목 신규 스캔을 시작합니다."
        )

    state = load_json(STATE_FILE, {"positions": {}, "signals": {}})
    markets = get_upbit_krw_markets()

    if not markets:
        print("❌ 업비트 종목 수집 실패로 스캔 중단")
        return

    detected = []

    for market in markets:
        symbol = market.replace("KRW-", "")
        closes, highs, lows, volumes = fetch_candles(market)
        
        if not closes:
            continue

        entry_price = closes[-1]
        rsi = calculate_rsi(closes)
        
        # 50일 이동평균선(SMA)
        sma50 = sum(closes[-30:]) / 30 if len(closes) >= 30 else entry_price

        # 매수 시그널 판단 (이평선 지지 + RSI 반등 조건)
        if entry_price > sma50 and 40 <= rsi <= 68:
            tp1 = entry_price * 1.02  # +2.0%
            tp2 = entry_price * 1.04  # +4.0%
            sl = entry_price * 0.98   # -2.0%

            tv_link = f"https://www.tradingview.com/chart/?symbol=UPBIT%3A{symbol}"

            detected.append({
                "symbol": symbol,
                "price": entry_price,
                "rsi": rsi,
                "tp1": tp1,
                "tp2": tp2,
                "sl": sl,
                "tv_link": tv_link
            })

    print(f"🎯 신규 포착된 시그널: {len(detected)}개")

    # 텔레그램 메시지 생성 및 발송
    if detected:
        for item in detected[:5]:  # 상위 5개 알림 전송
            msg = (
                f"🟢 <b>[코인 현물] 신규 매수 시그널</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>종목명</b>: #{item['symbol']}\n"
                f"• <b>진입가</b>: <code>{fmt(item['price'])} KRW</code>\n"
                f"• <b>RSI (14)</b>: <code>{item['rsi']:.1f}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 <b>1차 목표가 (TP1)</b>: <code>{fmt(item['tp1'])} KRW</code> (+2.0%)\n"
                f"🎯 <b>2차 목표가 (TP2)</b>: <code>{fmt(item['tp2'])} KRW</code> (+4.0%)\n"
                f"🛡 <b>손절가 (SL)</b>: <code>{fmt(item['sl'])} KRW</code> (-2.0%)\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📈 <a href=\"{item['tv_link']}\"><b>[트레이딩뷰 차트 열기]</b></a>\n"
                f"⏱ <code>{now().strftime('%H:%M:%S KST')}</code>"
            )
            telegram(msg)
            time.sleep(1)

        # 상태 업데이트
        new_positions = {x["symbol"]: {"entry": x["price"], "sl": x["sl"]} for x in detected[:5]}
        state["positions"] = new_positions
        save_json(STATE_FILE, state)

    print("✅ 스캔 및 모니터링 등록 완료!")

if __name__ == "__main__":
    main()
