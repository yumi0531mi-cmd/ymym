-- 상승·반복단타 혼합 스캐너: Supabase 전체 경로 검증 분석 쿼리
--
-- 대상: public.scanner_events 테이블의 kind = 'validation_case' JSONB payload
-- 기준: COMPLETE 데이터만 전체 경로(5·10·15·30분) 승률의 분모로 사용합니다.
--
-- 주의: 결과가 100건 미만이면 80% 통과/실패를 확정하지 않고 "표본 누적 중"으로 표시합니다.


-- ================================================================
-- 1. 전체 표본·전체 경로·엄격 통과·비용 반영 성과 요약
-- ================================================================
WITH base AS (
    SELECT
        id AS event_id,
        created_at AS stored_at,
        payload,
        payload ->> 'market' AS market,
        payload ->> 'session' AS session,
        payload ->> 'strategy' AS strategy,
        payload ->> 'symbol' AS symbol,
        payload ->> 'data_completeness' AS data_completeness,
        NULLIF(payload ->> 'quote_pass', '')::boolean AS quote_pass,
        NULLIF(payload ->> 'entry_executable', '')::boolean AS entry_executable,
        NULLIF(payload ->> 'structural_target_confirmed', '')::boolean AS structural_target_confirmed,
        NULLIF(payload ->> 'full_path_pass', '')::boolean AS full_path_pass,
        NULLIF(payload ->> 'regime_pass', '')::boolean AS regime_pass,
        NULLIF(payload ->> 'target_pass', '')::boolean AS target_pass,
        NULLIF(payload ->> 'complete_four_area_pass', '')::boolean AS complete_four_area_pass,
        NULLIF(payload ->> 'net_return_pct', '')::numeric AS net_return_pct
    FROM public.scanner_events
    WHERE kind = 'validation_case'
), summary AS (
    SELECT
        COUNT(*) AS all_saved_signals,
        COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE') AS complete_samples,
        COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND quote_pass IS TRUE) AS quote_verified,
        COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND entry_executable IS TRUE) AS executable_entries,
        COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND structural_target_confirmed IS TRUE) AS structural_targets,
        COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND full_path_pass IS TRUE) AS full_path_passes,
        COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND regime_pass IS TRUE) AS regime_passes,
        COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND target_pass IS TRUE) AS target_first_passes,
        COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND complete_four_area_pass IS TRUE) AS strict_passes,
        COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND net_return_pct > 0) AS cost_positive_cases,
        AVG(net_return_pct) FILTER (WHERE data_completeness = 'COMPLETE') AS average_net_return_pct
    FROM base
)
SELECT
    all_saved_signals AS "저장 신호 수",
    complete_samples AS "완료된 전체 경로 표본 수",
    quote_verified AS "현재가 검증 통과 수",
    executable_entries AS "체결 가능 통과 수",
    structural_targets AS "구조 목표 확인 수",
    full_path_passes AS "5·10·15·30분 동시 통과 수",
    ROUND(100.0 * full_path_passes / NULLIF(complete_samples, 0), 2) AS "전체 경로 통과율(%)",
    regime_passes AS "장세 판정 통과 수",
    target_first_passes AS "1차 목표 선도달 수",
    strict_passes AS "엄격 전체 통과 수",
    ROUND(100.0 * strict_passes / NULLIF(complete_samples, 0), 2) AS "엄격 전체 통과율(%)",
    cost_positive_cases AS "비용 반영 양수 수",
    ROUND(100.0 * cost_positive_cases / NULLIF(complete_samples, 0), 2) AS "비용 반영 양수율(%)",
    ROUND(average_net_return_pct, 4) AS "평균 비용 반영 수익률(%)",
    CASE
        WHEN complete_samples < 100 THEN '표본 누적 중: 완료 표본 100건 필요'
        WHEN strict_passes >= 80
         AND 100.0 * strict_passes / NULLIF(complete_samples, 0) >= 80
         AND average_net_return_pct > 0 THEN '80% 전체 경로 기준 통과'
        ELSE '80% 전체 경로 기준 미통과: 실패 원인 분석 필요'
    END AS "100건·80% 판정"
FROM summary;


-- ================================================================
-- 2. 시장·세션·전략별 전체 경로 성적표
--    어떤 조합이 실제로 100건/80% 기준에 도달했는지 확인합니다.
-- ================================================================
WITH base AS (
    SELECT
        payload ->> 'market' AS market,
        payload ->> 'session' AS session,
        COALESCE(NULLIF(payload ->> 'strategy', ''), '미분류') AS strategy,
        payload ->> 'data_completeness' AS data_completeness,
        NULLIF(payload ->> 'full_path_pass', '')::boolean AS full_path_pass,
        NULLIF(payload ->> 'complete_four_area_pass', '')::boolean AS strict_pass,
        NULLIF(payload ->> 'target_pass', '')::boolean AS target_pass,
        NULLIF(payload ->> 'net_return_pct', '')::numeric AS net_return_pct
    FROM public.scanner_events
    WHERE kind = 'validation_case'
)
SELECT
    market AS "시장",
    session AS "세션",
    strategy AS "전략",
    COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE') AS "완료 표본 수",
    COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND full_path_pass IS TRUE) AS "전체 경로 통과 수",
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND full_path_pass IS TRUE)
        / NULLIF(COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE'), 0),
        2
    ) AS "전체 경로 통과율(%)",
    COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND strict_pass IS TRUE) AS "엄격 통과 수",
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND strict_pass IS TRUE)
        / NULLIF(COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE'), 0),
        2
    ) AS "엄격 통과율(%)",
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND target_pass IS TRUE)
        / NULLIF(COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE'), 0),
        2
    ) AS "1차 목표 선도달률(%)",
    ROUND(AVG(net_return_pct) FILTER (WHERE data_completeness = 'COMPLETE'), 4) AS "평균 비용 반영 수익률(%)",
    CASE
        WHEN COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE') < 100 THEN '표본 누적 중'
        WHEN COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND strict_pass IS TRUE) >= 80
         AND 100.0 * COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND strict_pass IS TRUE)
             / NULLIF(COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE'), 0) >= 80
         AND AVG(net_return_pct) FILTER (WHERE data_completeness = 'COMPLETE') > 0 THEN '80% 기준 통과'
        ELSE '80% 기준 미통과'
    END AS "판정"
FROM base
GROUP BY market, session, strategy
ORDER BY "엄격 통과율(%)" DESC NULLS LAST, "완료 표본 수" DESC;


-- ================================================================
-- 3. 시간대별(5·10·15·30분) 예측 경로 성적표
--    전체 경로 실패가 어느 시간대에서 가장 많이 발생하는지 확인합니다.
-- ================================================================
WITH horizons AS (
    SELECT
        event.id AS event_id,
        event.payload ->> 'market' AS market,
        event.payload ->> 'session' AS session,
        event.payload ->> 'strategy' AS strategy,
        event.payload ->> 'data_completeness' AS data_completeness,
        horizon AS horizon
    FROM public.scanner_events AS event
    CROSS JOIN LATERAL jsonb_array_elements(COALESCE(event.payload -> 'horizons', '[]'::jsonb)) AS horizon
    WHERE event.kind = 'validation_case'
)
SELECT
    (horizon ->> 'minutes')::integer AS "예측 시간(분)",
    COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE') AS "완료 표본 수",
    COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND (horizon ->> 'range_pass')::boolean IS TRUE) AS "가격 범위 통과 수",
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND (horizon ->> 'range_pass')::boolean IS TRUE)
        / NULLIF(COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE'), 0),
        2
    ) AS "가격 범위 통과율(%)",
    COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND (horizon ->> 'direction_pass')::boolean IS TRUE) AS "방향 통과 수",
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND (horizon ->> 'direction_pass')::boolean IS TRUE)
        / NULLIF(COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE'), 0),
        2
    ) AS "방향 통과율(%)",
    COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND (horizon ->> 'pass_all')::boolean IS TRUE) AS "동시 통과 수",
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE' AND (horizon ->> 'pass_all')::boolean IS TRUE)
        / NULLIF(COUNT(*) FILTER (WHERE data_completeness = 'COMPLETE'), 0),
        2
    ) AS "시간대별 동시 통과율(%)"
FROM horizons
WHERE (horizon ->> 'minutes') IN ('5', '10', '15', '30')
GROUP BY (horizon ->> 'minutes')::integer
ORDER BY "예측 시간(분)";


-- ================================================================
-- 4. 엄격 전체 통과를 막은 실패 원인 분류
--    한 신호에 실패 원인이 여러 개면 각각 집계됩니다.
-- ================================================================
WITH base AS (
    SELECT
        payload ->> 'data_completeness' AS data_completeness,
        NULLIF(payload ->> 'quote_pass', '')::boolean AS quote_pass,
        NULLIF(payload ->> 'entry_executable', '')::boolean AS entry_executable,
        NULLIF(payload ->> 'structural_target_confirmed', '')::boolean AS structural_target_confirmed,
        NULLIF(payload ->> 'full_path_pass', '')::boolean AS full_path_pass,
        NULLIF(payload ->> 'regime_pass', '')::boolean AS regime_pass,
        NULLIF(payload ->> 'target_pass', '')::boolean AS target_pass,
        NULLIF(payload ->> 'complete_four_area_pass', '')::boolean AS strict_pass,
        payload ->> 'target_outcome' AS target_outcome
    FROM public.scanner_events
    WHERE kind = 'validation_case'
), failed_complete AS (
    SELECT *
    FROM base
    WHERE data_completeness = 'COMPLETE'
      AND strict_pass IS DISTINCT FROM TRUE
), reasons AS (
    SELECT failure_reason
    FROM failed_complete
    CROSS JOIN LATERAL (
        VALUES
            ('현재가 교차검증 실패', quote_pass IS DISTINCT FROM TRUE),
            ('체결 가능성 실패', entry_executable IS DISTINCT FROM TRUE),
            ('구조 목표 확인 실패', structural_target_confirmed IS DISTINCT FROM TRUE),
            ('5·10·15·30분 전체 경로 실패', full_path_pass IS DISTINCT FROM TRUE),
            ('장세 판정 실패', regime_pass IS DISTINCT FROM TRUE),
            ('1차 목표 선도달 실패: ' || COALESCE(target_outcome, '미판정'), target_pass IS DISTINCT FROM TRUE)
    ) AS item(failure_reason, is_failure)
    WHERE is_failure
)
SELECT
    failure_reason AS "실패 원인",
    COUNT(*) AS "실패 건수",
    ROUND(100.0 * COUNT(*) / NULLIF((SELECT COUNT(*) FROM failed_complete), 0), 2) AS "엄격 실패 표본 중 비중(%)"
FROM reasons
GROUP BY failure_reason
ORDER BY "실패 건수" DESC, "실패 원인";


-- ================================================================
-- 5. 최근 완료 표본 100건의 원본 감사 목록
--    기준을 사후에 바꾸지 않았는지 직접 점검할 때 사용합니다.
-- ================================================================
SELECT
    payload ->> 'signal_time' AS "신호 시각",
    payload ->> 'market' AS "시장",
    payload ->> 'session' AS "세션",
    payload ->> 'symbol' AS "종목",
    payload ->> 'strategy' AS "전략",
    payload ->> 'predicted_regime' AS "예측 장세",
    payload ->> 'actual_regime' AS "실제 장세",
    payload ->> 'full_path_pass' AS "5·10·15·30분 동시 통과",
    payload ->> 'target_outcome' AS "목표·손절 결과",
    payload ->> 'complete_four_area_pass' AS "엄격 전체 통과",
    payload ->> 'net_return_pct' AS "비용 반영 수익률(%)",
    payload -> 'horizons' AS "시간대별 원본"
FROM public.scanner_events
WHERE kind = 'validation_case'
  AND payload ->> 'data_completeness' = 'COMPLETE'
ORDER BY created_at DESC
LIMIT 100;
