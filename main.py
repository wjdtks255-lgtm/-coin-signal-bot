import json
import os
from datetime import datetime, timedelta

CACHE_FILE = "tracked_coins.json"
MIN_ACC_TRADE_PRICE = 50_000_000_000   # 테스트를 위해 거래대금 기준을 50억 원으로 일시 완화
MAX_ALLOWABLE_STOP_LOSS_PCT = 10.0     # 손절 폭 허용치 10%로 완화
COOLDOWN_HOURS = 1                     # 쿨타임도 일단 1시간으로 단축 테스트

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

def evaluate_and_send_signal(ticker, current_price, acc_trade_price, volume_spike_flag, calculated_stop_loss_pct):
    print(f"[{ticker}] 검토 중... 대금: {acc_trade_price/100000000:,.1f}억, 손절폭: {calculated_stop_loss_pct}%")

    # [조건 1] 거래대금 필터
    if acc_trade_price < MIN_ACC_TRADE_PRICE:
        print(f" -> [스킵] 거래대금 부족 ({acc_trade_price/100000000:,.1f}억 < 50억)")
        return

    # [조건 2] 손절 폭 필터
    if calculated_stop_loss_pct > MAX_ALLOWABLE_STOP_LOSS_PCT:
        print(f" -> [스킵] 손절 폭 너무 넓음 ({calculated_stop_loss_pct}%)")
        return

    # [조건 3] 거래량 폭발 플래그
    if not volume_spike_flag:
        print(f" -> [스킵] 거래량 폭발 조건 미충족")
        return

    # [조건 4] 쿨타임 검증
    cache = load_cache()
    now = datetime.now()
    if ticker in cache:
        last_alert_str = cache[ticker].get("last_alert")
        if last_alert_str:
            last_alert_time = datetime.fromisoformat(last_alert_str)
            if now - last_alert_time < timedelta(hours=COOLDOWN_HOURS):
                print(f" -> [스킵] 쿨타임 중 (최근 알림: {last_alert_str})")
                return

    # 가격 산출
    stop_loss = current_price * (1 - (calculated_stop_loss_pct / 100))
    target_1 = current_price * 1.03
    target_2 = current_price * 1.06
    target_3 = current_price * 1.09

    message = (
        f"📊 **[QUANT SIGNAL] 현물 마켓 트렌드 포착**\n"
        f"────────────────────────\n"
        f"▪ **종목명**: `{ticker}`\n"
        f"▪ **현재가**: `{current_price:,.1f} KRW`\n"
        f"▪ **24H 거래대금**: `{acc_trade_price / 100_000_000:,.1f}억 원`\n\n"
        f"🎯 **TARGET (분할 목표가)**\n"
        f"  └ 1차 목표: `{target_1:,.1f}원` (+3.0%)\n"
        f"  └ 2차 목표: `{target_2:,.1f}원` (+6.0%)\n"
        f"  └ 3차 목표: `{target_3:,.1f}원` (+9.0%)\n\n"
        f"🛡️ **RISK MANAGEMENT (방어)**\n"
        f"  └ 타이트 손절가: `{stop_loss:,.1f}원` (-{calculated_stop_loss_pct}%)\n"
        f"────────────────────────\n"
        f"💡 *Notice: 디버깅 모드 테스트 중*"
    )
    
    print(f"🔥 [알림 전송 성공!] {ticker} 신호 발송 준비 완료")
    # 실제 전송 함수 연동 시 아래 주석 해제
    # send_telegram_message(message)
    print(message)

    cache[ticker] = {"last_alert": now.isoformat()}
    save_cache(cache)
