"""
옥동자 매매 대상 종목 (80개)
"""
from typing import List, Dict

WATCHLIST: List[Dict[str, str]] = [
    # 반도체/IT
    {"code": "005930", "name": "삼성전자",     "sector": "반도체/IT"},
    {"code": "000660", "name": "SK하이닉스",   "sector": "반도체/IT"},
    {"code": "042700", "name": "한미반도체",   "sector": "반도체/IT"},
    {"code": "066570", "name": "LG전자",       "sector": "반도체/IT"},
    {"code": "009150", "name": "삼성전기",     "sector": "반도체/IT"},
    {"code": "011070", "name": "LG이노텍",     "sector": "반도체/IT"},
    {"code": "034220", "name": "LG디스플레이", "sector": "반도체/IT"},
    {"code": "018260", "name": "삼성SDS",      "sector": "반도체/IT"},
    {"code": "000990", "name": "DB하이텍",     "sector": "반도체/IT"},
    # 2차전지
    {"code": "373220", "name": "LG에너지솔루션","sector": "2차전지"},
    {"code": "005490", "name": "POSCO홀딩스",  "sector": "2차전지"},
    {"code": "006400", "name": "삼성SDI",      "sector": "2차전지"},
    {"code": "051910", "name": "LG화학",       "sector": "2차전지"},
    {"code": "003670", "name": "포스코퓨처엠", "sector": "2차전지"},
    {"code": "450080", "name": "에코프로머티", "sector": "2차전지"},
    {"code": "096770", "name": "SK이노베이션", "sector": "2차전지/정유"},
    {"code": "010960", "name": "금양",         "sector": "2차전지"},
    # AI전력/설비
    {"code": "034020", "name": "두산에너빌리티",   "sector": "AI전력/설비"},
    {"code": "267260", "name": "HD현대일렉트릭",   "sector": "AI전력/설비"},
    {"code": "010120", "name": "LS ELECTRIC",      "sector": "AI전력/설비"},
    {"code": "006260", "name": "LS",               "sector": "AI전력/설비"},
    # 자동차/부품
    {"code": "005380", "name": "현대차",       "sector": "자동차/부품"},
    {"code": "000270", "name": "기아",         "sector": "자동차/부품"},
    {"code": "012330", "name": "현대모비스",   "sector": "자동차/부품"},
    {"code": "018880", "name": "한온시스템",   "sector": "자동차/부품"},
    {"code": "011210", "name": "현대위아",     "sector": "자동차/부품"},
    # 조선/해운
    {"code": "009540", "name": "HD한국조선해양","sector": "조선/해운"},
    {"code": "042660", "name": "한화오션",     "sector": "조선/해운"},
    {"code": "010140", "name": "삼성중공업",   "sector": "조선/해운"},
    {"code": "011200", "name": "HMM",          "sector": "조선/해운"},
    {"code": "010620", "name": "HD현대미포",   "sector": "조선/해운"},
    {"code": "028670", "name": "팬오션",       "sector": "조선/해운"},
    # 방산/우주
    {"code": "012450", "name": "한화에어로스페이스","sector": "방산/우주"},
    {"code": "079550", "name": "LIG넥스원",    "sector": "방산/우주"},
    {"code": "064350", "name": "현대로템",     "sector": "방산/우주"},
    {"code": "047810", "name": "한국항공우주", "sector": "방산/우주"},
    {"code": "272210", "name": "한화시스템",   "sector": "방산/우주"},
    # 바이오/제약
    {"code": "207940", "name": "삼성바이오로직스","sector": "바이오/제약"},
    {"code": "068270", "name": "셀트리온",     "sector": "바이오/제약"},
    {"code": "000100", "name": "유한양행",     "sector": "바이오/제약"},
    {"code": "326030", "name": "SK바이오팜",   "sector": "바이오/제약"},
    {"code": "302440", "name": "SK바이오사이언스","sector": "바이오/제약"},
    {"code": "008930", "name": "한미사이언스", "sector": "바이오/제약"},
    {"code": "128940", "name": "한미약품",     "sector": "바이오/제약"},
    {"code": "001630", "name": "종근당",       "sector": "바이오/제약"},
    # 플랫폼/게임
    {"code": "035420", "name": "NAVER",        "sector": "플랫폼/게임"},
    {"code": "035720", "name": "카카오",       "sector": "플랫폼/게임"},
    {"code": "259960", "name": "크래프톤",     "sector": "플랫폼/게임"},
    {"code": "036570", "name": "엔씨소프트",   "sector": "플랫폼/게임"},
    {"code": "251270", "name": "넷마블",       "sector": "플랫폼/게임"},
    {"code": "030000", "name": "제일기획",     "sector": "플랫폼/게임"},
    # 금융/지주
    {"code": "105560", "name": "KB금융",       "sector": "금융/지주"},
    {"code": "055550", "name": "신한지주",     "sector": "금융/지주"},
    {"code": "086790", "name": "하나금융지주", "sector": "금융/지주"},
    {"code": "316140", "name": "우리금융지주", "sector": "금융/지주"},
    {"code": "032830", "name": "삼성생명",     "sector": "금융/지주"},
    {"code": "000810", "name": "삼성화재",     "sector": "금융/지주"},
    {"code": "006800", "name": "미래에셋증권", "sector": "금융/지주"},
    {"code": "016360", "name": "삼성증권",     "sector": "금융/지주"},
    {"code": "028260", "name": "삼성물산",     "sector": "금융/지주"},
    {"code": "000150", "name": "두산",         "sector": "금융/지주"},
    {"code": "034730", "name": "SK",           "sector": "금융/지주"},
    {"code": "003550", "name": "LG",           "sector": "금융/지주"},
    # 철강/소재
    {"code": "004020", "name": "현대제철",     "sector": "철강/소재"},
    {"code": "010130", "name": "고려아연",     "sector": "철강/소재"},
    {"code": "103140", "name": "풍산",         "sector": "철강/소재"},
    {"code": "001230", "name": "동국제강",     "sector": "철강/소재"},
    # 화학/정유
    {"code": "011780", "name": "금호석유",     "sector": "화학/정유"},
    {"code": "010950", "name": "S-Oil",        "sector": "화학/정유"},
    # 유통/소비재
    {"code": "090430", "name": "아모레퍼시픽", "sector": "유통/소비재"},
    {"code": "051900", "name": "LG생활건강",   "sector": "유통/소비재"},
    {"code": "023530", "name": "롯데쇼핑",     "sector": "유통/소비재"},
    {"code": "282330", "name": "BGF리테일",    "sector": "유통/소비재"},
    {"code": "271560", "name": "오리온",       "sector": "유통/소비재"},
    {"code": "000080", "name": "하이트진로",   "sector": "유통/소비재"},
    {"code": "004370", "name": "농심",         "sector": "유통/소비재"},
    # 통신/인프라
    {"code": "017670", "name": "SK텔레콤",     "sector": "통신/인프라"},
    {"code": "030200", "name": "KT",           "sector": "통신/인프라"},
    {"code": "032640", "name": "LG유플러스",   "sector": "통신/인프라"},
    {"code": "015760", "name": "한국전력",     "sector": "통신/인프라"},
    {"code": "036460", "name": "한국가스공사", "sector": "통신/인프라"},
    # 건설/건자재
    {"code": "000720", "name": "현대건설",     "sector": "건설/건자재"},
    {"code": "006360", "name": "GS건설",       "sector": "건설/건자재"},
    {"code": "047040", "name": "대우건설",     "sector": "건설/건자재"},
]

# 종목코드 → 정보 빠른 조회용
CODE_MAP: Dict[str, Dict[str, str]] = {s["code"]: s for s in WATCHLIST}

# 섹터별 그룹
SECTOR_MAP: Dict[str, List[Dict[str, str]]] = {}
for _s in WATCHLIST:
    SECTOR_MAP.setdefault(_s["sector"], []).append(_s)
