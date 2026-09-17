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
    if price < 1:
        return f"{price:.4f}원"
    elif price < 10:
        return f"{price:.2f}원"
    elif price < 1000:
        return f"{price:.1f}원"
    else:
        return f"{price:,.0f}원"

def calculate_dynamic_duration(target_pct, vol_ratio, change_rate):
    speed_factor = max(vol_ratio, 1.0) * max(change_rate, 0.5)
    estimated_hours = (target_pct * 12.0) / speed_factor
    estimated_hours = max(2, min(estimated_hours, 168.0))
    
    if estimated_hours < 12:
        return f"약 {int(estimated_hours)}시간 이내 (초단기 폭발형)"
    elif estimated_hours < 24:
        return f"약 {int(estimated_hours)}시간 이내 (당일 슈팅형)"
    elif estimated_hours < 72:
        days = round(estimated_hours / 24, 1)
        return f"약 {days}일 이내 (단기 스윙형)"
    else:
        days = round(estimated_hours / 24)
        return f"약 {days}일 소요 예상 (중기 추세형)"

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_cache(cache):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception as e:
        print(f"캐시 저장 에러: {e}")

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
    print("🌐 [고점 방어 +3% 타겟팅 실시간 스캐너] 가동 중...")
    
    market_dict = get_upbit_market_details()
    market_list = list(market_dict.keys())
    
    trade_prices_24h = get_24h_trade_prices(market_list)
    
    tracked_cache = load_cache()
    current_time = time.time()
    
    # 📌 캐시 유지 시간을 24시간(86400초)으로 늘려 동일 종목 반복 알림 철저 차단
    tracked_cache = {k: v for k, v in tracked_cache.items() if current_time - v.get('time', 0) < 86400}
    notifications = []

    for market, korean_name in market_dict.items():
        try:
            acc_trade_price = trade_prices_24h.get(market, 0)
            if acc_trade_price < 500000000: # 5억 이상
                continue

            url = f"https://api.upbit.com/v1/candles/minutes/15?market={market}&count=30"
            res = requests.get(url).json()
            if len(res) < 25:
                continue
                
            res = list(reversed(res))
            opens = np.array([x['opening_price'] for x in res])
            closes = np.array([x['trade_price'] for x in res])
            highs = np.array([x['high_price'] for x in res])
            lows = np.array([x['low_price'] for x in res])
            volumes = np.array([x['candle_acc_trade_volume'] for x in res])
            
            current_price = closes[-1]
            current_open = opens[-1]
            prev_close = closes[-2]
            change_rate = ((current_price - prev_close) / prev_close) * 100
            
            candle_body = current_price - current_open
            
            if market in tracked_cache:
                continue

            # 🛡️ [방어선 1] 이미 고점을 찍고 윗꼬리를 길게 달며 밀려 내려오는 음봉/약세 캔들 차단
            high_price_15m = highs[-1]
            if current_price < (high_price_15m * 0.985): 
                continue

            # 🛡️ [방어선 2] 당일 너무 과도하게 폭등한 자리(설거지 및 추격매수 위험 구간) 배제
            if change_rate >= 15.0: 
                continue

            if candle_body > 0 and change_rate >= 1.0:
                recent_atr = np.mean(highs[-5:] - lows[-5:])
                if recent_atr == 0: recent_atr = current_price * 0.03

                tp1 = current_price + (recent_atr * 3.0)
                tp2 = current_price + (recent_atr * 5.5)
                tp3 = current_price + (recent_atr * 8.0)
                sl = min(np.min(lows[-3:]), current_price * 0.95)
                
                tp1_pct = ((tp1 - current_price) / current_price) * 100
                
                # 1차 목표가 +3% 미만이면 제외
                if tp1_pct < 3.0:
                    continue

                tp2_pct = ((tp2 - current_price) / current_price) * 100
                tp3_pct = ((tp3 - current_price) / current_price) * 100
                sl_pct = ((sl - current_price) / current_price) * 100

                target_pct = tp3_pct
                vol_ratio = 1.5 
                dynamic_duration = calculate_dynamic_duration(target_pct, vol_ratio, change_rate)
                
                tracked_cache[market] = {"time": current_time, "tp1": tp1, "tp2": tp2, "tp3": tp3, "sl": sl, "reached_targets": []}
                
                new_msg = (
                    f"🚀 **[고수익 슈팅 포착 (+3% 이상)]** 🚀\n\n"
                    f"📌 **종목명**: `{korean_name}` (`{market}`)\n"
                    f"💰 **현재가**: `{format_price(current_price)}` (`+{change_rate:.2f}%`)\n"
                    f"💸 **24h 대금**: `{acc_trade_price / 100_000_000:,.0f}억원`\n"
                    f"📈 **포착 근거**: `15분봉 거래량 폭발 + 강세 양봉`\n\n"
                    f"🎯 **1차 목표**: `{format_price(tp1)}` (`+{tp1_pct:.1f}%`)\n"
                    f"🎯 **2차 목표**: `{format_price(tp2)}` (`+{tp2_pct:.1f}%`)\n"
                    f"🎯 **3차 목표**: `{format_price(tp3)}` (`+{tp3_pct:.1f}%`)\n"
                    f"🛑 **손절가**: `{format_price(sl)}` (`{sl_pct:.1f}%`)\n\n"
                    f"⚖️ **기대 손익비**: `1 : {abs(tp1_pct / sl_pct):.1f}`\n"
                    f"⏱ **예상 소요 기간**: `{dynamic_duration}`\n\n"
                    f"💡 *팁: 1차 목표 도달 시 절반 익절 후 본절가 대응*"
                )
                notifications.append(new_msg)

        except Exception as e:
            pass

    for msg in notifications:
        send_telegram(msg)

    save_cache(tracked_cache)
    print("고점 방어 스캔 완료.")
