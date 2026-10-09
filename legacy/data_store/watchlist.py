"""
옥동자 매매 대상 종목 관리

[공통 유니버스 — KOSPI200 + KOSDAQ150 + ETF (채권·금리 ETF 제외)]
  S1(오전 단기), S2(MA 이평선), S3(CANSLIM N·S·L·I·M) 모두 동일
  → KOSPI200:  data/kospi200_cache.json  (update_watchlist.py 분기 갱신)
  → KOSDAQ150: data/kosdaq150_cache.json (update_watchlist.py 분기 갱신)
  → ETF:       data/etf_cache.json       (update_watchlist.py 분기 갱신)
               캐시 없으면 data/etf_watchlist.py 정적 목록 폴백
"""
import json
from pathlib import Path
from typing import List, Dict

from data.etf_watchlist import ETF_LIST  # 폴백용

_CACHE_PATH      = Path(__file__).parent / "kospi200_cache.json"
_KOSDAQ150_CACHE = Path(__file__).parent / "kosdaq150_cache.json"
_ETF_CACHE       = Path(__file__).parent / "etf_cache.json"

# ── KOSPI200 빌트인 폴백 (200종목, 2026-01-23 기준, 시가총액순) ────────────────
_BUILTIN: List[Dict[str, str]] = [
    # 반도체/IT
    {"code": "005930", "name": "삼성전자",              "sector": "반도체/IT"},
    {"code": "000660", "name": "SK하이닉스",             "sector": "반도체/IT"},
    {"code": "009150", "name": "삼성전기",               "sector": "반도체/IT"},
    {"code": "066570", "name": "LG전자",                 "sector": "반도체/IT"},
    {"code": "042700", "name": "한미반도체",             "sector": "반도체/IT"},
    {"code": "018260", "name": "삼성에스디에스",         "sector": "반도체/IT"},
    {"code": "307950", "name": "현대오토에버",           "sector": "반도체/IT"},
    {"code": "011070", "name": "LG이노텍",               "sector": "반도체/IT"},
    {"code": "034220", "name": "LG디스플레이",           "sector": "반도체/IT"},
    {"code": "022100", "name": "포스코DX",               "sector": "반도체/IT"},
    {"code": "064400", "name": "LG씨엔에스",             "sector": "반도체/IT"},
    {"code": "007660", "name": "이수페타시스",           "sector": "반도체/IT"},
    # 2차전지
    {"code": "373220", "name": "LG에너지솔루션",         "sector": "2차전지"},
    {"code": "006400", "name": "삼성SDI",                "sector": "2차전지"},
    {"code": "005490", "name": "POSCO홀딩스",            "sector": "2차전지"},
    {"code": "051910", "name": "LG화학",                 "sector": "2차전지"},
    {"code": "003670", "name": "포스코퓨처엠",           "sector": "2차전지"},
    {"code": "096770", "name": "SK이노베이션",           "sector": "2차전지"},
    {"code": "450080", "name": "에코프로머티",           "sector": "2차전지"},
    {"code": "066970", "name": "엘앤에프",               "sector": "2차전지"},
    {"code": "361610", "name": "SK아이이테크놀로지",     "sector": "2차전지"},
    {"code": "011790", "name": "SKC",                    "sector": "2차전지"},
    # 바이오/제약
    {"code": "207940", "name": "삼성바이오로직스",       "sector": "바이오/제약"},
    {"code": "068270", "name": "셀트리온",               "sector": "바이오/제약"},
    {"code": "0126Z0", "name": "삼성에피스홀딩스",       "sector": "바이오/제약"},
    {"code": "000100", "name": "유한양행",               "sector": "바이오/제약"},
    {"code": "326030", "name": "SK바이오팜",             "sector": "바이오/제약"},
    {"code": "302440", "name": "SK바이오사이언스",       "sector": "바이오/제약"},
    {"code": "128940", "name": "한미약품",               "sector": "바이오/제약"},
    {"code": "008930", "name": "한미사이언스",           "sector": "바이오/제약"},
    {"code": "003220", "name": "대웅제약",               "sector": "바이오/제약"},
    {"code": "009420", "name": "한올바이오파마",         "sector": "바이오/제약"},
    {"code": "006280", "name": "녹십자",                 "sector": "바이오/제약"},
    {"code": "137310", "name": "에스디바이오센서",       "sector": "바이오/제약"},
    {"code": "005250", "name": "녹십자홀딩스",           "sector": "바이오/제약"},
    {"code": "069620", "name": "대웅",                   "sector": "바이오/제약"},
    {"code": "001630", "name": "종근당",                 "sector": "바이오/제약"},
    # 조선/해운
    {"code": "267250", "name": "HD현대중공업",           "sector": "조선/해운"},
    {"code": "042660", "name": "한화오션",               "sector": "조선/해운"},
    {"code": "009540", "name": "HD한국조선해양",         "sector": "조선/해운"},
    {"code": "010140", "name": "삼성중공업",             "sector": "조선/해운"},
    {"code": "011200", "name": "HMM",                    "sector": "조선/해운"},
    {"code": "028670", "name": "팬오션",                 "sector": "조선/해운"},
    {"code": "443060", "name": "HD현대마린솔루션",       "sector": "조선/해운"},
    {"code": "071970", "name": "HD현대마린엔진",         "sector": "조선/해운"},
    # 방산/우주
    {"code": "012450", "name": "한화에어로스페이스",     "sector": "방산/우주"},
    {"code": "064350", "name": "현대로템",               "sector": "방산/우주"},
    {"code": "272210", "name": "한화시스템",             "sector": "방산/우주"},
    {"code": "047810", "name": "한국항공우주",           "sector": "방산/우주"},
    {"code": "079550", "name": "LIG넥스원",              "sector": "방산/우주"},
    # 자동차/부품
    {"code": "005380", "name": "현대차",                 "sector": "자동차/부품"},
    {"code": "000270", "name": "기아",                   "sector": "자동차/부품"},
    {"code": "012330", "name": "현대모비스",             "sector": "자동차/부품"},
    {"code": "161390", "name": "한국타이어앤테크놀로지", "sector": "자동차/부품"},
    {"code": "018880", "name": "한온시스템",             "sector": "자동차/부품"},
    {"code": "204320", "name": "HL만도",                 "sector": "자동차/부품"},
    {"code": "011810", "name": "에스엘",                 "sector": "자동차/부품"},
    {"code": "000240", "name": "한국앤컴퍼니",           "sector": "자동차/부품"},
    {"code": "073240", "name": "금호타이어",             "sector": "자동차/부품"},
    {"code": "007340", "name": "DN오토모티브",           "sector": "자동차/부품"},
    {"code": "011210", "name": "현대위아",               "sector": "자동차/부품"},
    {"code": "004490", "name": "세방전지",               "sector": "자동차/부품"},
    # AI전력/설비
    {"code": "034020", "name": "두산에너빌리티",         "sector": "AI전력/설비"},
    {"code": "267260", "name": "HD현대일렉트릭",         "sector": "AI전력/설비"},
    {"code": "010120", "name": "LS ELECTRIC",            "sector": "AI전력/설비"},
    {"code": "298040", "name": "효성중공업",             "sector": "AI전력/설비"},
    {"code": "052690", "name": "한전기술",               "sector": "AI전력/설비"},
    {"code": "001440", "name": "대한전선",               "sector": "AI전력/설비"},
    {"code": "062040", "name": "산일전기",               "sector": "AI전력/설비"},
    {"code": "112610", "name": "씨에스윈드",             "sector": "AI전력/설비"},
    {"code": "051600", "name": "한전KPS",                "sector": "AI전력/설비"},
    # 중공업/기계
    {"code": "329180", "name": "HD현대",                 "sector": "중공업/기계"},
    {"code": "000150", "name": "두산",                   "sector": "중공업/기계"},
    {"code": "241560", "name": "두산밥캣",               "sector": "중공업/기계"},
    {"code": "454910", "name": "두산로보틱스",           "sector": "중공업/기계"},
    {"code": "017800", "name": "현대엘리베이터",         "sector": "중공업/기계"},
    {"code": "082740", "name": "한화엔진",               "sector": "중공업/기계"},
    # 항공/물류
    {"code": "086280", "name": "현대글로비스",           "sector": "항공/물류"},
    {"code": "003490", "name": "대한항공",               "sector": "항공/물류"},
    {"code": "000120", "name": "CJ대한통운",             "sector": "항공/물류"},
    {"code": "180640", "name": "한진칼",                 "sector": "항공/물류"},
    # 엔터테인먼트
    {"code": "352820", "name": "하이브",                 "sector": "엔터테인먼트"},
    # 플랫폼/게임
    {"code": "402340", "name": "SK스퀘어",               "sector": "플랫폼/게임"},
    {"code": "035420", "name": "NAVER",                  "sector": "플랫폼/게임"},
    {"code": "035720", "name": "카카오",                 "sector": "플랫폼/게임"},
    {"code": "259960", "name": "크래프톤",               "sector": "플랫폼/게임"},
    {"code": "323410", "name": "카카오뱅크",             "sector": "플랫폼/게임"},
    {"code": "377300", "name": "카카오페이",             "sector": "플랫폼/게임"},
    {"code": "036570", "name": "엔씨소프트",             "sector": "플랫폼/게임"},
    {"code": "251270", "name": "넷마블",                 "sector": "플랫폼/게임"},
    {"code": "192080", "name": "더블유게임즈",           "sector": "플랫폼/게임"},
    {"code": "030000", "name": "제일기획",               "sector": "플랫폼/게임"},
    # 금융/지주
    {"code": "028260", "name": "삼성물산",               "sector": "금융/지주"},
    {"code": "105560", "name": "KB금융",                 "sector": "금융/지주"},
    {"code": "055550", "name": "신한지주",               "sector": "금융/지주"},
    {"code": "032830", "name": "삼성생명",               "sector": "금융/지주"},
    {"code": "086790", "name": "하나금융지주",           "sector": "금융/지주"},
    {"code": "000810", "name": "삼성화재",               "sector": "금융/지주"},
    {"code": "316140", "name": "우리금융지주",           "sector": "금융/지주"},
    {"code": "034730", "name": "SK",                     "sector": "금융/지주"},
    {"code": "006800", "name": "미래에셋증권",           "sector": "금융/지주"},
    {"code": "138040", "name": "메리츠금융지주",         "sector": "금융/지주"},
    {"code": "024110", "name": "기업은행",               "sector": "금융/지주"},
    {"code": "034830", "name": "한국금융지주",           "sector": "금융/지주"},
    {"code": "039490", "name": "키움증권",               "sector": "금융/지주"},
    {"code": "005830", "name": "DB손해보험",             "sector": "금융/지주"},
    {"code": "000880", "name": "한화",                   "sector": "금융/지주"},
    {"code": "005940", "name": "NH투자증권",             "sector": "금융/지주"},
    {"code": "016360", "name": "삼성증권",               "sector": "금융/지주"},
    {"code": "006260", "name": "LS",                     "sector": "금융/지주"},
    {"code": "029780", "name": "삼성카드",               "sector": "금융/지주"},
    {"code": "078930", "name": "GS",                     "sector": "금융/지주"},
    {"code": "001040", "name": "CJ",                     "sector": "금융/지주"},
    {"code": "138930", "name": "BNK금융지주",            "sector": "금융/지주"},
    {"code": "004990", "name": "롯데지주",               "sector": "금융/지주"},
    {"code": "088350", "name": "한화생명",               "sector": "금융/지주"},
    {"code": "001450", "name": "현대해상",               "sector": "금융/지주"},
    {"code": "139130", "name": "iM금융지주",             "sector": "금융/지주"},
    {"code": "175330", "name": "JB금융지주",             "sector": "금융/지주"},
    {"code": "003550", "name": "LG",                     "sector": "금융/지주"},
    {"code": "002790", "name": "아모레퍼시픽홀딩스",     "sector": "금융/지주"},
    {"code": "001800", "name": "오리온홀딩스",           "sector": "금융/지주"},
    {"code": "009970", "name": "영원무역홀딩스",         "sector": "금융/지주"},
    # 철강/소재
    {"code": "010130", "name": "고려아연",               "sector": "철강/소재"},
    {"code": "047050", "name": "포스코인터내셔널",       "sector": "철강/소재"},
    {"code": "004020", "name": "현대제철",               "sector": "철강/소재"},
    {"code": "103140", "name": "풍산",                   "sector": "철강/소재"},
    {"code": "000670", "name": "영풍",                   "sector": "철강/소재"},
    {"code": "001430", "name": "세아베스틸지주",         "sector": "철강/소재"},
    {"code": "003030", "name": "세아제강지주",           "sector": "철강/소재"},
    # 화학/정유
    {"code": "010950", "name": "S-Oil",                  "sector": "화학/정유"},
    {"code": "009830", "name": "한화솔루션",             "sector": "화학/정유"},
    {"code": "011780", "name": "금호석유화학",           "sector": "화학/정유"},
    {"code": "011170", "name": "롯데케미칼",             "sector": "화학/정유"},
    {"code": "014680", "name": "한솔케미칼",             "sector": "화학/정유"},
    {"code": "457190", "name": "이수스페셜티케미컬",     "sector": "화학/정유"},
    {"code": "120110", "name": "코오롱인더",             "sector": "화학/정유"},
    {"code": "298030", "name": "HS효성첨단소재",         "sector": "화학/정유"},
    {"code": "298050", "name": "효성티앤씨",             "sector": "화학/정유"},
    {"code": "003240", "name": "태광산업",               "sector": "화학/정유"},
    {"code": "006650", "name": "대한유화",               "sector": "화학/정유"},
    {"code": "004000", "name": "롯데정밀화학",           "sector": "화학/정유"},
    {"code": "285130", "name": "SK케미칼",               "sector": "화학/정유"},
    {"code": "017960", "name": "한국카본",               "sector": "화학/정유"},
    {"code": "093370", "name": "후성",                   "sector": "화학/정유"},
    {"code": "268280", "name": "미원에스씨",             "sector": "화학/정유"},
    {"code": "002840", "name": "미원상사",               "sector": "화학/정유"},
    {"code": "008730", "name": "율촌화학",               "sector": "화학/정유"},
    {"code": "005420", "name": "코스모화학",             "sector": "화학/정유"},
    {"code": "069260", "name": "TKG휴켐스",              "sector": "화학/정유"},
    {"code": "010600", "name": "OCI홀딩스",              "sector": "화학/정유"},
    {"code": "014820", "name": "동원시스템즈",           "sector": "화학/정유"},
    # 유통/소비재
    {"code": "051900", "name": "LG생활건강",             "sector": "유통/소비재"},
    {"code": "033780", "name": "KT&G",                   "sector": "유통/소비재"},
    {"code": "090430", "name": "아모레퍼시픽",           "sector": "유통/소비재"},
    {"code": "021240", "name": "코웨이",                 "sector": "유통/소비재"},
    {"code": "097950", "name": "CJ제일제당",             "sector": "유통/소비재"},
    {"code": "271560", "name": "오리온",                 "sector": "유통/소비재"},
    {"code": "004370", "name": "농심",                   "sector": "유통/소비재"},
    {"code": "139480", "name": "이마트",                 "sector": "유통/소비재"},
    {"code": "004170", "name": "신세계",                 "sector": "유통/소비재"},
    {"code": "007070", "name": "GS리테일",               "sector": "유통/소비재"},
    {"code": "069960", "name": "현대백화점",             "sector": "유통/소비재"},
    {"code": "282330", "name": "BGF리테일",              "sector": "유통/소비재"},
    {"code": "023530", "name": "롯데쇼핑",               "sector": "유통/소비재"},
    {"code": "044820", "name": "코스맥스",               "sector": "유통/소비재"},
    {"code": "006040", "name": "동원산업",               "sector": "유통/소비재"},
    {"code": "008770", "name": "호텔신라",               "sector": "유통/소비재"},
    {"code": "114090", "name": "GKL",                    "sector": "유통/소비재"},
    {"code": "034230", "name": "파라다이스",             "sector": "유통/소비재"},
    {"code": "161890", "name": "한국콜마",               "sector": "유통/소비재"},
    {"code": "007310", "name": "오뚜기",                 "sector": "유통/소비재"},
    {"code": "000080", "name": "하이트진로",             "sector": "유통/소비재"},
    {"code": "005300", "name": "롯데칠성",               "sector": "유통/소비재"},
    {"code": "280360", "name": "롯데웰푸드",             "sector": "유통/소비재"},
    {"code": "278470", "name": "에이피알",               "sector": "유통/소비재"},
    {"code": "003230", "name": "삼양식품",               "sector": "유통/소비재"},
    {"code": "035250", "name": "강원랜드",               "sector": "유통/소비재"},
    {"code": "111770", "name": "영원무역",               "sector": "유통/소비재"},
    {"code": "009240", "name": "한샘",                   "sector": "유통/소비재"},
    {"code": "026960", "name": "동서",                   "sector": "유통/소비재"},
    {"code": "383220", "name": "F&F",                    "sector": "유통/소비재"},
    {"code": "001680", "name": "대상",                   "sector": "유통/소비재"},
    {"code": "081660", "name": "미스토홀딩스",           "sector": "유통/소비재"},
    {"code": "012750", "name": "에스원",                 "sector": "유통/소비재"},
    # 통신/인프라
    {"code": "017670", "name": "SK텔레콤",               "sector": "통신/인프라"},
    {"code": "030200", "name": "KT",                     "sector": "통신/인프라"},
    {"code": "032640", "name": "LG유플러스",             "sector": "통신/인프라"},
    {"code": "015760", "name": "한국전력",               "sector": "통신/인프라"},
    {"code": "036460", "name": "한국가스공사",           "sector": "통신/인프라"},
    {"code": "071320", "name": "지역난방공사",           "sector": "통신/인프라"},
    # 건설/건자재
    {"code": "000720", "name": "현대건설",               "sector": "건설/건자재"},
    {"code": "028100", "name": "삼성E&A",                "sector": "건설/건자재"},
    {"code": "002380", "name": "KCC",                    "sector": "건설/건자재"},
    {"code": "047040", "name": "대우건설",               "sector": "건설/건자재"},
    {"code": "375500", "name": "DL이앤씨",               "sector": "건설/건자재"},
    {"code": "006360", "name": "GS건설",                 "sector": "건설/건자재"},
    {"code": "000210", "name": "DL",                     "sector": "건설/건자재"},
    {"code": "003300", "name": "한일시멘트",             "sector": "건설/건자재"},
    {"code": "002020", "name": "아세아",                 "sector": "건설/건자재"},
]

# ── KOSDAQ150 빌트인 폴백 (150종목, 2026-01-23 기준, 시가총액순) ───────────────
_KOSDAQ150_BUILTIN: List[Dict[str, str]] = [
    # 바이오/제약
    {"code": "196170", "name": "알테오젠",               "sector": "바이오/제약"},
    {"code": "298380", "name": "에이비엘바이오",         "sector": "바이오/제약"},
    {"code": "000250", "name": "삼천당제약",             "sector": "바이오/제약"},
    {"code": "028300", "name": "HLB",                    "sector": "바이오/제약"},
    {"code": "141080", "name": "리가켐바이오",           "sector": "바이오/제약"},
    {"code": "087010", "name": "펩트론",                 "sector": "바이오/제약"},
    {"code": "214450", "name": "파마리서치",             "sector": "바이오/제약"},
    {"code": "214370", "name": "케어젠",                 "sector": "바이오/제약"},
    {"code": "214150", "name": "클래시스",               "sector": "바이오/제약"},
    {"code": "347850", "name": "디앤디파마텍",           "sector": "바이오/제약"},
    {"code": "310210", "name": "보로노이",               "sector": "바이오/제약"},
    {"code": "140410", "name": "메지온",                 "sector": "바이오/제약"},
    {"code": "145020", "name": "휴젤",                   "sector": "바이오/제약"},
    {"code": "237690", "name": "에스티팜",               "sector": "바이오/제약"},
    {"code": "068760", "name": "셀트리온제약",           "sector": "바이오/제약"},
    {"code": "226950", "name": "올릭스",                 "sector": "바이오/제약"},
    {"code": "039200", "name": "오스코텍",               "sector": "바이오/제약"},
    {"code": "290650", "name": "엘앤씨바이오",           "sector": "바이오/제약"},
    {"code": "195940", "name": "HK이노엔",               "sector": "바이오/제약"},
    {"code": "007390", "name": "네이처셀",               "sector": "바이오/제약"},
    {"code": "085660", "name": "차바이오텍",             "sector": "바이오/제약"},
    {"code": "096530", "name": "씨젠",                   "sector": "바이오/제약"},
    {"code": "328130", "name": "루닛",                   "sector": "바이오/제약"},
    {"code": "082270", "name": "젬백스",                 "sector": "바이오/제약"},
    {"code": "358570", "name": "지아이이노베이션",       "sector": "바이오/제약"},
    {"code": "086900", "name": "메디톡스",               "sector": "바이오/제약"},
    {"code": "336570", "name": "원텍",                   "sector": "바이오/제약"},
    {"code": "086450", "name": "동국제약",               "sector": "바이오/제약"},
    {"code": "060280", "name": "큐렉소",                 "sector": "바이오/제약"},
    {"code": "048410", "name": "현대바이오",             "sector": "바이오/제약"},
    {"code": "025070", "name": "시노펙스",               "sector": "바이오/제약"},
    {"code": "053030", "name": "바이넥스",               "sector": "바이오/제약"},
    {"code": "200130", "name": "콜마비앤에이치",         "sector": "바이오/제약"},
    # 반도체/IT
    {"code": "247540", "name": "에코프로비엠",           "sector": "2차전지"},
    {"code": "086520", "name": "에코프로",               "sector": "2차전지"},
    {"code": "058470", "name": "리노공업",               "sector": "반도체/IT"},
    {"code": "240810", "name": "원익IPS",                "sector": "반도체/IT"},
    {"code": "039030", "name": "이오테크닉스",           "sector": "반도체/IT"},
    {"code": "403870", "name": "HPSP",                   "sector": "반도체/IT"},
    {"code": "357780", "name": "솔브레인",               "sector": "반도체/IT"},
    {"code": "095340", "name": "ISC",                    "sector": "반도체/IT"},
    {"code": "005290", "name": "동진쎄미켐",             "sector": "반도체/IT"},
    {"code": "064760", "name": "티씨케이",               "sector": "반도체/IT"},
    {"code": "098460", "name": "고영",                   "sector": "반도체/IT"},
    {"code": "036710", "name": "심텍",                   "sector": "반도체/IT"},
    {"code": "178320", "name": "서진시스템",             "sector": "반도체/IT"},
    {"code": "084370", "name": "유진테크",               "sector": "반도체/IT"},
    {"code": "065350", "name": "신성델타테크",           "sector": "반도체/IT"},
    {"code": "067310", "name": "하나마이크론",           "sector": "반도체/IT"},
    {"code": "140860", "name": "파크시스템스",           "sector": "반도체/IT"},
    {"code": "036930", "name": "주성엔지니어링",         "sector": "반도체/IT"},
    {"code": "261660", "name": "태성",                   "sector": "반도체/IT"},
    {"code": "101490", "name": "에스앤에스텍",           "sector": "반도체/IT"},
    {"code": "089030", "name": "테크윙",                 "sector": "반도체/IT"},
    {"code": "031980", "name": "피에스케이홀딩스",       "sector": "반도체/IT"},
    {"code": "204270", "name": "제이앤티씨",             "sector": "반도체/IT"},
    {"code": "232140", "name": "와이씨",                 "sector": "반도체/IT"},
    {"code": "319400", "name": "피에스케이",             "sector": "반도체/IT"},
    {"code": "161580", "name": "필옵틱스",               "sector": "반도체/IT"},
    {"code": "166090", "name": "하나머티리얼즈",         "sector": "반도체/IT"},
    {"code": "131970", "name": "두산테스나",             "sector": "반도체/IT"},
    {"code": "183300", "name": "코미코",                 "sector": "반도체/IT"},
    {"code": "080220", "name": "제주반도체",             "sector": "반도체/IT"},
    {"code": "056190", "name": "에스에프에이",           "sector": "반도체/IT"},
    {"code": "095610", "name": "테스",                   "sector": "반도체/IT"},
    {"code": "218410", "name": "RFHIC",                  "sector": "반도체/IT"},
    {"code": "213420", "name": "덕산네오룩스",           "sector": "반도체/IT"},
    {"code": "036540", "name": "SFA반도체",              "sector": "반도체/IT"},
    {"code": "036830", "name": "솔브레인홀딩스",         "sector": "반도체/IT"},
    {"code": "189300", "name": "인텔리안테크",           "sector": "반도체/IT"},
    {"code": "131290", "name": "티에스이",               "sector": "반도체/IT"},
    {"code": "036810", "name": "에프에스티",             "sector": "반도체/IT"},
    {"code": "348210", "name": "넥스틴",                 "sector": "반도체/IT"},
    {"code": "399720", "name": "가온칩스",               "sector": "반도체/IT"},
    {"code": "214430", "name": "아이쓰리시스템",         "sector": "반도체/IT"},
    {"code": "074600", "name": "원익QnC",                "sector": "반도체/IT"},
    {"code": "171090", "name": "선익시스템",                 "sector": "반도체/IT"},
    {"code": "272290", "name": "이녹스첨단소재",         "sector": "반도체/IT"},
    {"code": "253590", "name": "네오셈",                 "sector": "반도체/IT"},
    {"code": "079370", "name": "제우스",                 "sector": "반도체/IT"},
    {"code": "059090", "name": "미코",                   "sector": "반도체/IT"},
    {"code": "179900", "name": "유티아이",               "sector": "반도체/IT"},
    {"code": "046890", "name": "서울반도체",             "sector": "반도체/IT"},
    # 2차전지
    {"code": "348370", "name": "엔켐",                   "sector": "2차전지"},
    {"code": "281740", "name": "레이크머티리얼즈",       "sector": "2차전지"},
    {"code": "078600", "name": "대주전자재료",           "sector": "2차전지"},
    {"code": "417200", "name": "LS머트리얼즈",           "sector": "2차전지"},
    {"code": "009520", "name": "포스코엠텍",             "sector": "2차전지"},
    {"code": "137400", "name": "피엔티",                 "sector": "2차전지"},
    {"code": "222080", "name": "씨아이에스",             "sector": "2차전지"},
    {"code": "121600", "name": "나노신소재",             "sector": "2차전지"},
    {"code": "365590", "name": "성일하이텍",             "sector": "2차전지"},
    {"code": "278280", "name": "천보",                   "sector": "2차전지"},
    {"code": "383310", "name": "에코프로에이치엔",       "sector": "2차전지"},
    {"code": "101360", "name": "에코앤드림",             "sector": "2차전지"},
    # 중공업/기계
    {"code": "277810", "name": "레인보우로보틱스",       "sector": "중공업/기계"},
    {"code": "108490", "name": "로보티즈",               "sector": "중공업/기계"},
    {"code": "058610", "name": "에스피지",               "sector": "중공업/기계"},
    {"code": "239010", "name": "하이젠알앤엠",           "sector": "중공업/기계"},
    {"code": "083650", "name": "비에이치아이",           "sector": "중공업/기계"},
    {"code": "321260", "name": "클로봇",                 "sector": "중공업/기계"},
    {"code": "388790", "name": "유일로보틱스",           "sector": "중공업/기계"},
    {"code": "056080", "name": "유진로봇",               "sector": "중공업/기계"},
    {"code": "014620", "name": "성광벤드",               "sector": "중공업/기계"},
    # 엔터테인먼트
    {"code": "041510", "name": "에스엠",                 "sector": "엔터테인먼트"},
    {"code": "035900", "name": "JYP Ent.",               "sector": "엔터테인먼트"},
    {"code": "035760", "name": "CJ ENM",                 "sector": "엔터테인먼트"},
    {"code": "253450", "name": "스튜디오드래곤",         "sector": "엔터테인먼트"},
    {"code": "122870", "name": "와이지엔터테인먼트",     "sector": "엔터테인먼트"},
    {"code": "376300", "name": "디어유",                 "sector": "엔터테인먼트"},
    # 플랫폼/게임
    {"code": "263750", "name": "펄어비스",               "sector": "플랫폼/게임"},
    {"code": "293490", "name": "카카오게임즈",           "sector": "플랫폼/게임"},
    {"code": "112040", "name": "위메이드",               "sector": "플랫폼/게임"},
    {"code": "042000", "name": "카페24",                 "sector": "플랫폼/게임"},
    {"code": "041140", "name": "넥슨게임즈",             "sector": "플랫폼/게임"},
    {"code": "067160", "name": "SOOP",                   "sector": "플랫폼/게임"},
    {"code": "032190", "name": "다우데이타",             "sector": "플랫폼/게임"},
    {"code": "052400", "name": "코나아이",               "sector": "플랫폼/게임"},
    {"code": "060250", "name": "NHN KCP",                "sector": "플랫폼/게임"},
    {"code": "053800", "name": "안랩",                   "sector": "플랫폼/게임"},
    {"code": "095660", "name": "네오위즈",               "sector": "플랫폼/게임"},
    {"code": "030520", "name": "한글과컴퓨터",           "sector": "플랫폼/게임"},
    {"code": "101730", "name": "위메이드맥스",           "sector": "플랫폼/게임"},
    {"code": "069080", "name": "웹젠",                   "sector": "플랫폼/게임"},
    {"code": "078340", "name": "컴투스",                 "sector": "플랫폼/게임"},
    {"code": "194480", "name": "데브시스터즈",           "sector": "플랫폼/게임"},
    {"code": "304100", "name": "솔트룩스",               "sector": "플랫폼/게임"},
    {"code": "108600", "name": "셀바스AI",               "sector": "플랫폼/게임"},
    {"code": "058970", "name": "엠로",                   "sector": "플랫폼/게임"},
    # 유통/소비재
    {"code": "257720", "name": "실리콘투",               "sector": "유통/소비재"},
    {"code": "003380", "name": "하림지주",               "sector": "유통/소비재"},
    {"code": "241710", "name": "코스메카코리아",         "sector": "유통/소비재"},
    {"code": "025980", "name": "아난티",                 "sector": "유통/소비재"},
    {"code": "251970", "name": "펌텍코리아",             "sector": "유통/소비재"},
    {"code": "018290", "name": "브이티",                 "sector": "유통/소비재"},
    {"code": "036620", "name": "감성코퍼레이션",         "sector": "유통/소비재"},
    {"code": "352480", "name": "씨앤씨인터내셔널",       "sector": "유통/소비재"},
    {"code": "215200", "name": "메가스터디교육",         "sector": "유통/소비재"},
    {"code": "215000", "name": "골프존",                 "sector": "유통/소비재"},
    # AI전력/설비
    {"code": "032820", "name": "우리기술",               "sector": "AI전력/설비"},
    {"code": "033100", "name": "제룡전기",               "sector": "AI전력/설비"},
    # 금융/지주
    {"code": "006730", "name": "서부T&D",                "sector": "금융/지주"},
    {"code": "041190", "name": "우리기술투자",           "sector": "금융/지주"},
    {"code": "211050", "name": "인카금융서비스",         "sector": "금융/지주"},
    # 통신/인프라
    {"code": "032500", "name": "케이엠더블유",           "sector": "통신/인프라"},
    {"code": "050890", "name": "쏠리드",                 "sector": "통신/인프라"},
    # 조선/해운
    {"code": "403450", "name": "LS마린솔루션",           "sector": "조선/해운"},
    # 자동차/부품
    {"code": "015750", "name": "성우하이텍",             "sector": "자동차/부품"},
    # 화학/정유
    {"code": "025440", "name": "동성화인텍",             "sector": "화학/정유"},
    {"code": "025900", "name": "동화기업",               "sector": "화학/정유"},
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


def _load_etf() -> List[Dict[str, str]]:
    """ETF 캐시(etf_cache.json) 로드. 없으면 etf_watchlist.py 정적 목록 폴백."""
    try:
        data = json.loads(_ETF_CACHE.read_text(encoding="utf-8"))
        etfs = data.get("etfs", [])
        if len(etfs) >= 10:
            return etfs
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return ETF_LIST


def get_active_watchlist() -> List[Dict[str, str]]:
    """S1/S2/S3 공통 유니버스: KOSPI200 + KOSDAQ150 + ETF (code 중복 제거)"""
    return get_s2_watchlist()


def get_s2_watchlist() -> List[Dict[str, str]]:
    """S2(MA전략) + S3(CANSLIM) + DART배치용: KOSPI200 + KOSDAQ150 + ETF 통합 (code 중복 제거)"""
    seen: set = set()
    merged: List[Dict[str, str]] = []
    for item in _load_kospi200() + _load_kosdaq150() + _load_etf():
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
