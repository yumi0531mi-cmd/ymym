merged.get("screen_volume") or 0
            )), int(float(candidate.get("screen_volume") or candidate.get("volume") or 0)))
                merged["screen_amount"] = max(float(old.get("screen_amount") or 0), float(candidate.get("screen_amount") or candidate.get("amount") or 0))
                ranked_rows[ticker] = merged
        except Exception:
            continue

    if ranked_rows:
        # 동적 수집된 후보군 필터링 및 정렬
        sorted_candidates = sorted(
            ranked_rows.values(),
            key=lambda x: (x.get("screen_amount", 0), x.get("screen_change", 0)),
            reverse=True
        )
        for item in sorted_candidates:
            price = item.get("screen_price", 0)
            amount = item.get("screen_amount", 0)
            if market == "국내":
                # 국내: 최소 주가 및 거래대금 조건 확인
                if price >= 500 and amount >= limit:
                    accepted.append(item)
            else:
                # 미국: 최소 주가 $1 이상
                if price >= 1.0:
                    accepted.append(normalize_us_item(item))
            if len(accepted) >= 30:
                break

    # 동적 수집 실패 시 하드코딩된 유니버스 fallback
    if not accepted:
        for row in source:
            item = dict(row)
            item["screen_price"] = 0.0
            item["screen_change"] = 0.0
            item["screen_volume"] = 0
            item["screen_amount"] = 0.0
            accepted.append(item)

    return accepted


def save_prediction(item: dict) -> None:
    """예측 결과를 SQLite DB에 기록하여 승률 추적에 활용."""
    ticker = str(item.get("ticker", ""))
    if not ticker:
        return
    issued = time.time()
    base_price = float(item.get("price", 0) or 0)
    f5 = float(item.get("forecast_5m", 0) or 0)
    f10 = float(item.get("forecast_10m", 0) or 0)
    f20 = float(item.get("forecast_20m", 0) or 0)
    f30 = float(item.get("forecast_30m", 0) or 0)

    if base_price <= 0:
        return

    try:
        with db_connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO predictions 
                (ticker, issued, base_price, f5, f10, f20, f30)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, issued, base_price, f5, f10, f20, f30)
            )
            conn.commit()
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Streamlit 메인 UI 구동부
# -----------------------------------------------------------------------------

# 자동 새로고침 설정 (15초 주기)
st_autorefresh(interval=15000, key="auto_refresh_scalp")

# 사이드바 컨트롤
st.sidebar.title("⚡ 초단타 타점 설정")
selected_market = st.sidebar.radio("마켓 선택", ["국내", "미국"], index=0)

clock_info = market_clock(selected_market)
st.sidebar.caption(f"**현재 세션:** {clock_info['session']}")
st.sidebar.caption(f"**시간:** {clock_info['local_time']}")

# 종목 검색 / 선택 UI
candidates = live_filtered_universe(selected_market)
candidate_options = {
    f"{c.get('name', c.get('ticker'))} ({c.get('ticker')})": c for c in candidates
}

st.sidebar.subheader("종목 선택")
manual_input = st.sidebar.text_input("종목명 또는 티커 직접 입력", value="")

selected_item_info = None
if manual_input.strip():
    resolved = resolve_manual(manual_input, selected_market)
    if resolved:
        selected_item_info = resolved
        st.sidebar.success(f"검색 성공: {resolved['name']} ({resolved['ticker']})")
    else:
        st.sidebar.error("종목을 찾을 수 없습니다.")

if not selected_item_info:
    selected_label = st.sidebar.selectbox("실시간 후보 종목 목록", list(candidate_options.keys()))
    if selected_label:
        selected_item_info = candidate_options[selected_label]

# 메인 콘텐츠 영역
if selected_item_info:
    ticker = selected_item_info["ticker"]
    name = selected_item_info.get("name", ticker)
    exchange = selected_item_info.get("exchange", "KR" if selected_market == "국내" else "NASDAQ")

    st.title(f"{name} ({ticker}) 초단타 타점 분석")

    # 스캐너 실행 및 데이터 처리
    with st.spinner("최신 실시간 호가 및 분봉 데이터를 분석 중입니다..."):
        try:
            mode = "국내 돌파" if selected_market == "국내" else "미국 급등주"
            raw_analysis = scanner().analyze_item(ticker, exchange, mode=mode)
            item_data = apply_mode_policy(raw_analysis, mode=mode)
            item_data = finalize_trade_item(item_data)
            if selected_market == "미국":
                item_data = normalize_us_item(item_data, selected_item_info)
        except Exception as err:
            st.error(f"데이터 분석 중 오류가 발생했습니다: {err}")
            st.stop()

    # 데이터 품질 검문
    checks, is_valid, spread_val = data_quality_gate(item_data, selected_market)

    if not is_valid:
        st.error("⚠️ 분봉/호가 데이터가 부족하거나 신뢰성이 낮아 상세 분석을 표시할 수 없습니다.")
        st.write("Data Quality Check Result:", checks)
    else:
        save_prediction(item_data)

        # 1. 상단 메트릭 카드
        p_col1, p_col2, p_col3, p_col4 = st.columns(4)
        price = float(item_data.get("price", 0))
        change = float(item_data.get("change_percent", 0))
        vwap = float(item_data.get("vwap", 0))
        rvol = float(item_data.get("rvol", 0))

        p_col1.metric("현재가", fmt(price), f"{change:+.2f}%")
        p_col2.metric("VWAP", fmt(vwap), f"{((price/vwap - 1)*100):+.2f}%" if vwap else "-")
        p_col3.metric("RVOL (상상거래량)", f"{rvol:.2f}배")
        p_col4.metric("스프레드", f"{spread_val:.3f}%" if spread_val is not None else "-")

        st.divider()

        # 2. 매수/매도 종합 진단
        v_label, v_color = verdict_text(item_data)
        regime, regime_desc = market_regime(item_data)

        votes, buys, sells, waits = strategy_consensus(item_data)
        score, buy_weight = weighted_strategy_score(votes, regime)

        st.subheader("💡 종합 매매 판정")
        res_col1, res_col2 = st.columns([1, 2])

        with res_col1:
            if v_color == "success":
                st.success(f"### {v_label}")
            elif v_color == "warning":
                st.warning(f"### {v_label}")
            else:
                st.error(f"### {v_label}")

            st.write(f"**장세 구분:** {regime}")
            st.caption(f"전략 가이드: {regime_desc}")
            st.write(f"**전략 가중 점수:** `{score:+.1f}점` (매수 강도 {buy_weight:.1f}%)")

        with res_col2:
            st.write(f"**기법 합의 현황:** 매수 `{buys}` | 매도 `{sells}` | 관망 `{waits}`")
            df_votes = pd.DataFrame(votes)
            st.dataframe(df_votes, use_container_width=True, hide_index=True)

        st.divider()

        # 3. 예측 시나리오 (Forecast)
        st.subheader("🔮 단기 가격 흐름 예측")
        f_cols = st.columns(4)
        timeframes = [("5분 후", "forecast_5m"), ("10분 후", "forecast_10m"), ("20분 후", "forecast_20m"), ("30분 후", "forecast_30m")]

        for idx, (label, key) in enumerate(timeframes):
            val = float(item_data.get(key, 0) or 0)
            target_price = price * (1 + val / 100)
            f_tag = forecast_label(val)

            with f_cols[idx]:
                st.markdown(
                    f"""
                    <div class="forecast-card">
                        <div class="title">{label} ({f_tag})</div>
                        <div class="price" style="color: {'#19a15f' if val > 0 else '#e45656' if val < 0 else '#333'};">
                            {fmt(target_price)} ({val:+.2f}%)
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        st.divider()

        # 4. 차트 시각화
        st.subheader("📈 1분봉 & VWAP / EMA 추세 차트")
        chart_close = item_data.get("chart_close_1m", [])
        chart_time = item_data.get("chart_time_1m", [])

        if chart_close and len(chart_close) > 0:
            df_chart = pd.DataFrame({
                "시간": chart_time[-len(chart_close):],
                "종가": chart_close,
            })
            chart = alt.Chart(df_chart).mark_line(color="#2962FF").encode(
                x=alt.X("시간:T", title="시간"),
                y=alt.Y("종가:Q", scale=alt.Scale(zero=False), title="가격"),
                tooltip=["시간", "종가"]
            ).properties(height=350)
            st.altair_chart(chart, use_container_width=True)
        else:
            st.info("차트 데이터를 표시할 수 없습니다.")
