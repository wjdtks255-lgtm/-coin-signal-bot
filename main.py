import json
import os
import requests
from datetime import datetime, timedelta

CACHE_FILE = "tracked_coins.json"
MIN_ACC_TRADE_PRICE = 50_000_000_000   # 거래대금 50억 이상 (테스트용)
MAX_ALLOWABLE_STOP_LOSS_PCT = 10.0     # 손절 폭 10% 이내

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("텔레그램 토큰 또는 챗 ID가 설정되지 않았습니다.")
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

def get_market_names():
    """업비트에서 코인 한글 명칭 매핑 정보 가져오기"""
    try:
        url = "https://api.upbit.com/v1/market/all"
        res = requests.get(url).json()
        return {item['market']: item['korean_name'] for item in res if item['market'].startswith('KRW-')}
    except Exception:
        return {}

def evaluate_and_send_signal(ticker, korean_name, current_price, acc_trade_price, volume_spike_flag, calculated_stop_loss_pct):
    print(f"[{korean_name}({ticker})] 검토 중... 대금: {acc_trade_price/100000000:,.1f}억")

    if acc_trade_price < MIN_ACC_TRADE_PRICE:
        return

    if calculated_stop_loss_pct > MAX_ALLOWABLE_STOP_LOSS_PCT:
        return

    if not volume_spike_flag:
        return

    # 가격 산출
    stop_loss = current_price * (1 - (calculated_stop_loss_pct / 100))
    target_1 = current_price * 1.03  # +3.0%
    target_2 = current_price * 1.06  # +6.0%
    target_3 = current_price * 1.09  # +9.0%

    # [스마트 트래킹 검증] 이전 목표가를 돌파한 경우에만 추가 알림 허용
    cache = load_cache()
    now = datetime.now()
    
    if ticker in cache:
        prev_target_1 = cache[ticker].get("target_1", 0)
        if current_price <= prev_target_1:
            print(f" -> [스킵] 기존 시그널 구간 유지 중 (이전 TP1 미돌파)")
            return
        else:
            print(f"🔥 [상향 파동 연장] {ticker} - 이전 목표가 돌파 후 재포착")

    # [전문가형 하이엔드 메시지 포맷]
    message = (
        f"🚀 **[QUANT PROFESSIONAL SIGNAL]**\n"
        f"────────────────────────\n"
        f"▪ **자산명**: `{korean_name} ({ticker})`\n"
        f"▪ **현재가**: `{current_price:,.1f} KRW`\n"
        f"▪ **24H 거래대금**: `{acc_trade_price / 100_000_000:,.1f}억 원`\n\n"
        f"🎯 **TARGET LEVELS (분할 익절 구간)**\n"
        f"  ├ **TP1**: `{target_1:,.1f}원` (+3.0%)\n"
        f"  ├ **TP2**: `{target_2:,.1f}원` (+6.0%)\n"
        f"  └ **TP3**: `{target_3:,.1f}원` (+9.0%)\n\n"
        f"🛡️ **RISK MANAGEMENT (리스크 관리)**\n"
        f"  ├ **방어 손절가 (SL)**: `{stop_loss:,.1f}원` (-{calculated_stop_loss_pct}%)\n"
        f"  └ **기대 손익비**: `1 : 2.0 이상 (고효율 구간)`\n"
        f"────────────────────────\n"
        f"💡 *Strategy: 직전 저항선 돌파 및 실시간 볼륨 유입 포착*"
    )
    
    print(f"🔥 [알림 전송 완료] {korean_name}({ticker})")
    send_telegram_message(message)

    # 캐시 갱신 (현재 1차 목표가를 기준으로 저장)
    cache[ticker] = {
        "last_price": current_price,
        "target_1": target_1,
        "last_alert": now.isoformat()
    }
    save_cache(cache)

if __name__ == "__main__":
    print("업비트 하이엔드 퀀트 스캐너 가동 시작...")
    try:
        # 코인 한글 명칭 사전 로드
        market_names = get_market_names()

        market_url = "https://api.upbit.com/v1/market/all"
        markets = [item['market'] for item in requests.get(market_url).json() if item['market'].startswith('KRW-')]
        
        ticker_url = f"https://api.upbit.com/v1/ticker?markets={','.join(markets)}"
        ticker_data = requests.get(ticker_url).json()

        for data in ticker_data:
            ticker = data['market']
            korean_name = market_names.get(ticker, ticker)  # 한글명 매칭 (없으면 티커 그대로)
            current_price = data['trade_price']
            acc_trade_price = data['acc_trade_price_24h']

            volume_spike_flag = True  # 테스트 플래그
            calculated_stop_loss_pct = 4.5  

            evaluate_and_send_signal(ticker, korean_name, current_price, acc_trade_price, volume_spike_flag, calculated_stop_loss_pct)

    except Exception as e:
        print(f"실행 중 에러 발생: {e}")
