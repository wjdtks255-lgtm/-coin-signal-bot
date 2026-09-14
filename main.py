import os
import requests

# 깃허브 Secrets에서 토큰과 챗 아이디를 불러옵니다
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    requests.post(url, json=payload)

def check_upbit():
    try:
        url = "https://api.upbit.com/v1/market/all"
        markets = [item['market'] for item in requests.get(url).json() if item['market'].startswith('KRW-')]
        
        # 10개만 테스트로 조회
        ticker_url = f"https://api.upbit.com/v1/ticker?markets={','.join(markets[:10])}"
        res = requests.get(ticker_url).json()
        
        msg = "📊 [업비트 상위 코인 시세 현황]\n"
        for item in res:
            coin = item['market']
            price = item['trade_price']
            change = item['signed_change_rate'] * 100
            msg += f"- {coin}: {price:,.0f원} ({change:+.2f}%)\n"
        
        send_telegram(msg)
    except Exception as e:
        print(f"업비트 에러: {e}")

if __name__ == "__main__":
    send_telegram("🚀 깃허브 액션에서 급등 스캐너 봇이 실행되었습니다!")
    check_upbit()
