"""
CANSLIM 전략3 대상 종목 유니버스 (~200종목)

선정 기준:
  - 국내 상장 성장주 (KOSPI 대형·중형 + KOSDAQ 핵심 성장주)
  - DART 분기보고서 제출 대상 (상장 법인)
  - ETF·리츠·스팩·우선주 제외

이 파일은 DART 배치(update_dart.py)의 조회 대상 유니버스임.
분기 DART 배치 실행 후 C·A 조건 통과 종목이 dart_ca_screened.json에 저장되며,
이후 canslim-batch 는 해당 사전 필터링 목록만 사용함.
"""
from typing import List, Dict

CANSLIM_UNIVERSE: List[Dict[str, str]] = [
    # ── KOSPI 반도체/IT ───────────────────────────────────────────
    {"code": "005930", "name": "삼성전자",           "sector": "반도체"},
    {"code": "000660", "name": "SK하이닉스",          "sector": "반도체"},
    {"code": "009150", "name": "삼성전기",            "sector": "전자부품"},
    {"code": "011070", "name": "LG이노텍",            "sector": "전자부품"},
    {"code": "066570", "name": "LG전자",              "sector": "전자"},
    {"code": "018260", "name": "삼성에스디에스",      "sector": "IT서비스"},
    {"code": "035420", "name": "NAVER",               "sector": "인터넷"},
    {"code": "035720", "name": "카카오",              "sector": "인터넷"},
    {"code": "036570", "name": "엔씨소프트",          "sector": "게임"},
    {"code": "251270", "name": "넷마블",              "sector": "게임"},
    {"code": "259960", "name": "크래프톤",            "sector": "게임"},
    {"code": "293490", "name": "카카오게임즈",        "sector": "게임"},

    # ── KOSPI 2차전지/소재/화학 ───────────────────────────────────
    {"code": "373220", "name": "LG에너지솔루션",      "sector": "2차전지"},
    {"code": "006400", "name": "삼성SDI",             "sector": "2차전지"},
    {"code": "003670", "name": "포스코퓨처엠",        "sector": "2차전지소재"},
    {"code": "096770", "name": "SK이노베이션",        "sector": "에너지/배터리"},
    {"code": "051910", "name": "LG화학",              "sector": "화학"},
    {"code": "009830", "name": "한화솔루션",          "sector": "태양광/화학"},
    {"code": "005490", "name": "포스코홀딩스",        "sector": "철강/소재"},
    {"code": "010950", "name": "S-Oil",               "sector": "정유"},

    # ── KOSPI 바이오/헬스케어 ──────────────────────────────────────
    {"code": "207940", "name": "삼성바이오로직스",    "sector": "바이오"},
    {"code": "068270", "name": "셀트리온",            "sector": "바이오"},
    {"code": "000100", "name": "유한양행",            "sector": "제약"},
    {"code": "128940", "name": "한미약품",            "sector": "제약"},
    {"code": "185750", "name": "종근당",              "sector": "제약"},
    {"code": "302440", "name": "SK바이오사이언스",    "sector": "바이오"},
    {"code": "051900", "name": "LG생활건강",          "sector": "생활용품"},

    # ── KOSPI 자동차/모빌리티 ─────────────────────────────────────
    {"code": "005380", "name": "현대차",              "sector": "자동차"},
    {"code": "000270", "name": "기아",                "sector": "자동차"},
    {"code": "012330", "name": "현대모비스",          "sector": "자동차부품"},
    {"code": "011210", "name": "현대위아",            "sector": "자동차부품"},
    {"code": "086280", "name": "현대글로비스",        "sector": "물류"},

    # ── KOSPI 방산/조선/중공업 ────────────────────────────────────
    {"code": "012450", "name": "한화에어로스페이스",  "sector": "방산"},
    {"code": "079550", "name": "LIG넥스원",          "sector": "방산"},
    {"code": "064350", "name": "현대로템",            "sector": "방산"},
    {"code": "272210", "name": "한화시스템",          "sector": "방산/IT"},
    {"code": "009540", "name": "HD한국조선해양",      "sector": "조선"},
    {"code": "010140", "name": "삼성중공업",          "sector": "조선"},
    {"code": "042660", "name": "한화오션",            "sector": "조선"},
    {"code": "329180", "name": "HD현대중공업",        "sector": "조선"},
    {"code": "267260", "name": "HD현대일렉트릭",      "sector": "전력기기"},
    {"code": "010120", "name": "LS ELECTRIC",         "sector": "전력기기"},
    {"code": "034020", "name": "두산에너빌리티",      "sector": "중공업"},
    {"code": "454910", "name": "두산로보틱스",        "sector": "로봇"},
    {"code": "241560", "name": "두산밥캣",            "sector": "건설기계"},
    {"code": "267250", "name": "HD현대",              "sector": "지주사"},

    # ── KOSPI 엔터/미디어/소비재 ──────────────────────────────────
    {"code": "352820", "name": "HYBE",                "sector": "엔터"},
    {"code": "041510", "name": "SM엔터테인먼트",      "sector": "엔터"},
    {"code": "035900", "name": "JYP엔터테인먼트",     "sector": "엔터"},
    {"code": "122870", "name": "YG엔터테인먼트",      "sector": "엔터"},
    {"code": "035760", "name": "CJ ENM",              "sector": "미디어"},
    {"code": "271560", "name": "오리온",              "sector": "식품"},
    {"code": "097950", "name": "CJ제일제당",          "sector": "식품"},
    {"code": "004370", "name": "농심",                "sector": "식품"},

    # ── KOSPI 건설/부동산 ─────────────────────────────────────────
    {"code": "000720", "name": "현대건설",            "sector": "건설"},
    {"code": "006360", "name": "GS건설",              "sector": "건설"},

    # ── KOSPI 통신 ────────────────────────────────────────────────
    {"code": "017670", "name": "SK텔레콤",            "sector": "통신"},
    {"code": "030200", "name": "KT",                  "sector": "통신"},
    {"code": "032640", "name": "LG유플러스",          "sector": "통신"},

    # ── KOSPI 화장품/뷰티 ─────────────────────────────────────────
    {"code": "044820", "name": "코스맥스",            "sector": "화장품"},
    {"code": "161890", "name": "한국콜마",            "sector": "화장품"},

    # ── KOSDAQ 2차전지/소재 ───────────────────────────────────────
    {"code": "247540", "name": "에코프로비엠",        "sector": "2차전지소재"},
    {"code": "086520", "name": "에코프로",            "sector": "2차전지소재"},
    {"code": "066970", "name": "엘앤에프",            "sector": "2차전지소재"},
    {"code": "357780", "name": "솔브레인",            "sector": "반도체소재"},
    {"code": "336370", "name": "솔루스첨단소재",      "sector": "2차전지소재"},
    {"code": "078600", "name": "대주전자재료",        "sector": "전자재료"},

    # ── KOSDAQ 반도체장비/소재 ────────────────────────────────────
    {"code": "036930", "name": "주성엔지니어링",      "sector": "반도체장비"},
    {"code": "240810", "name": "원익IPS",             "sector": "반도체장비"},
    {"code": "319660", "name": "피에스케이",          "sector": "반도체장비"},
    {"code": "039030", "name": "이오테크닉스",        "sector": "레이저장비"},
    {"code": "140860", "name": "파크시스템스",        "sector": "반도체장비"},
    {"code": "265520", "name": "AP시스템",            "sector": "디스플레이장비"},
    {"code": "217270", "name": "넥스틴",              "sector": "반도체장비"},
    {"code": "056190", "name": "에스에프에이",        "sector": "자동화장비"},
    {"code": "058470", "name": "리노공업",            "sector": "반도체소켓"},
    {"code": "064760", "name": "티씨케이",            "sector": "반도체소재"},
    {"code": "046890", "name": "서울반도체",          "sector": "LED"},
    {"code": "089150", "name": "테크윙",              "sector": "반도체장비"},
    {"code": "102710", "name": "이엔에프테크놀로지",  "sector": "반도체소재"},
    {"code": "213420", "name": "덕산네오룩스",        "sector": "OLED소재"},

    # ── KOSDAQ 바이오/의료기기 ────────────────────────────────────
    {"code": "196170", "name": "알테오젠",            "sector": "바이오"},
    {"code": "145020", "name": "휴젤",                "sector": "바이오"},
    {"code": "028300", "name": "HLB",                 "sector": "바이오"},
    {"code": "298380", "name": "에이비엘바이오",      "sector": "바이오"},
    {"code": "140410", "name": "메지온",              "sector": "바이오"},
    {"code": "039200", "name": "오스코텍",            "sector": "바이오"},
    {"code": "141080", "name": "레고켐바이오",        "sector": "바이오"},
    {"code": "328130", "name": "루닛",                "sector": "AI의료"},
    {"code": "237690", "name": "에스티팜",            "sector": "CMO"},
    {"code": "048260", "name": "오스템임플란트",      "sector": "의료기기"},
    {"code": "145720", "name": "덴티움",              "sector": "의료기기"},
    {"code": "214150", "name": "클래시스",            "sector": "의료기기"},
    {"code": "096530", "name": "씨젠",                "sector": "진단"},
    {"code": "067630", "name": "HLB생명과학",         "sector": "바이오"},
    {"code": "950160", "name": "코오롱티슈진",        "sector": "바이오"},

    # ── KOSDAQ IT/플랫폼/SaaS ─────────────────────────────────────
    {"code": "012510", "name": "더존비즈온",          "sector": "SaaS"},
    {"code": "067160", "name": "아프리카TV",          "sector": "미디어플랫폼"},
    {"code": "042000", "name": "카페24",              "sector": "이커머스"},
    {"code": "090460", "name": "비에이치",            "sector": "전자부품"},
    {"code": "263140", "name": "웹젠",                "sector": "게임"},
    {"code": "112040", "name": "위메이드",            "sector": "게임"},
    {"code": "263750", "name": "펄어비스",            "sector": "게임"},
    {"code": "108860", "name": "셀바스AI",            "sector": "AI/음성인식"},

    # ── KOSDAQ 기타 성장주 ────────────────────────────────────────
    {"code": "192400", "name": "쿠쿠홀딩스",          "sector": "소형가전"},
    {"code": "122990", "name": "와이솔",              "sector": "RF부품"},
    {"code": "357230", "name": "씨아이에스",          "sector": "배터리장비"},
    {"code": "022100", "name": "포스코DX",            "sector": "IT서비스"},
    {"code": "178920", "name": "PI첨단소재",          "sector": "소재"},
    {"code": "257720", "name": "실리콘투",            "sector": "K뷰티유통"},
    {"code": "058470", "name": "리노공업",            "sector": "반도체소켓"},
]

# 중복 코드 제거 (리노공업 중복 방지)
_seen: set = set()
_dedup: list = []
for _s in CANSLIM_UNIVERSE:
    if _s["code"] not in _seen:
        _seen.add(_s["code"])
        _dedup.append(_s)
CANSLIM_UNIVERSE = _dedup

CANSLIM_CODE_MAP: dict = {s["code"]: s for s in CANSLIM_UNIVERSE}
