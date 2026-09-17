import json
import os
import requests
from datetime import datetime, timedelta

CACHE_FILE = "tracked_coins.json"
MIN_ACC_TRADE_PRICE = 50_000_000_000   # 거래대금 50억 이상 (테스트용)
MAX_ALLOWABLE_STOP_LOSS_PCT = 10.0     # 손절 폭 10% 이내
COOLDOWN_HOURS = 1                     # 쿨타임 1시간

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("텔레그램 토큰 또는챗 ID가 설정되지 않았습니다.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"텔레그램 전송 에러: {e}")

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=4)

def evaluate_and_send_signal(ticker, current_price, acc_trade_price, volume_spike_flag, calculated_stop_loss_pct):
    print(f"[{ticker}] 검토 중... 대금: {acc_trade_price/100000000:,.1f}억, 손절폭: {calculated_stop_loss_pct}%")

    if acc_trade_price < MIN_ACC_TRADE_PRICE:
        print(f" -> [스킵] 거래대금 부족")
        return

    if calculated_stop_loss_pct > MAX_ALLOWABLE_STOP_LOSS_PCT:
        print(f" -> [스킵] 손절 폭 초과")
        return

    if not volume_spike_flag:
        print(f" -> [스킵] 거래량 폭발 미충족")
        return

    cache = load_cache()
    now = datetime.now()
    if ticker in cache:
        last_alert_str = cache[ticker].get("last_alert")
        if last_alert_str:
            last_alert_time = datetime.fromisoformat(last_alert_str)
            if now - last_alert_time < timedelta(hours=COOLDOWN_HOURS):
                print(f" -> [스킵] 쿨타임 중")
                return

    stop_loss = current_price * (1 - (calculated_stop_loss_pct / 100))
    target_1 = current_price * 1.03
    target_2 = current_price * 1.06
    target_3 = current_price * 1.09

    message = (
        f"📊 **[QUANT SIGNAL] 현물 마켓 트렌드 포착**\n"
        f"────────────────────────\n"
        f"▪ **종목명**: `{ticker}`\n"
        f"▪ **현재가**: `{current_price:,.1f} KRW`\n"
        f"▪ **24H 거래대금**: `{acc_trade_price / 100_000_000:,.1f}억 원`\n\n"
        f"🎯 **TARGET (분할 목표가)**\n"
        f"  └ 1차 목표: `{target_1:,.1f}원` (+3.0%)\n"
        f"  └ 2차 목표: `{target_2:,.1f}원` (+6.0%)\n"
        f"  └ 3차 목표: `{target_3:,.1f}원` (+9.0%)\n\n"
        f"🛡️ **RISK MANAGEMENT (방어)**\n"
        f"  └ 타이트 손절가: `{stop_loss:,.1f}원` (-{calculated_stop_loss_pct}%)\n"
        f"────────────────────────\n"
        f"💡 *Notice: 필터 통과 실전 신호 발송*"
    )
    
    print(f"🔥 [알림 전송] {ticker}")
    send_telegram_message(message)

    cache[ticker] = {"last_alert": now.isoformat()}
    save_cache(cache)

# --- 업비트 시장 데이터 조회 및 메인 실행부 ---
if __name__ == "__main__":
    print("업비트 시장 데이터 스캔 시작...")
    try:
        # 1. 원화 마켓 코인 리스트 조회
        market_url = "https://api.upbit.com/v1/market/all"
        markets = [item['market'] for item in requests.get(market_url).json() if item['market'].startswith('KRW-')]
        
        # 2. 현재가 및 24시간 대금 조회
        ticker_url = f"https://api.upbit.com/v1/ticker?markets={','.join(markets)}"
        ticker_data = requests.get(ticker_url).json()

        for data in ticker_data:
            ticker = data['market']
            current_price = data['trade_price']
            acc_trade_price = data['acc_trade_price_24h']

            # 예시 테스트용 플래그 및 손절가 계산 (실제 사용하시던 15분봉 조건 로직이 있다면 이 자리에 연동됩니다)
            # 현재는 테스트를 위해 거래대금 50억 넘는 코인 중 임의 테스트 통과 조건 부여
            volume_spike_flag = True  
            calculated_stop_loss_pct = 4.5  

            evaluate_and_send_signal(ticker, current_price, acc_trade_price, volume_spike_flag, calculated_stop_loss_pct)

    except Exception as e:
        print(f"실행 중 에러 발생: {e}")
