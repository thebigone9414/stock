"""
CANSLIM 전략 제외 종목 관리

[자동 제외 — 이름 기반 (update_dart.py 에서 처리)]
  스팩(SPAC), 우선주, 리츠, ETF, 인프라펀드 등

[자동 제외 — 실적 기반]
  DART에서 연간 EPS 없음 → 조기 탈출 (ETF, 무실적 기업 자연 제거)
  가격 < 10,000원 → canslim-batch 에서 OHLCV 조회 시 필터링

[수동 제외 — EXCLUDED_CODES]
  매매정지, 불성실공시, 관리종목, 투자주의 환기종목 등
  거래소 공시 확인 후 아래 목록에 직접 추가:
    https://kind.krx.co.kr/corpgeneral/unfairnotice.do (불성실공시법인 조회)
    https://kind.krx.co.kr/corpgeneral/tradehaltinfo.do (매매정지 현황)

[CANSLIM 성격 맞지 않는 업종]
  O'Neil이 강조한 CANSLIM 대상:
    ✓ 고성장 기술/바이오/소비재 — EPS가 YoY 두 자릿수 이상 성장
    ✓ 시장 점유율 확대 중인 신생 기업 또는 카테고리 킬러
  자연 필터링되는 업종 (C·A 조건을 통과하기 어려움):
    - 은행/보험/금융지주: 대손충당금, NIM 구조로 EPS 변동성 왜곡
    - 유틸리티(한국전력, SK가스 등): 규제 산업, 정책 의존 → 낮은 성장률
    - 원자재/정유(S-Oil 등): 유가·원자재가 의존적 EPS → CAGR 신뢰 부족
    - 전통 소매/유통: 저마진 성숙 산업
    - 지주사(삼성물산 등): 자체 이익 미미, 순자산 기반 가치
    → 위 업종들은 C·A 필터 통과가 어려워 자동 제거됨
"""
from typing import Set

# ── 수동 제외 종목 코드 ─────────────────────────────────────────────────
# 형식: "코드",   # 회사명 - 제외 사유 (조치일 포함 권장)
EXCLUDED_CODES: Set[str] = {
    # 예시:
    # "999999",   # 샘플회사 - 불성실공시법인 지정 (2025-01)
    # "888888",   # 샘플회사2 - 상장폐지 사유 발생 (2025-03)
}

# ── 이름 기반 자동 제외 ─────────────────────────────────────────────────

# 이름에 포함되면 제외
EXCLUDE_NAME_CONTAINS: tuple = (
    "스팩",       # SPAC (기업인수목적회사)
    "리츠",       # REITs
    "인프라",     # 인프라펀드
    "REIT",
    "ETF",
    "특수목적",   # 특수목적법인
    "투자신탁",
    "자산운용",
)

# 이름 끝이 이것으로 끝나면 제외 (우선주: 삼성전자우, 현대차우B 등)
EXCLUDE_NAME_ENDSWITH: tuple = (
    "우",         # 우선주 (삼성전자우, 현대차우 ...)
    "우B",
    "우C",
    "1우",
    "2우",
    "3우",
)

# ── 헬퍼 함수 ──────────────────────────────────────────────────────────

def should_exclude(code: str, name: str) -> tuple:
    """True 반환 시 제외. 반환값: (제외여부, 사유)"""
    if code in EXCLUDED_CODES:
        return True, "수동제외목록"
    nm = name.strip()
    for kw in EXCLUDE_NAME_CONTAINS:
        if kw in nm:
            return True, f"이름패턴({kw})"
    for suffix in EXCLUDE_NAME_ENDSWITH:
        if nm.endswith(suffix):
            return True, f"우선주({suffix})"
    return False, ""
