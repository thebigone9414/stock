#!/usr/bin/env python3
"""
DART 재무 데이터 배치 — 전체 상장 종목 대상 (분기 1회)

[유니버스]
  DART corpCode.xml 의 전체 상장법인 (~2,500종목)
  → 이름 패턴 제외: 스팩, 우선주, 리츠, ETF, 인프라펀드
  → 수동 제외: data/canslim_exclusions.py

[효율적 처리 — 조기 탈출(Early Bail)]
  1단계: 최근 연간 EPS 조회 (1 API call)
         → None 이면 실적 없음 → 즉시 건너뜀 (ETF, 스팩, 무실적 기업 자연 제거)
  2단계: 실적 있으면 분기 × 8 + 연간 × 3 = 11 call 풀 조회

[C·A 사전 필터링]
  전체 수집 완료 후 C·A 조건 통과 종목을 data/dart_ca_screened.json 저장
  → 이후 canslim-batch 는 이 목록(예상 50~150종목)만 N·S·L·I·M 계산

[예상 소요 시간]
  실적 없는 종목(~1,800): call 1개 × 0.3s = ~9분
  실적 있는 종목(~700): call 11개 × 0.35s = ~45분
  합계: ~54분 (GitHub Actions timeout=120분 설정)

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

from config.settings import get_settings
from utils.logger import setup_logger
from data.dart_client import DARTClient, REPRT_Q1, REPRT_H1, REPRT_Q3, REPRT_ANN
from data.canslim_exclusions import should_exclude
import data.dart_store as dart_store
import data.canslim_store as canslim_store

KST             = pytz.timezone("Asia/Seoul")
DART_RATE_SLEEP = 0.11   # 초당 ~9건 (공식 한도 10건/초 + 여유)


def _current_quarters(n_years: int = 2) -> list:
    """현재 시점에서 수집 가능한 분기 목록 [(year, quarter_num, reprt_code)]"""
    now   = datetime.now(KST)
    year  = now.year
    month = now.month

    if month >= 11:
        base = [(year, 3, REPRT_Q3), (year, 2, REPRT_H1),
                (year, 1, REPRT_Q1), (year - 1, 4, REPRT_ANN)]
    elif month >= 8:
        base = [(year, 2, REPRT_H1), (year, 1, REPRT_Q1),
                (year - 1, 4, REPRT_ANN), (year - 1, 3, REPRT_Q3)]
    elif month >= 5:
        base = [(year, 1, REPRT_Q1), (year - 1, 4, REPRT_ANN),
                (year - 1, 3, REPRT_Q3), (year - 1, 2, REPRT_H1)]
    else:
        base = [(year - 1, 4, REPRT_ANN), (year - 1, 3, REPRT_Q3),
                (year - 1, 2, REPRT_H1), (year - 1, 1, REPRT_Q1)]

    result    = list(base)
    prev_year = base[-1][0] - 1
    result   += [(prev_year, 4, REPRT_ANN), (prev_year, 3, REPRT_Q3),
                 (prev_year, 2, REPRT_H1), (prev_year, 1, REPRT_Q1)]
    return result[:8]


def _annual_years(n: int = 3) -> list:
    now  = datetime.now(KST)
    base = now.year - 1 if now.month < 4 else now.year
    return [(y, REPRT_ANN) for y in range(base, base - n, -1)]


def run_dart_batch(dart_client: DARTClient) -> None:
    today     = datetime.now(KST).strftime("%Y-%m-%d")
    quarters  = _current_quarters(n_years=2)
    ann_years = _annual_years(n=3)

    # ── 유니버스 구성 ──────────────────────────────────────────────────
    all_stocks  = dart_client.get_all_listed_stocks()
    universe    = []
    skip_reason = {}

    for s in all_stocks:
        excluded, reason = should_exclude(s["code"], s["name"])
        if excluded:
            skip_reason[reason] = skip_reason.get(reason, 0) + 1
        else:
            universe.append(s)

    n_total = len(universe)
    logger.info("══════════════════════════════════════════")
    logger.info(f" DART 배치 시작 [{today}]")
    logger.info(f" 전체 상장법인: {len(all_stocks):,}개")
    logger.info(f" 이름패턴 제외: {dict(sorted(skip_reason.items(), key=lambda x: -x[1]))}")
    logger.info(f" 처리 대상: {n_total:,}개")
    logger.info(f" 분기 범위: {quarters[0][:2]} ~ {quarters[-1][:2]}")
    logger.info(f" 연간 범위: {[y for y, _ in ann_years]}")
    logger.info("══════════════════════════════════════════")

    existing_data = dart_store.load()
    corps_out     = existing_data.get("corps", {})

    # 최신 연간 (Early Bail 판단 기준)
    latest_ann_year, latest_ann_reprt = ann_years[0]

    ok, bail_out, fail, skip = 0, 0, 0, 0
    log_interval = max(1, n_total // 20)   # 5% 단위 진행 로그

    for i, stock in enumerate(universe, 1):
        code      = stock["code"]
        name      = stock["name"]
        corp_code = dart_client.get_corp_code(code)

        if not corp_code:
            skip += 1
            continue

        try:
            # ── 1단계: 연간 EPS 존재 여부 확인 (Early Bail) ──────────
            time.sleep(DART_RATE_SLEEP)
            eps_check = dart_client.get_eps(
                corp_code, str(latest_ann_year), latest_ann_reprt
            )
            if eps_check is None:
                bail_out += 1
                if i % log_interval == 0:
                    logger.info(
                        f"[{i:04d}/{n_total}] 진행: OK={ok} 조기탈출={bail_out} "
                        f"실패={fail} 건너뜀={skip}"
                    )
                continue

            # ── 2단계: 실적 있는 종목 — 전체 분기·연간 EPS 조회 ─────
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

            # 섹터 힌트: 기존 캐시에서 유지, 없으면 "기타"
            prev_sector = corps_out.get(code, {}).get("sector", "기타")

            corps_out[code] = {
                "corp_code":     corp_code,
                "name":          name,
                "sector":        prev_sector,
                "quarterly_eps": quarterly_eps,
                "annual_eps":    annual_eps,
                "annual_rev":    annual_rev,
            }
            ok += 1

            if i % log_interval == 0 or ok <= 20:
                lq_eps = next((q["eps"] for q in quarterly_eps if q["eps"] is not None), None)
                la_eps = next((a["eps"] for a in annual_eps    if a["eps"] is not None), None)
                logger.info(
                    f"[{i:04d}/{n_total}] [{code}] {name:14s}  "
                    f"분기EPS={lq_eps}  연간EPS={la_eps}  "
                    f"(OK={ok} 탈출={bail_out})"
                )

        except Exception as e:
            fail += 1
            logger.debug(f"[{i:04d}/{n_total}] [{code}] {name} 실패: {e}")

    # ── dart_data.json 저장 ────────────────────────────────────────────
    existing_data["updated_at"] = today
    existing_data["corps"]      = corps_out
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
        f"실패={fail} / 건너뜀={skip}"
    )
    logger.info(f" C·A 통과 종목: {len(screened)}개 → dart_ca_screened.json")
    if screened:
        ca_both = [s for s in screened if s["C"] and s["A"]]
        c_only  = [s for s in screened if s["C"] and not s["A"]]
        a_only  = [s for s in screened if not s["C"] and s["A"]]
        logger.info(f"   C+A 모두:{len(ca_both)}개  C만:{len(c_only)}개  A만:{len(a_only)}개")
        logger.info(" 상위 10개:")
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
            "code":          code,
            "name":          corp.get("name", ""),
            "sector":        corp.get("sector", "기타"),
            "C":             C,
            "A":             A,
            "latest_q_eps":  lq_eps,
            "latest_a_eps":  la_eps,
        })

    # C+A 모두 통과 우선 정렬
    screened.sort(key=lambda x: (x["C"] and x["A"], x["C"], x["A"]), reverse=True)

    canslim_store.save_ca_screened({"updated_at": today, "screened": screened})
    return screened


if __name__ == "__main__":
    settings = get_settings()
    setup_logger(settings.log_level)

    import os
    dart_key = os.environ.get("DART_API_KEY", "")
    if not dart_key:
        logger.error("[DART배치] DART_API_KEY 환경변수 없음 — 종료")
        sys.exit(1)

    logger.info("=== DART 재무 배치 (전체 상장 종목) ===")
    client = DARTClient(dart_key)
    try:
        run_dart_batch(client)
    except Exception as _e:
        logger.exception(f"[DART배치] 예외 발생: {_e}")
        sys.exit(1)
