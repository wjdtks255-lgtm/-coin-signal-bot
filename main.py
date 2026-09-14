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
    """ 비트코인(KRW-BTC)의 최근 추세를 파악하여 시장 분위기 판단 """
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
        
        current_price = closes[-1]
        prev_close = closes[-2]
        change_rate = ((current_price - prev_close) / prev_close) * 100
        
        # 🚨 [테스트용 조건] 까다로운 필터를 모두 빼고, 0.5% 이상 상승 중이면 무조건 포착!
        if change_rate >= 0.5:
            
            # 변동성(ATR) 기반 동적 목표가 및 손절가 계산
            recent_atr = np.mean(highs[-5:] - lows[-5:])
            ma20 = np.mean(closes[-20:])
            
            tp1 = current_price + (recent_atr * 1.2)
            tp2 = current_price + (recent_atr * 2.5)
            tp3 = current_price + (recent_atr * 4.0)
            sl = min(np.min(lows[-5:]), ma20 * 0.98)
            
            tp1_pct = ((tp1 - current_price) / current_price) * 100
            tp2_pct = ((tp2 - current_price) / current_price) * 100
            tp3_pct = ((tp3 - current_price) / current_price) * 100
            sl_pct = ((sl - current_price) / current_price) * 100

            reasons = []
            reasons.append(f"• [테스트 모드] 현재 상승률 `+{change_rate:.2f}%` 감지")
            reasons.append("• 테스트를 위해 필터 조건을 일시적으로 해제한 상태입니다.")
            
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
    print("🧪 테스트용 시그널 스캔 시스템 가동...")
    
    market_status = get_market_status()
    market_dict = get_upbit_market_details()
    
    signals = []
    for market, korean_name in market_dict.items():
        result = analyze_coin(market, korean_name)
        if result:
            signals.append(result)
            
    if signals:
        # 너무 많은 알림이 한꺼번에 오면 텔레그램에서 차단할 수 있으므로 상위 3개만 테스트 전송
        for s in signals[:3]:
            msg = (
                f"🧪 **[테스트 알림 발송]** 🧪\n\n"
                f"📌 **종목명**: `{s['korean_name']}` (`{s['market']}`)\n"
                f"💰 **현재가**: `{s['price']:,.0f}원` (`+{s['change']:.2f}%`)\n\n"
                f"🎯 **1차 목표가**: `{s['tp1']:,.0f}원` (`+{s['tp1_pct']:.1f}%`)\n"
                f"🎯 **2차 목표가**: `{s['tp2']:,.0f}원` (`+{s['tp2_pct']:.1f}%`)\n"
                f"🎯 **3차 목표가**: `{s['tp3']:,.0f}원` (`+{s['tp3_pct']:.1f}%`)\n"
                f"🛑 **손절가**: `{s['sl']:,.0f}원` (`{s['sl_pct']:.1f}%`)\n\n"
                f"📊 **테스트 근거**:\n{s['reasons']}"
            )
            send_telegram(msg)
    else:
        print("현재 0.5% 이상 상승 중인 코인이 없습니다.")
