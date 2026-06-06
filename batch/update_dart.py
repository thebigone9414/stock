#!/usr/bin/env python3
"""
DART 재무 데이터 배치 업데이트 (분기 1회 권장)

수집 항목:
  - 분기별 주당순이익(EPS): 최근 2개 연도 × 4분기
  - 연간 주당순이익(EPS):   최근 3개 연도
  - 연간 매출액:            최근 3개 연도

동작:
  1. CANSLIM 유니버스 전 종목에 대해 corp_code 확보 (corpCode.xml 다운로드)
  2. DART API로 재무제표 호출 (초당 10건 이하 rate-limit)
  3. data/dart_data.json 저장 및 git commit/push

실행 시기: 분기 보고서 공시 완료 후 (Q1: 5월 중순, Q2: 8월 중순, Q3: 11월 중순, Annual: 3월 말)
GitHub Actions: dart-batch 모드 수동 트리거
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

KST = pytz.timezone("Asia/Seoul")

# DART API 초당 10건 이하 (공식 가이드라인)
DART_RATE_SLEEP = 0.12   # 초간격 (10건/초 = 0.1s + 여유)


def _current_quarters(n_years: int = 2) -> list:
    """현재 시점에서 수집할 (year, quarter, reprt_code) 목록 반환

    분기 보고서 공시 시기:
      Q1(11013): 5월 15일 이후 공시
      H1(11012): 8월 14일 이후
      Q3(11014): 11월 14일 이후
      Ann(11011): 다음해 3월 31일 이후
    """
    now    = datetime.now(KST)
    year   = now.year
    month  = now.month

    # 현재 조회 가능한 최신 분기 결정
    if month >= 11:
        latest = [(year, 3, REPRT_Q3), (year, 2, REPRT_H1), (year, 1, REPRT_Q1),
                  (year - 1, 4, REPRT_ANN)]
    elif month >= 8:
        latest = [(year, 2, REPRT_H1), (year, 1, REPRT_Q1),
                  (year - 1, 4, REPRT_ANN), (year - 1, 3, REPRT_Q3)]
    elif month >= 5:
        latest = [(year, 1, REPRT_Q1), (year - 1, 4, REPRT_ANN),
                  (year - 1, 3, REPRT_Q3), (year - 1, 2, REPRT_H1)]
    else:
        latest = [(year - 1, 4, REPRT_ANN), (year - 1, 3, REPRT_Q3),
                  (year - 1, 2, REPRT_H1), (year - 1, 1, REPRT_Q1)]

    # 2개 연도치 (최대 8개 분기 항목)
    result = list(latest)
    if len(result) < 4 * n_years:
        # 한 해 더 추가 (이전 연도 4개 분기)
        prev_year = latest[-1][0] - 1
        result += [
            (prev_year, 4, REPRT_ANN),
            (prev_year, 3, REPRT_Q3),
            (prev_year, 2, REPRT_H1),
            (prev_year, 1, REPRT_Q1),
        ]
    return result[:8]


def _annual_years(n: int = 3) -> list:
    """연간 보고서 연도 목록 (최근 n개 연도)"""
    now  = datetime.now(KST)
    year = now.year
    # Annual 보고서는 전년도까지 확정 (당해년도 사업보고서는 다음 3월 공시)
    base = year - 1 if now.month < 4 else year
    return [(y, REPRT_ANN) for y in range(base, base - n, -1)]


def run_dart_batch(dart_client: DARTClient) -> None:
    today     = datetime.now(KST).strftime("%Y-%m-%d")
    quarters  = _current_quarters(n_years=2)
    ann_years = _annual_years(n=3)

    logger.info("══════════════════════════════════════════")
    logger.info(f" DART 배치 시작 [{today}]")
    logger.info(f" 대상: {len(CANSLIM_UNIVERSE)}종목")
    logger.info(f" 분기 범위: {quarters[0][:2]} ~ {quarters[-1][:2]}")
    logger.info(f" 연간 범위: {[y for y, _ in ann_years]}")
    logger.info("══════════════════════════════════════════")

    # corp_code 맵 다운로드 (1회)
    corp_map = dart_client.get_corp_map()

    existing_data = dart_store.load()
    corps_out     = existing_data.get("corps", {})
    ok, fail, skip = 0, 0, 0

    for i, stock in enumerate(CANSLIM_UNIVERSE, 1):
        code = stock["code"]
        name = stock["name"]

        corp_code = corp_map.get(code)
        if not corp_code:
            logger.warning(f"[{i:03d}/{len(CANSLIM_UNIVERSE)}] [{code}] {name} corp_code 없음 — 건너뜀")
            skip += 1
            continue

        try:
            # ── 분기 EPS ───────────────────────────────────────────
            quarterly_eps = []
            for qyear, qnum, reprt_code in quarters:
                time.sleep(DART_RATE_SLEEP)
                eps = dart_client.get_eps(corp_code, str(qyear), reprt_code)
                quarterly_eps.append({
                    "year":    qyear,
                    "quarter": qnum,
                    "eps":     eps,
                })
                logger.debug(f"  [{code}] {qyear}Q{qnum}: EPS={eps}")

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
                logger.debug(f"  [{code}] {ayear}Annual: EPS={eps}, Rev={rev}")

            corps_out[code] = {
                "corp_code":     corp_code,
                "name":          name,
                "quarterly_eps": quarterly_eps,
                "annual_eps":    annual_eps,
                "annual_rev":    annual_rev,
            }

            # 간단 로그
            latest_q_eps = next((q["eps"] for q in quarterly_eps if q["eps"] is not None), None)
            latest_a_eps = next((a["eps"] for a in annual_eps   if a["eps"] is not None), None)
            logger.info(
                f"[{i:03d}/{len(CANSLIM_UNIVERSE)}] [{code}] {name:14s}  "
                f"최근분기EPS={latest_q_eps}  최근연간EPS={latest_a_eps}"
            )
            ok += 1

        except Exception as e:
            logger.warning(f"[{i:03d}/{len(CANSLIM_UNIVERSE)}] [{code}] {name} 실패: {e}")
            fail += 1

    existing_data["updated_at"] = today
    existing_data["corps"]      = corps_out
    dart_store.save(existing_data)

    dart_store.git_commit_push(
        [str(dart_store.DART_DATA_PATH)],
        f"data: DART 재무 배치 {today} ({ok}/{len(CANSLIM_UNIVERSE)}종목)",
    )

    logger.info("══════════════════════════════════════════")
    logger.info(f" DART 배치 완료: 성공:{ok} / 실패:{fail} / 건너뜀:{skip}")
    logger.info("══════════════════════════════════════════")


if __name__ == "__main__":
    settings = get_settings()
    setup_logger(settings.log_level)

    dart_key = __import__("os").environ.get("DART_API_KEY", "")
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
