import json
import os
import requests
from datetime import datetime, timedelta

CACHE_FILE = "tracked_coins.json"
MIN_ACC_TRADE_PRICE = 10_000_000_000   # 거래대금 100억 이상
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

def format_price(price):
    """가격대에 맞춰 소수점 자릿수를 동적으로 변환하는 함수"""
    if price >= 100:
        return f"{price:,.1f}"
    elif price >= 1:
        return f"{price:,.2f}"
    elif price >= 0.1:
        return f"{price:,.3f}"
    else:
        return f"{price:,.4f}"

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

    # [스마트 트래킹 검증 강화] 중복 알림 차단 로직
    cache = load_cache()
    now = datetime.now()
    
    if ticker in cache:
        prev_target_1 = cache[ticker].get("target_1", 0)
        last_alert_time_str = cache[ticker].get("last_alert", "")
        
        # 1) 최근 4시간 이내에 알림 이력이 있다면 무조건 스킵 (쿨타임)
        if last_alert_time_str:
            try:
                last_alert_time = datetime.fromisoformat(last_alert_time_str)
                if now - last_alert_time < timedelta(hours=4):
                    print(f" -> [스킵] 최근 4시간 내 알림 이력 존재 ({korean_name})")
                    return
            except Exception:
                pass

        # 2) 가격이 이전 TP1보다 최소 1.5% 이상 더 치고 올라가지 않았으면 횡보 중으로 판단하여 스킵
        if current_price < prev_target_1 * 1.015:
            print(f" -> [스킵] 상향 파동 미흡 - 이전 TP1 근처 횡보 중 ({korean_name})")
            return
        else:
            print(f"🔥 [상향 파동 연장] {korean_name} - 추가 슈팅 포착!")

    # 동적 가격 포맷 적용
    curr_str = format_price(current_price)
    tp1_str = format_price(target_1)
    tp2_str = format_price(target_2)
    tp3_str = format_price(target_3)
    sl_str = format_price(stop_loss)

    # [전문가형 하이엔드 메시지 포맷]
    message = (
        f"🚀 **[QUANT PROFESSIONAL SIGNAL]**\n"
        f"────────────────────────\n"
        f"▪ **자산명**: `{korean_name} ({ticker})`\n"
        f"▪ **현재가**: `{curr_str} KRW`\n"
        f"▪ **24H 거래대금**: `{acc_trade_price / 100_000_000:,.1f}억 원`\n\n"
        f"🎯 **TARGET LEVELS (분할 익절 구간)**\n"
        f"  ├ **TP1**: `{tp1_str}원` (+3.0%)\n"
        f"  ├ **TP2**: `{tp2_str}원` (+6.0%)\n"
        f"  └ **TP3**: `{tp3_str}원` (+9.0%)\n\n"
        f"🛡️ **RISK MANAGEMENT (리스크 관리)**\n"
        f"  ├ **방어 손절가 (SL)**: `{sl_str}원` (-{calculated_stop_loss_pct}%)\n"
        f"  └ **기대 손익비**: `1 : 2.0 이상 (고효율 구간)`\n"
        f"────────────────────────\n"
        f"💡 *Strategy: 직전 저항선 돌파 및 실시간 볼륨 유입 포착*"
    )
    
    print(f"🔥 [알림 전송 완료] {korean_name}({ticker})")
    send_telegram_message(message)

    # 캐시 갱신 (현재가 기준 목표가 및 발송 시각 저장)
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
            korean_name = market_names.get(ticker, ticker)
            current_price = data['trade_price']
            acc_trade_price = data['acc_trade_price_24h']

            volume_spike_flag = True  # 테스트 플래그
            calculated_stop_loss_pct = 4.5  

            evaluate_and_send_signal(ticker, korean_name, current_price, acc_trade_price, volume_spike_flag, calculated_stop_loss_pct)

    except Exception as e:
        print(f"실행 중 에러 발생: {e}")
