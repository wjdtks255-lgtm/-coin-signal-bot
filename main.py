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
    """ 저가 코인은 소수점까지 표시하고, 가격이 높으면 정수로 표시 """
    if price < 10:
        return f"{price:.2f}원"
    elif price < 1000:
        return f"{price:.1f}원"
    else:
        return f"{price:,.0f}원"

def calculate_dynamic_duration(target_pct, vol_ratio, change_rate):
    """ 코인별 목표 거리, 거래량, 변동성을 기반으로 예상 소요 시간을 동적 산출 """
    # 기본 시간 산출식: (목표 거리 % * 가중치) / (거래량 배율과 상승률의 에너지)
    speed_factor = max(vol_ratio, 1.0) * max(change_rate, 0.5)
    estimated_hours = (target_pct * 12.0) / speed_factor
    estimated_hours = max(2, min(estimated_hours, 168.0)) # 최소 2시간 ~ 최대 7일(168시간) 제한
    
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

if __name__ == "__main__":
    print("🌐 [올라운드 15분봉 + 코인별 동적 예상 기간 분석] 스캐너 가동 중...")
    
    market_dict = get_upbit_market_details()
    tracked_cache = load_cache()
    current_time = time.time()
    
    # 12시간 지난 캐시는 자동 정리
    tracked_cache = {k: v for k, v in tracked_cache.items() if current_time - v.get('time', 0) < 43200}
    
    notifications = []

    for market, korean_name in market_dict.items():
        try:
            url = f"https://api.upbit.com/v1/candles/minutes/15?market={market}&count=30"
            res = requests.get(url).json()
            if len(res) < 25:
                continue
                
            res = list(reversed(res))
            closes = np.array([x['trade_price'] for x in res])
            highs = np.array([x['high_price'] for x in res])
            lows = np.array([x['low_price'] for x in res])
            volumes = np.array([x['candle_acc_trade_volume'] for x in res])
            
            current_price = closes[-1]
            prev_close = closes[-2]
            change_rate = ((current_price - prev_close) / prev_close) * 100
            
            ma20 = np.mean(closes[-20:])
            std20 = np.std(closes[-20:])
            
            # 직전 20개 봉의 평균 거래량 계산 안정화
            avg_volume_20 = np.mean(volumes[-21:-1]) if len(volumes) >= 21 else np.mean(volumes[:-1])
            current_volume = volumes[-1]
            vol_ratio = current_volume / avg_volume_20 if avg_volume_20 > 0 else 0
            
            # --- [CASE 1: 이미 추적 중인 종목 모니터링] ---
            if market in tracked_cache:
                info = tracked_cache[market]
                tp1 = info['tp1']
                tp2 = info['tp2']
                tp3 = info['tp3']
                sl = info['sl']
                reached = info.get('reached_targets', [])
                
                if current_price <= sl:
                    notifications.append(f"🛑 **[손절가 이탈]** `{korean_name} ({market})`\n- 현재가 `{format_price(current_price)}`이 손절가를 이탈했습니다.")
                    del tracked_cache[market]
                    continue
                
                if 3 not in reached and current_price >= tp3:
                    notifications.append(f"🎯🔥 **[3차 목표가 최종 달성!]** `{korean_name} ({market})`\n- 최종 3차 목표가 돌파 완료!")
                    del tracked_cache[market]
                    continue
                elif 2 not in reached and current_price >= tp2:
                    notifications.append(f"🎯🚀 **[2차 목표가 달성!]** `{korean_name} ({market})`\n- 2차 목표가 도달!")
                    reached.append(2)
                elif 1 not in reached and current_price >= tp1:
                    notifications.append(f"🎯✨ **[1차 목표가 달성!]** `{korean_name} ({market})`\n- 1차 목표가 도달!")
                    reached.append(1)
                
                info['reached_targets'] = reached
                tracked_cache[market] = info
                continue

            recent_atr = np.mean(highs[-5:] - lows[-5:])
            if recent_atr == 0: recent_atr = current_price * 0.01

            # --- [CASE 2-A: 화끈한 강한 돌파 / 바닥 슈팅] ---
            is_strong_vol = vol_ratio >= 2.2
            is_strong_change = (3.0 <= change_rate <= 25.0)
            
            if is_strong_vol and is_strong_change:
                tp1 = current_price + (recent_atr * 1.2)
                tp2 = current_price + (recent_atr * 2.4)
                tp3 = current_price + (recent_atr * 4.0)
                
                tp1 = max(tp1, current_price * 1.03)
                tp2 = max(tp2, tp1 * 1.025)
                tp3 = max(tp3, tp2 * 1.025)
                
                sl = min(np.min(lows[-3:]), ma20 * 0.95)
                
                # 최종 목표가(tp3)까지의 거리 퍼센트 계산 후 동적 시간 산출
                target_pct = ((tp3 - current_price) / current_price) * 100
                dynamic_duration = calculate_dynamic_duration(target_pct, vol_ratio, change_rate)
                
                tracked_cache[market] = {"time": current_time, "tp1": tp1, "tp2": tp2, "tp3": tp3, "sl": sl, "reached_targets": []}
                
                new_msg = (
                    f"🔥 **[급등 / 바닥 슈팅 포착]** 🔥\n\n"
                    f"📌 **종목명**: `{korean_name}` (`{market}`)\n"
                    f"💰 **현재가**: `{format_price(current_price)}` (`+{change_rate:.2f}%`)\n\n"
                    f"🎯 **1차 목표**: `{format_price(tp1)}` (`+{((tp1-current_price)/current_price)*100:.1f}%`)\n"
                    f"🎯 **2차 목표**: `{format_price(tp2)}` (`+{((tp2-current_price)/current_price)*100:.1f}%`)\n"
                    f"🎯 **3차 목표**: `{format_price(tp3)}` (`+{((tp3-current_price)/current_price)*100:.1f}%`)\n"
                    f"🛑 **손절가**: `{format_price(sl)}` (`{((sl-current_price)/current_price)*100:.1f}%`)\n\n"
                    f"⏱ **예상 소요 기간**: `{dynamic_duration}`\n"
                    f"📊 **포착 근거**: 평소 대비 거래량 `{vol_ratio:.1f}배` 폭발 및 강력한 수급 유입"
                )
                notifications.append(new_msg)
                continue

            # --- [CASE 2-B: 잔잔한 상승세 / 수급 초입] ---
            is_mild_vol = vol_ratio >= 1.6
            is_mild_change = (0.5 <= change_rate < 3.0)
            
            if is_mild_vol and is_mild_change:
                tp1 = current_price + (recent_atr * 1.0)
                tp2 = current_price + (recent_atr * 2.0)
                tp3 = current_price + (recent_atr * 3.2)
                
                tp1 = max(tp1, current_price * 1.03)
                tp2 = max(tp2, tp1 * 1.02)
                tp3 = max(tp3, tp2 * 1.02)
                
                sl = min(np.min(lows[-3:]), ma20 * 0.97)
                
                # 최종 목표가(tp3)까지의 거리 퍼센트 계산 후 동적 시간 산출
                target_pct = ((tp3 - current_price) / current_price) * 100
                dynamic_duration = calculate_dynamic_duration(target_pct, vol_ratio, change_rate)
                
                tracked_cache[market] = {"time": current_time, "tp1": tp1, "tp2": tp2, "tp3": tp3, "sl": sl, "reached_targets": []}
                
                new_msg = (
                    f"⚡ **[약상승 / 수급 초입 포착]** ⚡\n\n"
                    f"📌 **종목명**: `{korean_name}` (`{market}`)\n"
                    f"💰 **현재가**: `{format_price(current_price)}` (`+{change_rate:.2f}%`)\n\n"
                    f"🎯 **1차 목표**: `{format_price(tp1)}` (`+{((tp1-current_price)/current_price)*100:.1f}%`)\n"
                    f"🎯 **2차 목표**: `{format_price(tp2)}` (`+{((tp2-current_price)/current_price)*100:.1f}%`)\n"
                    f"🎯 **3차 목표**: `{format_price(tp3)}` (`+{((tp3-current_price)/current_price)*100:.1f}%`)\n"
                    f"🛑 **손절가**: `{format_price(sl)}` (`{((sl-current_price)/current_price)*100:.1f}%`)\n\n"
                    f"⏱ **예상 소요 기간**: `{dynamic_duration}`\n"
                    f"📊 **포착 근거**: 거래량 `{vol_ratio:.1f}배` 유입 + 잔잔한 상승 모멘텀 발생"
                )
                notifications.append(new_msg)

        except Exception as e:
            pass

    for msg in notifications:
        send_telegram(msg)

    save_cache(tracked_cache)
    print("코인별 동적 예상 기간 분석 스캔 완료.")
