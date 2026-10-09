"""
S2~S5+수동 공유 슬롯 카운터

S2+S3+S4+S5+수동 = 10슬롯 공유 풀 (기본, 자산 증가 시 슬롯 추가)
S1(옥동자) = 비활성화
"""


def count_shared() -> tuple:
    """S2~S5+수동 총 트랜치 수 반환 → (s2_n, s3_n, s4_n, s5_n, manual_n)
    같은 종목 다른 날짜 = 독립 트랜치 → 각각 1슬롯 점유
    """
    from data.ma_store import get_positions as _s2
    from data.canslim_store import load_positions as _s3
    from data.sepa_store import load_positions as _s4
    from data.momentum_store import load_positions as _s5
    from data.manual_store import load_positions as _manual

    def _n(p: dict) -> int:
        return sum(len(t) for t in p.values())

    return _n(_s2()), _n(_s3()), _n(_s4()), _n(_s5()), _n(_manual())


def total_shared() -> int:
    return sum(count_shared())
