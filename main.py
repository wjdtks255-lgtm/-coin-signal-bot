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

def get_market_status():
    """ 비트코인(KRW-BTC) 최근 추세 파악 """
    try:
        url = "https://api.upbit.com/v1/candles/minutes/60?market=KRW-BTC&count=10"
        res = requests.get(url).json()
        if len(res) >= 10:
            res = list(reversed(res))
            closes = [x['trade_price'] for x in res]
            btc_change = ((closes[-1] - closes[0]) / closes[0]) * 100
            ma_short = np.mean(closes[-3:])
            ma_long = np.mean(closes[-10:])
            
            if ma_short < ma_long and btc_change < 0:
                return "BEARISH"
    except Exception as e:
        print(f"비트코인 상태 조회 에러: {e}")
    return "BULLISH / NEUTRAL"

def get_upbit_market_details():
    url = "https://api.upbit.com/v1/market/all"
    res = requests.get(url).json()
    market_dict = {}
    for item in res:
        if item['market'].startswith('KRW-') and item['market'] != 'KRW-BTC':
            market_dict[item['market']] = item['korean_name']
    return market_dict

if __name__ == "__main__":
    print("🎯 유연화된 스마트 멀티 트래킹 봇 가동 중...")
    
    market_status = get_market_status()
    market_dict = get_upbit_market_details()
    tracked_cache = load_cache()
    current_time = time.time()
    
    # 24시간 지난 캐시는 자동 정리
    tracked_cache = {k: v for k, v in tracked_cache.items() if current_time - v.get('time', 0) < 86400}
    
    notifications = []

    for market, korean_name in market_dict.items():
        try:
            url = f"https://api.upbit.com/v1/candles/minutes/60?market={market}&count=30"
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
            
            ma5 = np.mean(closes[-5:])
            ma20 = np.mean(closes[-20:])
            
            avg_volume_20 = np.mean(volumes[-21:-1])
            current_volume = volumes[-1]
            vol_ratio = current_volume / avg_volume_20 if avg_volume_20 > 0 else 0
            
            # --- [CASE 1: 이미 감지해서 추적 중인 종목 모니터링] ---
            if market in tracked_cache:
                info = tracked_cache[market]
                tp1 = info['tp1']
                tp2 = info['tp2']
                tp3 = info['tp3']
                sl = info['sl']
                reached = info.get('reached_targets', [])
                
                # 1. 손절가 도달 체크
                if current_price <= sl:
                    notifications.append(f"🛑 **[손절가 도달]** `{korean_name} ({market})`\n- 현재가 `{current_price:,.0f}원`이 설정된 손절가 아래로 이탈했습니다. 리스크 관리를 진행하세요.")
                    del tracked_cache[market]
                    continue
                
                # 2. 추세 종료 체크
                if ma5 < ma20:
                    notifications.append(f"⚠️ **[추세 종료 알림]** `{korean_name} ({market})`\n- 단기 이평선이 하향 이탈하며 상승 추세가 종료되었습니다.")
                    del tracked_cache[market]
                    continue
                
                # 3. 목표가 단계별 도달 체크
                if 3 not in reached and current_price >= tp3:
                    notifications.append(f"🎯🔥 **[3차 목표가 최종 달성!]** `{korean_name} ({market})`\n- 현재가 `{current_price:,.0f}원`! 최종 3차 목표가를 돌파했습니다. 전량 익절을 축하드립니다!")
                    info['reached_targets'] = [1, 2, 3]
                    del tracked_cache[market]
                    continue
                elif 2 not in reached and current_price >= tp2:
                    notifications.append(f"🎯🚀 **[2차 목표가 달성!]** `{korean_name} ({market})`\n- 현재가 `{current_price:,.0f}원`이 2차 목표가에 도달했습니다! 절반 이상 분할 익절을 챙기세요.")
                    reached.append(2)
                elif 1 not in reached and current_price >= tp1:
                    notifications.append(f"🎯✨ **[1차 목표가 달성!]** `{korean_name} ({market})`\n- 현재가 `{current_price:,.0f}원`이 1차 목표가에 도달했습니다! 가볍게 익절을 시작하세요.")
                    reached.append(1)
                
                info['reached_targets'] = reached

                # 4. 수급 추가 폭증 시 탄력 알림
                last_vol_ratio = info.get('last_vol_ratio', 0)
                if vol_ratio >= last_vol_ratio * 1.2 and vol_ratio >= 2.5 and (current_time - info.get('last_alert_time', 0) > 7200):
                    info['last_alert_time'] = current_time
                    info['last_vol_ratio'] = vol_ratio
                    notifications.append(
                        f"⚡ **[수급 급증 / 추가 탄력 포착]** `{korean_name} ({market})`\n"
                        f"- 현재가 `{current_price:,.0f}원` (`+{change_rate:.2f}%`)\n"
                        f"- 거래량이 평소 대비 **{vol_ratio:.1f}배**로 더 강력하게 폭증하며 추가 상승세가 붙고 있습니다!"
                    )
                
                tracked_cache[market] = info
                continue

            # --- [CASE 2: 새로운 급등 종목 발굴 (조건 완화)] ---
            is_volume_spike = vol_ratio >= 1.8   # 기존 3.0 -> 1.8배로 완화
            is_bullish = ma5 >= ma20             # 정배열 조건 완화
            
            # 조건 완화: 거래량 폭증이 동반되면서 양봉(상승세)인 종목 포착
            if is_volume_spike and is_bullish and (0.5 <= change_rate <= 20.0):
                recent_atr = np.mean(highs[-5:] - lows[-5:])
                if recent_atr == 0:
                    recent_atr = current_price * 0.01
                
                tp1 = current_price + (recent_atr * 1.0)
                tp2 = current_price + (recent_atr * 2.0)
                tp3 = current_price + (recent_atr * 3.5)
                sl = min(np.min(lows[-5:]), ma20 * 0.97)
                
                tp1_pct = ((tp1 - current_price) / current_price) * 100
                tp2_pct = ((tp2 - current_price) / current_price) * 100
                tp3_pct = ((tp3 - current_price) / current_price) * 100
                sl_pct = ((sl - current_price) / current_price) * 100

                reasons = [
                    f"• 평소 거래량 대비 `{vol_ratio:.1f}배` 유입",
                    "• 단기 이동평균선 상향 유지 및 수급 포착",
                    "• 변동성 확장 구간 진입"
                ]
                
                tracked_cache[market] = {
                    "time": current_time,
                    "last_alert_time": current_time,
                    "tp1": tp1,
                    "tp2": tp2,
                    "tp3": tp3,
                    "sl": sl,
                    "last_vol_ratio": vol_ratio,
                    "reached_targets": []
                }
                
                prefix = "⚠️ **[주의] 비트 하락장 속 수급 포착!**\n\n" if market_status == "BEARISH" else "🔥 **[고승률 3차 목표가 신규 시그널]** 🔥\n\n"
                
                new_msg = (
                    f"{prefix}"
                    f"📌 **종목명**: `{korean_name}` (`{market}`)\n"
                    f"💰 **현재가**: `{current_price:,.0f}원` (`+{change_rate:.2f}%`)\n\n"
                    f"🎯 **1차 목표가**: `{tp1:,.0f}원` (`+{tp1_pct:.1f}%`)\n"
                    f"🎯 **2차 목표가**: `{tp2:,.0f}원` (`+{tp2_pct:.1f}%`)\n"
                    f"🎯 **3차 목표가**: `{tp3:,.0f}원` (`+{tp3_pct:.1f}%`)\n"
                    f"🛑 **손절가**: `{sl:,.0f}원` (`{sl_pct:.1f}%`)\n\n"
                    f"📊 **급등 근거**:\n" + "\n".join(reasons)
                )
                notifications.append(new_msg)

        except Exception as e:
            pass

    for msg in notifications:
        send_telegram(msg)

    save_cache(tracked_cache)
    print("스캔 및 모니터링 완료.")
