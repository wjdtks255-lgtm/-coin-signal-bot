import os
import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram_test():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ 텔레그램 토큰 또는 챗 아이디가 설정되지 않았습니다!")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    text = "🚨 **[긴급 연동 테스트]**\n- 봇이 채널로 메시지를 성공적으로 전송했습니다!\n- 이 메시지가 보인다면 토큰과 채널 ID가 완벽하게 일치합니다."
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    
    res = requests.post(url, json=payload)
    print(f"텔레그램 전송 응답 코드: {res.status_code}")
    print(f"텔레그램 전송 응답 내용: {res.text}")

if __name__ == "__main__":
    print("🎯 강제 텔레그램 테스트 시작...")
    send_telegram_test()
    print("테스트 완료.")
