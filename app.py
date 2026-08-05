import html
import streamlit as st


def calculate_scalping_targets(
    current_price,
    low_1m,
    low_3m,
    high_5m,
    high_15m,
    holding_entry_price=None,
):
    """1M / 3M / 5M / 15M 봉 및 포지션 상태를 조합하여 진입/손절/목표가를 계산합니다."""
    # 1. 진입가 결정: 보유 중이면 기존 진입가로 고정(Freeze), 아니면 현재가
    entry_price = (
        holding_entry_price
        if holding_entry_price and holding_entry_price > 0
        else current_price
    )

    # 2. 손절가 (Stop Loss): 1분봉/3분봉 최저점 중 높은 값과 -1.8% 비율 손절 중 높은 가격 선택
    structural_stop = max(low_1m, low_3m)
    percent_stop = entry_price * 0.982  # -1.8% 손절
    stop_loss = max(structural_stop, percent_stop)

    # 리스크 범위 ($)
    risk = entry_price - stop_loss
    if risk <= 0:  # 예외 처리: 손절선이 진입가보다 높거나 같을 경우 기본 -1.5% 설정
        risk = entry_price * 0.015
        stop_loss = entry_price - risk

    # 3. 1차 매도가 (Target 1): Risk/Reward = 1:1.5 및 5분봉 전고점 반영
    rr_target_1 = entry_price + (risk * 1.5)
    target_1 = (
        min(high_5m, rr_target_1) if high_5m > entry_price else rr_target_1
    )

    # 4. 2차 매도가 (Target 2): Risk/Reward = 1:3.0 및 15분봉 전고점 반영
    rr_target_2 = entry_price + (risk * 3.0)
    target_2 = max(high_15m, rr_target_2)

    # 퍼센트 변환
    stop_pct = ((stop_loss - entry_price) / entry_price) * 100
    t1_pct = ((target_1 - entry_price) / entry_price) * 100
    t2_pct = ((target_2 - entry_price) / entry_price) * 100

    return {
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "stop_pct": stop_pct,
        "target_1": target_1,
        "t1_pct": t1_pct,
        "target_2": target_2,
        "t2_pct": t2_pct,
        "risk": risk,
    }


def render_scalping_card(
    ticker,
    name,
    market,
    current_price,
    change_pct,
    low_1m,
    low_3m,
    high_5m,
    high_15m,
    in_position=False,
    entry_price_fixed=None,
):
    """단타 종목별 진입/손절/익절 라인 및 실시간 판정 스캐너 카드 렌더링"""
    calc = calculate_scalping_targets(
        current_price=current_price,
        low_1m=low_1m,
        low_3m=low_3m,
        high_5m=high_5m,
        high_15m=high_15m,
        holding_entry_price=entry_price_fixed if in_position else None,
    )

    entry = calc["entry_price"]
    stop = calc["stop_loss"]
    stop_pct = calc["stop_pct"]
    t1 = calc["target_1"]
    t1_pct = calc["t1_pct"]
    t2 = calc["target_2"]
    t2_pct = calc["t2_pct"]

    # 통화 단위 ($ / ₩) 설정
    curr_symbol = "$" if market in ["NAS", "NYS", "AMS", "US"] else "₩"

    # 손익비 안전 계산 (ZeroDivisionError 방지)
    rr_ratio = round(t1_pct / abs(stop_pct), 1) if abs(stop_pct) > 0 else 0.0

    # 실시간 상태 판정 로직
    if in_position:
        pnl_pct = ((current_price - entry) / entry) * 100
        if current_price <= stop:
            verdict_class = "v-red"
            verdict_text = f"🚨 손절 대응 필요! (현재가 {curr_symbol}{current_price:.3f} ≤ 손절가 {curr_symbol}{stop:.3f})"
        elif current_price >= t2:
            verdict_class = "v-green"
            verdict_text = f"🔥 2차 목표 달성! 전량 익절 권장 (+{pnl_pct:.2f}%)"
        elif current_price >= t1:
            verdict_class = "v-green"
            verdict_text = (
                f"🎉 1차 목표 달성! 50% 분할 익절 구간 (+{pnl_pct:.2f}%)"
            )
        else:
            verdict_class = "v-yellow"
            verdict_text = f"✊ 포지션 보유 중 (현재 수익률: {pnl_pct:+.2f}%)"
    else:
        verdict_class = "v-gray"
        verdict_text = (
            f"🔍 매수 관망 / 타점 모니터링 중 (손익비 1:{rr_ratio})"
        )

    # 가격 상승/하락 스타일
    change_class = "change-up" if change_pct >= 0 else "change-down"
    change_sign = "+" if change_pct >= 0 else ""

    # safe escape 처리
    safe_name = html.escape(str(name))
    safe_ticker = html.escape(str(ticker))

    # 카드 HTML 생성
    html_content = f"""
    <div class="stock-card">
        <div class="card-head">
            <div>
                <div class="stock-name">{safe_name} <span style="font-size:0.75rem; color:#8b95a8;">({market})</span></div>
                <div class="ticker">{safe_ticker}</div>
            </div>
            <div>
                <div class="price">{curr_symbol}{current_price:,.2f if current_price >= 100 else f'{current_price:,.3f}'}</div>
                <div class="{change_class}">{change_sign}{change_pct:.2f}%</div>
            </div>
        </div>
        
        <div class="verdict {verdict_class}">
            {verdict_text}
        </div>

        <div class="trade-title">🎯 초단타(스캘핑) 라인 설정</div>
        <div class="trade-grid">
            <div class="trade-box">
                <span>{'진입 확정가' if in_position else '예상 진입가'}</span>
                <b class="entry">{curr_symbol}{entry:,.3f}</b>
            </div>
            <div class="trade-box">
                <span>손절가 (1M/3M)</span>
                <b class="stop">{curr_symbol}{stop:,.3f} <small>({stop_pct:.1f}%)</small></b>
            </div>
            <div class="trade-box">
                <span>1차 익절 (5M)</span>
                <b class="target">{curr_symbol}{t1:,.3f} <small>(+{t1_pct:.1f}%)</small></b>
            </div>
        </div>
        <div class="trade-grid" style="margin-top:5px;">
            <div class="trade-box" style="grid-column: span 3;">
                <span>2차 익절 목표 (15M 파동 상단)</span>
                <b class="target" style="color:#6ee79a;">{curr_symbol}{t2:,.3f} <small>(+{t2_pct:.1f}%)</small></b>
            </div>
        </div>
    </div>
    """

    # Streamlit에 HTML 출력
    st.markdown(html_content, unsafe_allow_html=True)
