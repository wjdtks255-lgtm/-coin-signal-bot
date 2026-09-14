import os
import requests
import numpy as np

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ 텔레그램 토큰 또는 챗 아이디가 설정되지 않았습니다!")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    res = requests.post(url, json=payload)
    print(f"텔레그램 전송 응답: {res.text}")

def get_market_status():
    """ 비트코인(KRW-BTC)의 최근 추세를 파악하여 시장 분위기(상승/하락) 판단 """
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
                return "BEARISH" # 비트 하락/약세장
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

def analyze_coin(market, korean_name):
    try:
        url = f"https://api.upbit.com/v1/candles/minutes/60?market={market}&count=30"
        res = requests.get(url).json()
        
        if len(res) < 25:
            return None
            
        res = list(reversed(res))
        
        closes = np.array([x['trade_price'] for x in res])
        highs = np.array([x['high_price'] for x in res])
        lows = np.array([x['low_price'] for x in res])
        volumes = np.array([x['candle_acc_trade_volume'] for x in res])
        
        current_price = closes[-1]
        prev_close = closes[-2]
        change_rate = ((current_price - prev_close) / prev_close) * 100
        
        # 1. 거래량 조건: 직전 20개 평균 대비 3배 이상 폭증
        avg_volume_20 = np.mean(volumes[-21:-1])
        current_volume = volumes[-1]
        vol_ratio = current_volume / avg_volume_20
        is_volume_spike = vol_ratio >= 3.0
        
        # 2. 이동평균선 단기 정배열 (5일선 > 20일선)
        ma5 = np.mean(closes[-5:])
        ma20 = np.mean(closes[-20:])
        is_bullish = ma5 > ma20
        
        # 3. 볼린저 밴드 상단 돌파
        std20 = np.std(closes[-20:])
        upper_band = ma20 + (std20 * 2.0)
        is_band_breakout = current_price >= upper_band
        
        # 조건 만족 시 (고승률 필터)
        if is_volume_spike and is_bullish and is_band_breakout and (2.0 <= change_rate <= 15.0):
            
            # --- [동적 목표가 및 손절가 계산 로직] ---
            # 최근 변동성(ATR 유사 개념) 및 고가 매물대 활용
            recent_atr = np.mean(highs[-5:] - lows[-5:])
            
            # 1차 목표가: 볼린저 상단과 최근 변동성을 반영한 근거리 저항선
            tp1 = current_price + (recent_atr * 1.2)
            # 2차 목표가: 1차보다 확장된 변동성 구간 (중거리)
            tp2 = current_price + (recent_atr * 2.5)
            # 3차 목표가: 세력 펌핑 시 오버슈팅을 고려한 상한선 (장거리)
            tp3 = current_price + (recent_atr * 4.0)
            
            # 손절가: 최근 5개 봉 최저점 혹은 20일 이평선 하단
            sl = min(np.min(lows[-5:]), ma20 * 0.98)
            
            # 퍼센트 계산용
            tp1_pct = ((tp1 - current_price) / current_price) * 100
            tp2_pct = ((tp2 - current_price) / current_price) * 100
            tp3_pct = ((tp3 - current_price) / current_price) * 100
            sl_pct = ((sl - current_price) / current_price) * 100

            reasons = []
            reasons.append(f"• 평소 거래량 대비 `{vol_ratio:.1f}배` 폭증 (수급 유입)")
            reasons.append("• 단기 이동평균선(5선/20선) 정배열 추세 형성")
            reasons.append("• 볼린저 밴드 상단 돌파 및 변동성(ATR) 확장세 포착")
            
            return {
                "market": market,
                "korean_name": korean_name,
                "price": current_price,
                "change": change_rate,
                "tp1": tp1, "tp1_pct": tp1_pct,
                "tp2": tp2, "tp2_pct": tp2_pct,
                "tp3": tp3, "tp3_pct": tp3_pct,
                "sl": sl, "sl_pct": sl_pct,
                "reasons": "\n".join(reasons)
            }
            
    except Exception as e:
        pass
    return None

if __name__ == "__main__":
    print("🎯 동적 목표가 분석 시스템 가동...")
    
    market_status = get_market_status()
    print(f"현재 비트코인 시장 분석 상태: {market_status}")
    
    market_dict = get_upbit_market_details()
    
    signals = []
    for market, korean_name in market_dict.items():
        result = analyze_coin(market, korean_name)
        if result:
            signals.append(result)
            
    if signals:
        for s in signals:
            if market_status == "BEARISH":
                header_warning = "⚠️ **[주의] 비트 하락장 속 개별 펌핑(역주행) 포착!**\n*(장 분위기가 불안정하므로 분할 매도와 리스크 관리를 철저히 하세요)*\n\n"
            else:
                header_warning = "🔥 **[고승률 3차 목표가 시그널 포착]** 🔥\n\n"

            msg = (
                f"{header_warning}"
                f"📌 **종목명**: `{s['korean_name']}` (`{s['market']}`)\n"
                f"💰 **현재가**: `{s['price']:,.0f}원` (`+{s['change']:.2f}%`)\n\n"
                f"🎯 **1차 목표가**: `{s['tp1']:,.0f}원` (`+{s['tp1_pct']:.1f}%`)\n"
                f"🎯 **2차 목표가**: `{s['tp2']:,.0f}원` (`+{s['tp2_pct']:.1f}%`)\n"
                f"🎯 **3차 목표가**: `{s['tp3']:,.0f}원` (`+{s['tp3_pct']:.1f}%`)\n"
                f"🛑 **손절가**: `{s['sl']:,.0f}원` (`{s['sl_pct']:.1f}%`)\n\n"
                f"📊 **급등 근거**:\n{s['reasons']}\n\n"
                f"⚡ *차트의 변동성 폭(ATR)을 분석하여 산출된 동적 가격입니다. 분할 익절을 권장합니다!*"
            )
            send_telegram(msg)
    else:
        print("현재 조건에 부합하는 급등 코인이 없습니다.")
