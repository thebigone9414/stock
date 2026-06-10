#!/usr/bin/env python3
"""
DART 재무 데이터 배치 — KOSPI200 + KOSDAQ150 대상 (분기 1회)

[유니버스]
  data/watchlist.py 의 get_s2_watchlist() → KOSPI200 + KOSDAQ150 (~350종목)
  전체 상장법인 대비 1/10 수준 → 소요 시간 대폭 단축

[효율적 처리 — 조기 탈출(Early Bail)]
  1단계: 최근 연간 당기순이익 조회 (1 API call)
         → None 이면 실적 없음 → 즉시 건너뜀
  2단계: 실적 있으면 분기 × 8 + 연간 × 3 = 11 call 풀 조회

[C·A 사전 필터링]
  전체 수집 완료 후 C·A 조건 통과 종목을 data/dart_ca_screened.json 저장
  → CANSLIM 배치(S3)에서 이 목록만 N·S·L·I·M 계산

[예상 소요 시간]
  ~350종목 × 평균 3call × 2s = ~35분 (GitHub Actions timeout=120분 설정)

[실행 시기]
  분기 보고서 공시 완료 후 수동 트리거:
    Q1: 5월 중순  Q2(반기): 8월 중순  Q3: 11월 중순  Annual: 다음해 3월 말

필수 Secret: DART_API_KEY (https://opendart.fss.or.kr)
"""
import sys
import time
from datetime import datetime
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from utils.logger import setup_logger
from data.dart_client import DARTClient, REPRT_Q1, REPRT_H1, REPRT_Q3, REPRT_ANN
from data.watchlist import get_s2_watchlist
import data.dart_store as dart_store
import data.canslim_store as canslim_store

KST             = pytz.timezone("Asia/Seoul")
DART_RATE_SLEEP = 0.11   # 초당 ~9건 (공식 한도 10건/초 + 여유)


def _current_quarters(n_years: int = 2) -> list:
    """현재 시점에서 수집 가능한 분기 목록 [(year, quarter_num, reprt_code)]

    C 조건(분기 EPS YoY) 계산을 위해 최근 4분기 + 그 직전 연도 동분기 포함.
    예) 2026-06: 2026Q1·2025Q4·Q3·Q2 + 2025Q1·2024Q4·Q3·Q2
    """
    now   = datetime.now(KST)
    year  = now.year
    month = now.month

    if month >= 11:
        recent = [(year, 3, REPRT_Q3), (year, 2, REPRT_H1),
                  (year, 1, REPRT_Q1), (year - 1, 4, REPRT_ANN)]
    elif month >= 8:
        recent = [(year, 2, REPRT_H1), (year, 1, REPRT_Q1),
                  (year - 1, 4, REPRT_ANN), (year - 1, 3, REPRT_Q3)]
    elif month >= 5:
        recent = [(year, 1, REPRT_Q1), (year - 1, 4, REPRT_ANN),
                  (year - 1, 3, REPRT_Q3), (year - 1, 2, REPRT_H1)]
    else:
        recent = [(year - 1, 4, REPRT_ANN), (year - 1, 3, REPRT_Q3),
                  (year - 1, 2, REPRT_H1), (year - 1, 1, REPRT_Q1)]

    # YoY 비교용: 최근 4분기의 전년도 동분기 추가
    year_ago = [(y - 1, q, rc) for y, q, rc in recent]

    seen, result = set(), []
    for item in recent + year_ago:
        key = (item[0], item[1])
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result[:8]


def _annual_years(n: int = 3) -> list:
    now  = datetime.now(KST)
    # 사업보고서(11011)는 전년도 기준 (당해연도 보고서는 다음해 3월에 공시)
    base = now.year - 1 if now.month >= 4 else now.year - 2
    return [(y, REPRT_ANN) for y in range(base, base - n, -1)]


def run_dart_batch(dart_client: DARTClient) -> None:
    today     = datetime.now(KST).strftime("%Y-%m-%d")
    quarters  = _current_quarters(n_years=2)
    ann_years = _annual_years(n=3)

    # ── 유니버스: KOSPI200 + KOSDAQ150 ────────────────────────────────
    universe = get_s2_watchlist()
    n_total  = len(universe)

    logger.info("══════════════════════════════════════════")
    logger.info(f" DART 배치 시작 [{today}]")
    logger.info(f" 유니버스: KOSPI200 + KOSDAQ150 = {n_total}종목")
    logger.info(f" 분기 범위: {quarters[0][:2]} ~ {quarters[-1][:2]}")
    logger.info(f" 연간 범위: {[y for y, _ in ann_years]}")
    logger.info("══════════════════════════════════════════")

    existing_data = dart_store.load()
    corps_out     = existing_data.get("corps", {})

    # 최신 연간 (Early Bail 판단 기준)
    latest_ann_year, latest_ann_reprt = ann_years[0]

    ok, bail_out, fail, skip = 0, 0, 0, 0

    for i, stock in enumerate(universe, 1):
        code      = stock["code"]
        name      = stock["name"]
        sector    = stock.get("sector", "기타")
        corp_code = dart_client.get_corp_code(code)

        if not corp_code:
            skip += 1
            logger.debug(f"[{i:03d}/{n_total}] [{code}] {name} — corp_code 없음, 건너뜀")
            continue

        try:
            # ── 1단계: 연간 순이익 존재 여부 확인 (Early Bail) ──────────
            time.sleep(DART_RATE_SLEEP)
            eps_check = dart_client.get_eps(
                corp_code, str(latest_ann_year), latest_ann_reprt
            )
            if eps_check is None:
                bail_out += 1
                logger.debug(f"[{i:03d}/{n_total}] [{code}] {name} — 실적 없음(bail)")
                continue

            # ── 2단계: 실적 있는 종목 — 전체 분기·연간 데이터 조회 ─────
            quarterly_eps = []
            for qyear, qnum, reprt_code in quarters:
                time.sleep(DART_RATE_SLEEP)
                eps = dart_client.get_eps(corp_code, str(qyear), reprt_code)
                quarterly_eps.append({"year": qyear, "quarter": qnum, "eps": eps})

            annual_eps = []
            annual_rev = []
            for ayear, reprt_code in ann_years:
                time.sleep(DART_RATE_SLEEP)
                eps = dart_client.get_eps(corp_code, str(ayear), reprt_code)
                time.sleep(DART_RATE_SLEEP)
                rev = dart_client.get_revenue(corp_code, str(ayear), reprt_code)
                annual_eps.append({"year": ayear, "eps": eps})
                annual_rev.append({"year": ayear, "rev": rev})

            corps_out[code] = {
                "corp_code":     corp_code,
                "name":          name,
                "sector":        sector,
                "quarterly_eps": quarterly_eps,
                "annual_eps":    annual_eps,
                "annual_rev":    annual_rev,
            }
            ok += 1

            lq_eps = next((q["eps"] for q in quarterly_eps if q["eps"] is not None), None)
            la_eps = next((a["eps"] for a in annual_eps    if a["eps"] is not None), None)
            logger.info(
                f"[{i:03d}/{n_total}] [{code}] {name:14s}  "
                f"분기EPS={lq_eps}  연간EPS={la_eps}  "
                f"(OK={ok} bail={bail_out})"
            )

        except Exception as e:
            fail += 1
            logger.warning(f"[{i:03d}/{n_total}] [{code}] {name} 실패: {e}")

    # ── dart_data.json 저장 ────────────────────────────────────────────
    existing_data["updated_at"] = today
    existing_data["corps"]      = corps_out
    # 이전 실행의 bail 캐시 초기화 (유니버스가 변경됨)
    existing_data.pop("bail_date",  None)
    existing_data.pop("bail_codes", None)
    dart_store.save(existing_data)

    # ── C·A 사전 필터링 → dart_ca_screened.json ───────────────────────
    screened = _filter_ca(corps_out, today)

    dart_store.git_commit_push(
        [str(dart_store.DART_DATA_PATH),
         str(canslim_store.CANSLIM_CA_SCREENED_PATH)],
        (f"data: DART 배치 {today} "
         f"(실적:{ok}/{n_total}종목, 탈출:{bail_out}, C·A통과:{len(screened)})"),
    )

    logger.info("══════════════════════════════════════════")
    logger.info(
        f" DART 배치 완료: 실적있음={ok} / 조기탈출={bail_out} / "
        f"실패={fail} / 건너뜀={skip} / 전체={n_total}"
    )
    logger.info(f" C·A 통과 종목: {len(screened)}개 → dart_ca_screened.json")
    if screened:
        ca_both = [s for s in screened if s["C"] and s["A"]]
        c_only  = [s for s in screened if s["C"] and not s["A"]]
        a_only  = [s for s in screened if not s["C"] and s["A"]]
        logger.info(f"   C+A 모두:{len(ca_both)}개  C만:{len(c_only)}개  A만:{len(a_only)}개")
        for s in screened[:10]:
            ca = "C+A" if (s["C"] and s["A"]) else ("C" if s["C"] else "A")
            logger.info(
                f"   [{s['code']}] {s['name']:14s}  {ca}  "
                f"분기EPS={s.get('latest_q_eps')}  연간EPS={s.get('latest_a_eps')}"
            )
        if len(screened) > 10:
            logger.info(f"   ... 외 {len(screened) - 10}개")
    else:
        logger.warning(" C·A 통과 종목 없음! DART 데이터 품질 또는 임계값 확인 필요")
    logger.info("══════════════════════════════════════════")


def _filter_ca(corps_out: dict, today: str) -> list:
    """C 또는 A 조건 통과 종목 필터링 → canslim_store 저장 후 리스트 반환"""
    screened = []
    for code, corp in corps_out.items():
        C = canslim_store.check_C(corp)
        A = canslim_store.check_A(corp)
        if not (C or A):
            continue
        lq_eps = next((q["eps"] for q in corp.get("quarterly_eps", []) if q["eps"] is not None), None)
        la_eps = next((a["eps"] for a in corp.get("annual_eps",    []) if a["eps"] is not None), None)
        screened.append({
            "code":         code,
            "name":         corp.get("name", ""),
            "sector":       corp.get("sector", "기타"),
            "C":            C,
            "A":            A,
            "latest_q_eps": lq_eps,
            "latest_a_eps": la_eps,
        })

    screened.sort(key=lambda x: (x["C"] and x["A"], x["C"], x["A"]), reverse=True)
    canslim_store.save_ca_screened({"updated_at": today, "screened": screened})
    return screened


if __name__ == "__main__":
    import os
    setup_logger(os.environ.get("LOG_LEVEL", "INFO"))

    dart_key = os.environ.get("DART_API_KEY", "")
    if not dart_key:
        logger.error("[DART배치] DART_API_KEY 환경변수 없음 — 종료")
        sys.exit(1)

    logger.info("=== DART 재무 배치 (KOSPI200 + KOSDAQ150) ===")
    client = DARTClient(dart_key)
    try:
        run_dart_batch(client)
    except Exception as _e:
        logger.exception(f"[DART배치] 예외 발생: {_e}")
        sys.exit(1)
