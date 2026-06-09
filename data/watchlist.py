"""
옥동자 매매 대상 종목 관리

[공통 유니버스 — KOSPI200 + KOSDAQ150 + ETF (채권·금리 ETF 제외)]
  S1(오전 단기), S2(MA 이평선), S3(CANSLIM N·S·L·I·M) 모두 동일
  → KOSPI200: data/kospi200_cache.json (update_watchlist.py 갱신)
  → KOSDAQ150: data/kosdaq150_cache.json (update_watchlist.py 갱신)
"""
import json
from pathlib import Path
from typing import List, Dict

from data.etf_watchlist import ETF_LIST

_CACHE_PATH      = Path(__file__).parent / "kospi200_cache.json"
_KOSDAQ150_CACHE = Path(__file__).parent / "kosdaq150_cache.json"

# ── KOSPI200 빌트인 폴백 (149종목) ───────────────────────────────────────────
_BUILTIN: List[Dict[str, str]] = [
    # 반도체/IT
    {"code": "005930", "name": "삼성전자",      "sector": "반도체/IT"},
    {"code": "000660", "name": "SK하이닉스",    "sector": "반도체/IT"},
    {"code": "042700", "name": "한미반도체",    "sector": "반도체/IT"},
    {"code": "066570", "name": "LG전자",        "sector": "반도체/IT"},
    {"code": "009150", "name": "삼성전기",      "sector": "반도체/IT"},
    {"code": "011070", "name": "LG이노텍",      "sector": "반도체/IT"},
    {"code": "034220", "name": "LG디스플레이",  "sector": "반도체/IT"},
    {"code": "018260", "name": "삼성SDS",       "sector": "반도체/IT"},
    {"code": "000990", "name": "DB하이텍",      "sector": "반도체/IT"},
    {"code": "240810", "name": "원익IPS",       "sector": "반도체/IT"},
    # 2차전지
    {"code": "373220", "name": "LG에너지솔루션", "sector": "2차전지"},
    {"code": "005490", "name": "POSCO홀딩스",   "sector": "2차전지"},
    {"code": "006400", "name": "삼성SDI",       "sector": "2차전지"},
    {"code": "051910", "name": "LG화학",        "sector": "2차전지"},
    {"code": "003670", "name": "포스코퓨처엠",  "sector": "2차전지"},
    {"code": "450080", "name": "에코프로머티",  "sector": "2차전지"},
    {"code": "096770", "name": "SK이노베이션",  "sector": "2차전지/정유"},
    # AI전력/설비
    {"code": "034020", "name": "두산에너빌리티", "sector": "AI전력/설비"},
    {"code": "267260", "name": "HD현대일렉트릭", "sector": "AI전력/설비"},
    {"code": "010120", "name": "LS ELECTRIC",   "sector": "AI전력/설비"},
    {"code": "006260", "name": "LS",            "sector": "AI전력/설비"},
    {"code": "051600", "name": "한전KPS",       "sector": "AI전력/설비"},
    # 자동차/부품
    {"code": "005380", "name": "현대차",        "sector": "자동차/부품"},
    {"code": "000270", "name": "기아",          "sector": "자동차/부품"},
    {"code": "012330", "name": "현대모비스",    "sector": "자동차/부품"},
    {"code": "018880", "name": "한온시스템",    "sector": "자동차/부품"},
    {"code": "011210", "name": "현대위아",      "sector": "자동차/부품"},
    {"code": "204320", "name": "만도",          "sector": "자동차/부품"},
    {"code": "002350", "name": "넥센타이어",    "sector": "자동차/부품"},
    # 조선/해운
    {"code": "009540", "name": "HD한국조선해양", "sector": "조선/해운"},
    {"code": "042660", "name": "한화오션",      "sector": "조선/해운"},
    {"code": "010140", "name": "삼성중공업",    "sector": "조선/해운"},
    {"code": "011200", "name": "HMM",           "sector": "조선/해운"},
    {"code": "010620", "name": "HD현대미포",    "sector": "조선/해운"},
    {"code": "028670", "name": "팬오션",        "sector": "조선/해운"},
    # 방산/우주
    {"code": "012450", "name": "한화에어로스페이스", "sector": "방산/우주"},
    {"code": "079550", "name": "LIG넥스원",     "sector": "방산/우주"},
    {"code": "064350", "name": "현대로템",      "sector": "방산/우주"},
    {"code": "047810", "name": "한국항공우주",  "sector": "방산/우주"},
    {"code": "272210", "name": "한화시스템",    "sector": "방산/우주"},
    # 항공/물류
    {"code": "003490", "name": "대한항공",      "sector": "항공/물류"},
    {"code": "086280", "name": "현대글로비스",  "sector": "항공/물류"},
    {"code": "000120", "name": "CJ대한통운",    "sector": "항공/물류"},
    {"code": "180640", "name": "한진칼",        "sector": "항공/물류"},
    # 중공업/기계
    {"code": "329180", "name": "HD현대",              "sector": "중공업/기계"},
    {"code": "267250", "name": "HD현대중공업",         "sector": "중공업/기계"},
    {"code": "042670", "name": "HD현대인프라코어",     "sector": "중공업/기계"},
    {"code": "267270", "name": "HD현대건설기계",       "sector": "중공업/기계"},
    {"code": "082740", "name": "한화엔진",             "sector": "중공업/기계"},
    {"code": "298040", "name": "효성중공업",           "sector": "중공업/기계"},
    {"code": "017800", "name": "현대엘리베이터",       "sector": "중공업/기계"},
    # 엔터테인먼트
    {"code": "352820", "name": "하이브",              "sector": "엔터테인먼트"},
    {"code": "041510", "name": "에스엠",              "sector": "엔터테인먼트"},
    {"code": "035900", "name": "JYP엔터테인먼트",     "sector": "엔터테인먼트"},
    {"code": "122870", "name": "와이지엔터테인먼트",  "sector": "엔터테인먼트"},
    {"code": "035760", "name": "CJ ENM",              "sector": "엔터테인먼트"},
    # 바이오/제약
    {"code": "207940", "name": "삼성바이오로직스", "sector": "바이오/제약"},
    {"code": "068270", "name": "셀트리온",      "sector": "바이오/제약"},
    {"code": "000100", "name": "유한양행",      "sector": "바이오/제약"},
    {"code": "326030", "name": "SK바이오팜",    "sector": "바이오/제약"},
    {"code": "302440", "name": "SK바이오사이언스", "sector": "바이오/제약"},
    {"code": "008930", "name": "한미사이언스",  "sector": "바이오/제약"},
    {"code": "128940", "name": "한미약품",      "sector": "바이오/제약"},
    {"code": "001630", "name": "종근당",        "sector": "바이오/제약"},
    {"code": "003220", "name": "대웅제약",      "sector": "바이오/제약"},
    {"code": "069620", "name": "대웅",          "sector": "바이오/제약"},
    {"code": "006280", "name": "GC녹십자",      "sector": "바이오/제약"},
    {"code": "145020", "name": "휴젤",          "sector": "바이오/제약"},
    {"code": "170900", "name": "동아에스티",    "sector": "바이오/제약"},
    # 플랫폼/게임
    {"code": "035420", "name": "NAVER",         "sector": "플랫폼/게임"},
    {"code": "035720", "name": "카카오",        "sector": "플랫폼/게임"},
    {"code": "259960", "name": "크래프톤",      "sector": "플랫폼/게임"},
    {"code": "036570", "name": "엔씨소프트",    "sector": "플랫폼/게임"},
    {"code": "251270", "name": "넷마블",        "sector": "플랫폼/게임"},
    {"code": "030000", "name": "제일기획",      "sector": "플랫폼/게임"},
    {"code": "293490", "name": "카카오게임즈",  "sector": "플랫폼/게임"},
    # 금융/지주
    {"code": "105560", "name": "KB금융",        "sector": "금융/지주"},
    {"code": "055550", "name": "신한지주",      "sector": "금융/지주"},
    {"code": "086790", "name": "하나금융지주",  "sector": "금융/지주"},
    {"code": "316140", "name": "우리금융지주",  "sector": "금융/지주"},
    {"code": "032830", "name": "삼성생명",      "sector": "금융/지주"},
    {"code": "000810", "name": "삼성화재",      "sector": "금융/지주"},
    {"code": "006800", "name": "미래에셋증권",  "sector": "금융/지주"},
    {"code": "016360", "name": "삼성증권",      "sector": "금융/지주"},
    {"code": "028260", "name": "삼성물산",      "sector": "금융/지주"},
    {"code": "000150", "name": "두산",          "sector": "금융/지주"},
    {"code": "034730", "name": "SK",            "sector": "금융/지주"},
    {"code": "003550", "name": "LG",            "sector": "금융/지주"},
    {"code": "138040", "name": "메리츠금융지주", "sector": "금융/지주"},
    {"code": "001450", "name": "현대해상",      "sector": "금융/지주"},
    {"code": "034830", "name": "한국금융지주",  "sector": "금융/지주"},
    {"code": "039490", "name": "키움증권",      "sector": "금융/지주"},
    {"code": "005940", "name": "NH투자증권",    "sector": "금융/지주"},
    {"code": "029780", "name": "삼성카드",      "sector": "금융/지주"},
    {"code": "323410", "name": "카카오뱅크",    "sector": "금융/지주"},
    {"code": "000880", "name": "한화",          "sector": "금융/지주"},
    {"code": "001040", "name": "CJ",            "sector": "금융/지주"},
    {"code": "004990", "name": "롯데지주",      "sector": "금융/지주"},
    {"code": "078930", "name": "GS",            "sector": "금융/지주"},
    {"code": "002790", "name": "아모레G",       "sector": "금융/지주"},
    {"code": "377300", "name": "카카오페이",    "sector": "금융/지주"},
    {"code": "138930", "name": "BNK금융지주",   "sector": "금융/지주"},
    {"code": "139130", "name": "DGB금융지주",   "sector": "금융/지주"},
    {"code": "175330", "name": "JB금융지주",    "sector": "금융/지주"},
    {"code": "088350", "name": "한화생명",      "sector": "금융/지주"},
    {"code": "000060", "name": "메리츠화재",    "sector": "금융/지주"},
    {"code": "005830", "name": "DB손해보험",    "sector": "금융/지주"},
    # 철강/소재
    {"code": "004020", "name": "현대제철",      "sector": "철강/소재"},
    {"code": "010130", "name": "고려아연",      "sector": "철강/소재"},
    {"code": "103140", "name": "풍산",          "sector": "철강/소재"},
    {"code": "001230", "name": "동국제강",      "sector": "철강/소재"},
    {"code": "047050", "name": "포스코인터내셔널", "sector": "철강/소재"},
    # 화학/정유
    {"code": "011780", "name": "금호석유",      "sector": "화학/정유"},
    {"code": "010950", "name": "S-Oil",         "sector": "화학/정유"},
    {"code": "011170", "name": "롯데케미칼",    "sector": "화학/정유"},
    {"code": "009830", "name": "한화솔루션",    "sector": "화학/정유"},
    {"code": "011790", "name": "SKC",           "sector": "화학/정유"},
    {"code": "285130", "name": "SK케미칼",      "sector": "화학/정유"},
    {"code": "010600", "name": "OCI홀딩스",     "sector": "화학/정유"},
    {"code": "004000", "name": "롯데정밀화학",  "sector": "화학/정유"},
    # 유통/소비재
    {"code": "090430", "name": "아모레퍼시픽",  "sector": "유통/소비재"},
    {"code": "051900", "name": "LG생활건강",    "sector": "유통/소비재"},
    {"code": "023530", "name": "롯데쇼핑",      "sector": "유통/소비재"},
    {"code": "282330", "name": "BGF리테일",     "sector": "유통/소비재"},
    {"code": "271560", "name": "오리온",        "sector": "유통/소비재"},
    {"code": "000080", "name": "하이트진로",    "sector": "유통/소비재"},
    {"code": "004370", "name": "농심",          "sector": "유통/소비재"},
    {"code": "004170", "name": "신세계",        "sector": "유통/소비재"},
    {"code": "139480", "name": "이마트",        "sector": "유통/소비재"},
    {"code": "007070", "name": "GS리테일",      "sector": "유통/소비재"},
    {"code": "069960", "name": "현대백화점",    "sector": "유통/소비재"},
    {"code": "021240", "name": "코웨이",        "sector": "유통/소비재"},
    {"code": "007340", "name": "롯데칠성",      "sector": "유통/소비재"},
    {"code": "097950", "name": "CJ제일제당",    "sector": "유통/소비재"},
    {"code": "007310", "name": "오뚜기",        "sector": "유통/소비재"},
    {"code": "033780", "name": "KT&G",          "sector": "유통/소비재"},
    {"code": "001680", "name": "대상",          "sector": "유통/소비재"},
    # 통신/인프라
    {"code": "017670", "name": "SK텔레콤",      "sector": "통신/인프라"},
    {"code": "030200", "name": "KT",            "sector": "통신/인프라"},
    {"code": "032640", "name": "LG유플러스",    "sector": "통신/인프라"},
    {"code": "015760", "name": "한국전력",      "sector": "통신/인프라"},
    {"code": "036460", "name": "한국가스공사",  "sector": "통신/인프라"},
    # 건설/건자재
    {"code": "000720", "name": "현대건설",      "sector": "건설/건자재"},
    {"code": "006360", "name": "GS건설",        "sector": "건설/건자재"},
    {"code": "047040", "name": "대우건설",      "sector": "건설/건자재"},
    {"code": "010960", "name": "삼호개발",      "sector": "건설/건자재"},
    {"code": "002380", "name": "KCC",           "sector": "건설/건자재"},
    {"code": "012630", "name": "HDC현대산업개발", "sector": "건설/건자재"},
    {"code": "006125", "name": "DL이앤씨",      "sector": "건설/건자재"},
]

# ── KOSDAQ150 빌트인 폴백 ──────────────────────────────────────────────────
_KOSDAQ150_BUILTIN: List[Dict[str, str]] = [
    # 반도체/장비
    {"code": "357780", "name": "솔브레인",      "sector": "반도체/IT"},
    {"code": "005290", "name": "동진쎄미켐",    "sector": "반도체/IT"},
    {"code": "031980", "name": "피에스케이홀딩스", "sector": "반도체/IT"},
    {"code": "039030", "name": "이오테크닉스",  "sector": "반도체/IT"},
    {"code": "140860", "name": "파크시스템스",  "sector": "반도체/IT"},
    {"code": "403870", "name": "HPSP",          "sector": "반도체/IT"},
    {"code": "074600", "name": "원익QnC",       "sector": "반도체/IT"},
    {"code": "058470", "name": "리노공업",      "sector": "반도체/IT"},
    {"code": "399720", "name": "가온칩스",      "sector": "반도체/IT"},
    {"code": "064290", "name": "인텍플러스",    "sector": "반도체/IT"},
    {"code": "203690", "name": "네패스아크",    "sector": "반도체/IT"},
    {"code": "022100", "name": "포스코DX",      "sector": "반도체/IT"},
    # 2차전지
    {"code": "086520", "name": "에코프로",      "sector": "2차전지"},
    {"code": "247540", "name": "에코프로비엠",  "sector": "2차전지"},
    {"code": "243840", "name": "신흥에스이씨",  "sector": "2차전지"},
    {"code": "393890", "name": "더블유씨피",    "sector": "2차전지"},
    # 바이오/제약
    {"code": "196170", "name": "알테오젠",      "sector": "바이오/제약"},
    {"code": "028300", "name": "HLB",           "sector": "바이오/제약"},
    {"code": "141080", "name": "리가켐바이오",  "sector": "바이오/제약"},
    {"code": "214450", "name": "파마리서치",    "sector": "바이오/제약"},
    {"code": "214150", "name": "클래시스",      "sector": "바이오/제약"},
    {"code": "237690", "name": "에스티팜",      "sector": "바이오/제약"},
    {"code": "068760", "name": "셀트리온제약",  "sector": "바이오/제약"},
    {"code": "086900", "name": "메디톡스",      "sector": "바이오/제약"},
    {"code": "039200", "name": "오스코텍",      "sector": "바이오/제약"},
    {"code": "214370", "name": "케어젠",        "sector": "바이오/제약"},
    {"code": "137310", "name": "에스디바이오센서", "sector": "바이오/제약"},
    {"code": "328130", "name": "루닛",          "sector": "바이오/제약"},
    {"code": "322510", "name": "제이엘케이",    "sector": "바이오/제약"},
    {"code": "085370", "name": "루트로닉",      "sector": "바이오/제약"},
    # 로봇/AI
    {"code": "454910", "name": "두산로보틱스",  "sector": "중공업/기계"},
    {"code": "277810", "name": "레인보우로보틱스", "sector": "중공업/기계"},
    # 플랫폼/게임
    {"code": "181710", "name": "NHN",           "sector": "플랫폼/게임"},
    {"code": "112040", "name": "위메이드",      "sector": "플랫폼/게임"},
    # 통신/부품
    {"code": "032500", "name": "케이엠더블유",  "sector": "통신/인프라"},
    {"code": "078070", "name": "유비쿼스",      "sector": "통신/인프라"},
    {"code": "054050", "name": "와이솔",        "sector": "반도체/IT"},
    # 소비/유통
    {"code": "257720", "name": "실리콘투",      "sector": "유통/소비재"},
    {"code": "403550", "name": "쏘카",          "sector": "유통/소비재"},
]


def _load_kospi200() -> List[Dict[str, str]]:
    """KOSPI200 캐시가 있으면 사용, 없으면 빌트인 폴백 반환"""
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        stocks = data.get("stocks", [])
        if len(stocks) >= 10:
            return stocks
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return _BUILTIN


def _load_kosdaq150() -> List[Dict[str, str]]:
    """KOSDAQ150 캐시가 있으면 사용, 없으면 빌트인 폴백 반환"""
    try:
        data = json.loads(_KOSDAQ150_CACHE.read_text(encoding="utf-8"))
        stocks = data.get("stocks", [])
        if len(stocks) >= 10:
            return stocks
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return _KOSDAQ150_BUILTIN


def get_active_watchlist() -> List[Dict[str, str]]:
    """S1/S2/S3 공통 유니버스: KOSPI200 + KOSDAQ150 + ETF (code 중복 제거)"""
    return get_s2_watchlist()


def get_s2_watchlist() -> List[Dict[str, str]]:
    """S2(MA전략) + S3(CANSLIM) + DART배치용: KOSPI200 + KOSDAQ150 + ETF 통합 (code 중복 제거)"""
    seen: set = set()
    merged: List[Dict[str, str]] = []
    for item in _load_kospi200() + _load_kosdaq150() + ETF_LIST:
        if item["code"] not in seen:
            seen.add(item["code"])
            merged.append(item)
    return merged


# S1/S2/S3 공통 유니버스: KOSPI200 + KOSDAQ150 + ETF
WATCHLIST: List[Dict[str, str]] = get_active_watchlist()

CODE_MAP: Dict[str, Dict[str, str]] = {s["code"]: s for s in WATCHLIST}

SECTOR_MAP: Dict[str, List[Dict[str, str]]] = {}
for _s in WATCHLIST:
    SECTOR_MAP.setdefault(_s["sector"], []).append(_s)
