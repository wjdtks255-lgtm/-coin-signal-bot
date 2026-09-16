import os
import requests
import numpy as np
import json
import time

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
CACHE_FILE = "tracked_coins.json"

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ 텔레그램 토큰 또는 챗 아이디가 설정되지 않았습니다!")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    res = requests.post(url, json=payload)
    print(f"텔레그램 전송 응답: {res.text}")

def format_price(price):
    if price < 10:
        return f"{price:.2f}원"
    elif price < 1000:
        return f"{price:.1f}원"
    else:
        return f"{price:,.0f}원"

def get_upbit_market_details():
    url = "https://api.upbit.com/v1/market/all"
    res = requests.get(url).json()
    market_dict = {}
    for item in res:
        if item['market'].startswith('KRW-') and item['market'] != 'KRW-BTC':
            market_dict[item['market']] = item['korean_name']
    return market_dict

def get_24h_trade_prices(markets):
    url = f"https://api.upbit.com/v1/ticker?markets={','.join(markets)}"
    try:
        res = requests.get(url).json()
        price_map = {}
        for item in res:
            price_map[item['market']] = item['acc_trade_price_24h']
        return price_map
    except:
        return {}

if __name__ == "__main__":
    print("🌐 [연결 테스트 및 강제 디버깅 스캐너] 가동 중...")
    
    market_dict = get_upbit_market_details()
    market_list = list(market_dict.keys())
    trade_prices_24h = get_24h_trade_prices(market_list)
    
    notifications = []

    # 테스트를 위해 현재 가장 상승률이 높고 거래대금이 터진 코인 딱 1개를 강제로 찾아봅니다.
    best_market = None
    best_name = ""
    max_change = -999
    best_data = {}

    for market, korean_name in market_dict.items():
        try:
            acc_trade_price = trade_prices_24h.get(market, 0)
            if acc_trade_price < 5000000000: # 50억 이상
                continue

            url = f"https://api.upbit.com/v1/candles/minutes/15?market={market}&count=5"
            res = requests.get(url).json()
            if len(res) < 2:
                continue
                
            current_price = res[0]['trade_price']
            prev_price = res[1]['trade_price']
            change_rate = ((current_price - prev_price) / prev_price) * 100
            
            if change_rate > max_change:
                max_change = change_rate
                best_market = market
                best_name = korean_name
                best_data = {
                    'price': current_price,
                    'change': change_rate,
                    'trade_price': acc_trade_price
                }
        except:
            pass

    # 조건에 맞는 종목이 없더라도 현재 가장 잘 나가는 코인 1개를 강제로 텔레그램으로 쏴서 테스트 완료 여부 확인
    if best_market:
        test_msg = (
            f"🧪 **[봇 정상작동 연결 테스트 알림]** 🧪\n\n"
            f"📌 **현재 최고 상승 종목**: `{best_name}` (`{best_market}`)\n"
            f"💰 **현재가**: `{format_price(best_data['price'])}` (`+{best_data['change']:.2f}%`)\n"
            f"💸 **24h 대금**: `{best_data['trade_price'] / 100_000_000:,.0f}억원`\n\n"
            f"✅ **상태**: 텔레그램 연동 및 깃허브 액션 스캔이 정상 작동 중입니다!"
        )
        notifications.append(test_msg)

    for msg in notifications:
        send_telegram(msg)

    print("테스트 스캔 완료.")
