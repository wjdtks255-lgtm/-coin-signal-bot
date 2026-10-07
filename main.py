import os, json, time, requests
from datetime import datetime, timezone, timedelta

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or "").strip()

STATE_FILE = "bot_state.json"
TRACKED_FILE = "tracked_coins.json"
KST = timezone(timedelta(hours=9))

def now():
    return datetime.now(KST)

def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def telegram(text):
    if not TOKEN or not CHAT_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15
        )
        return r.status_code == 200
    except Exception as e:
        print("Telegram error:", e)
        return False

def reset_all_positions():
    print("=" * 55)
    print("🔄 UPBIT SPOT BOT: ACTIVE POSITIONS RESET")
    print("=" * 55)

    # 1. 상태 데이터 완전 초기화
    empty_state = {
        "positions": {},
        "signals": {},
        "last_reset": now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    save_json(STATE_FILE, empty_state)
    save_json(TRACKED_FILE, {})

    print("✅ bot_state.json 및 tracked_coins.json 초기화 완료")

    # 2. 텔레그램 안내 메시지 발송
    msg = (
        "🔄 <b>[코인 현물 봇] 활성 포지션 전체 초기화 완료</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "• 기존 보유 감시 포지션(12개)이 모두 비워졌습니다.\n"
        "• 다음 스캔부터 전 종목을 처음부터 신규 검색합니다.\n"
        f"⏱ <code>{now().strftime('%Y-%m-%d %H:%M:%S KST')}</code>"
    )
    telegram(msg)
    print("📱 텔레그램 초기화 알림 전송 완료")

if __name__ == "__main__":
    reset_all_positions()
