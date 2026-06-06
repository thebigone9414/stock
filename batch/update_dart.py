#!/usr/bin/env python3
"""
DART 재무 데이터 배치 업데이트 (분기 1회 권장)

[수집 항목]
  분기별 주당순이익(EPS): 최근 2개 연도 × 각 분기
  연간 주당순이익(EPS):   최근 3개 연도
  연간 매출액:            최근 3개 연도

[C·A 사전 필터링]
  전체 수집 완료 후 C·A 조건 통과 종목만 data/dart_ca_screened.json 에 저장
  → 이후 canslim-batch 는 이 목록만 사용 (일일 API 호출 최소화)

  C: 최근 분기 EPS YoY +25% 이상
  A: 최근 3년 연간 EPS CAGR +15% 이상

[실행 시기]
  분기 보고서 공시 완료 후 수동 트리거:
    Q1: 5월 중순  Q2(반기): 8월 중순  Q3: 11월 중순  Annual: 다음해 3월 말

GitHub Actions: dart-batch 모드 (수동 workflow_dispatch)
필수 Secret: DART_API_KEY (https://opendart.fss.or.kr)
"""
import sys
import time
from datetime import datetime
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from config.settings import get_settings
from utils.logger import setup_logger
from data.dart_client import DARTClient, REPRT_Q1, REPRT_H1, REPRT_Q3, REPRT_ANN
from data.canslim_universe import CANSLIM_UNIVERSE
import data.dart_store as dart_store
import data.canslim_store as canslim_store

KST = pytz.timezone("Asia/Seoul")

# DART API: 초당 10건 이하 공식 가이드라인
DART_RATE_SLEEP = 0.11


def _current_quarters(n_years: int = 2) -> list:
    """현재 시점에서 수집할 (year, quarter, reprt_code) 목록"""
    now   = datetime.now(KST)
    year  = now.year
    month = now.month

    if month >= 11:
        latest = [(year, 3, REPRT_Q3), (year, 2, REPRT_H1),
                  (year, 1, REPRT_Q1), (year - 1, 4, REPRT_ANN)]
    elif month >= 8:
        latest = [(year, 2, REPRT_H1), (year, 1, REPRT_Q1),
                  (year - 1, 4, REPRT_ANN), (year - 1, 3, REPRT_Q3)]
    elif month >= 5:
        latest = [(year, 1, REPRT_Q1), (year - 1, 4, REPRT_ANN),
                  (year - 1, 3, REPRT_Q3), (year - 1, 2, REPRT_H1)]
    else:
        latest = [(year - 1, 4, REPRT_ANN), (year - 1, 3, REPRT_Q3),
                  (year - 1, 2, REPRT_H1), (year - 1, 1, REPRT_Q1)]

    result = list(latest)
    prev_year = latest[-1][0] - 1
    result += [
        (prev_year, 4, REPRT_ANN),
        (prev_year, 3, REPRT_Q3),
        (prev_year, 2, REPRT_H1),
        (prev_year, 1, REPRT_Q1),
    ]
    return result[:8]


def _annual_years(n: int = 3) -> list:
    now  = datetime.now(KST)
    base = now.year - 1 if now.month < 4 else now.year
    return [(y, REPRT_ANN) for y in range(base, base - n, -1)]


def run_dart_batch(dart_client: DARTClient) -> None:
    today     = datetime.now(KST).strftime("%Y-%m-%d")
    quarters  = _current_quarters(n_years=2)
    ann_years = _annual_years(n=3)

    n_stocks = len(CANSLIM_UNIVERSE)
    logger.info("══════════════════════════════════════════")
    logger.info(f" DART 배치 시작 [{today}]  대상: {n_stocks}종목")
    logger.info(f" 분기 범위: {quarters[0][:2]} ~ {quarters[-1][:2]}")
    logger.info(f" 연간 범위: {[y for y, _ in ann_years]}")
    logger.info("══════════════════════════════════════════")

    corp_map      = dart_client.get_corp_map()
    existing_data = dart_store.load()
    corps_out     = existing_data.get("corps", {})
    ok, fail, skip = 0, 0, 0

    for i, stock in enumerate(CANSLIM_UNIVERSE, 1):
        code   = stock["code"]
        name   = stock["name"]
        sector = stock.get("sector", "")

        corp_code = corp_map.get(code)
        if not corp_code:
            logger.warning(f"[{i:03d}/{n_stocks}] [{code}] {name} corp_code 없음 — 건너뜀")
            skip += 1
            continue

        try:
            # ── 분기 EPS ───────────────────────────────────────────
            quarterly_eps = []
            for qyear, qnum, reprt_code in quarters:
                time.sleep(DART_RATE_SLEEP)
                eps = dart_client.get_eps(corp_code, str(qyear), reprt_code)
                quarterly_eps.append({"year": qyear, "quarter": qnum, "eps": eps})

            # ── 연간 EPS · 매출 ────────────────────────────────────
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

            latest_q_eps = next((q["eps"] for q in quarterly_eps if q["eps"] is not None), None)
            latest_a_eps = next((a["eps"] for a in annual_eps    if a["eps"] is not None), None)
            logger.info(
                f"[{i:03d}/{n_stocks}] [{code}] {name:14s}  "
                f"분기EPS={latest_q_eps}  연간EPS={latest_a_eps}"
            )
            ok += 1

        except Exception as e:
            logger.warning(f"[{i:03d}/{n_stocks}] [{code}] {name} 실패: {e}")
            fail += 1

    # ── dart_data.json 저장 ────────────────────────────────────────────
    existing_data["updated_at"] = today
    existing_data["corps"]      = corps_out
    dart_store.save(existing_data)

    # ── C·A 사전 필터링 → dart_ca_screened.json ───────────────────────
    screened = _filter_ca(corps_out, today)

    dart_store.git_commit_push(
        [str(dart_store.DART_DATA_PATH),
         str(canslim_store.CANSLIM_CA_SCREENED_PATH)],
        f"data: DART 재무 배치 {today} ({ok}/{n_stocks}종목, CA통과:{len(screened)}종목)",
    )

    logger.info("══════════════════════════════════════════")
    logger.info(f" DART 배치 완료: 성공:{ok} / 실패:{fail} / 건너뜀:{skip}")
    logger.info(f" C·A 통과 종목: {len(screened)}개 → dart_ca_screened.json 저장")
    if screened:
        logger.info(" (이후 canslim-batch 는 이 목록만 조회)")
        for s in screened[:10]:
            logger.info(
                f"  [{s['code']}] {s['name']:14s}  "
                f"C:{int(s['C'])} A:{int(s['A'])}  "
                f"분기EPS={s.get('latest_q_eps')}  연간EPS={s.get('latest_a_eps')}"
            )
        if len(screened) > 10:
            logger.info(f"  ... 외 {len(screened) - 10}종목")
    else:
        logger.info(" C·A 통과 종목 없음 — 임계값 재검토 필요")
    logger.info("══════════════════════════════════════════")


def _filter_ca(corps_out: dict, today: str) -> list:
    """C·A 조건 통과 종목 필터링 → canslim_store 에 저장 후 리스트 반환"""
    screened = []
    for code, corp in corps_out.items():
        C = canslim_store.check_C(corp)
        A = canslim_store.check_A(corp)
        if not (C or A):       # 둘 다 False 면 제외 (C만 or A만 통과도 포함)
            continue
        latest_q_eps = next(
            (q["eps"] for q in corp.get("quarterly_eps", []) if q["eps"] is not None), None
        )
        latest_a_eps = next(
            (a["eps"] for a in corp.get("annual_eps", [])    if a["eps"] is not None), None
        )
        screened.append({
            "code":          code,
            "name":          corp.get("name", ""),
            "sector":        corp.get("sector", ""),
            "C":             C,
            "A":             A,
            "latest_q_eps":  latest_q_eps,
            "latest_a_eps":  latest_a_eps,
        })

    # C+A 모두 통과 우선, 그 다음 C만 통과, A만 통과 순
    screened.sort(key=lambda x: (x["C"] and x["A"], x["C"], x["A"]), reverse=True)

    canslim_store.save_ca_screened({
        "updated_at": today,
        "screened":   screened,
    })
    return screened


if __name__ == "__main__":
    settings = get_settings()
    setup_logger(settings.log_level)

    import os
    dart_key = os.environ.get("DART_API_KEY", "")
    if not dart_key:
        logger.error("[DART배치] DART_API_KEY 환경변수 없음 — 종료")
        sys.exit(1)

    logger.info("=== DART 재무 배치 ===")
    client = DARTClient(dart_key)
    try:
        run_dart_batch(client)
    except Exception as _e:
        logger.exception(f"[DART배치] 예외 발생: {_e}")
        sys.exit(1)
