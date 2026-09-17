import json
import os
from datetime import datetime, timedelta

CACHE_FILE = "tracked_coins.json"
MIN_ACC_TRADE_PRICE = 300_000_000_000  # 최소 거래대금 300억 원 이상
MAX_ALLOWABLE_STOP_LOSS_PCT = 5.0      # 최대 허용 손절 폭 5% 이내
COOLDOWN_HOURS = 24                    # 동일 종목 24시간 중복 방지 쿨타임

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
    # [조건 1] 300억 미만 저대금 종목 차단
    if acc_trade_price < MIN_ACC_TRADE_PRICE:
        return

    # [조건 2] 손절 폭이 -5%를 초과하면 차단
    if calculated_stop_loss_pct > MAX_ALLOWABLE_STOP_LOSS_PCT:
        return

    # [조건 3] 거래량 폭발 조건 미충족 시 차단
    if not volume_spike_flag:
        return

    # [조건 4] 24시간 재알림 쿨타임 검증
    cache = load_cache()
    now = datetime.now()
    
    if ticker in cache:
        last_alert_str = cache[ticker].get("last_alert")
        if last_alert_str:
            last_alert_time = datetime.fromisoformat(last_alert_str)
            if now - last_alert_time < timedelta(hours=COOLDOWN_HOURS):
                return

    # [가격 산출] 손절가 및 1·2·3차 목표가 계산
    stop_loss = current_price * (1 - (calculated_stop_loss_pct / 100))
    target_1 = current_price * 1.03  # 1차 목표 (+3.0%)
    target_2 = current_price * 1.06  # 2차 목표 (+6.0%)
    target_3 = current_price * 1.09  # 3차 목표 (+9.0%)

    # [전문가형 메시지 포맷 (1~3차 목표가 포함)]
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
        f"💡 *Notice: 300억 이상 유동성 검증 및 리스크 필터 적용완료*"
    )
    
    # 텔레그램 전송 함수 (사용 중인 함수로 연동)
    # send_telegram_message(message)
    print(message)  # 테스트용 출력

    # 쿨타임 저장
    cache[ticker] = {"last_alert": now.isoformat()}
    save_cache(cache)
