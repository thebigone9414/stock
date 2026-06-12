"""
S2~S4 공유 슬롯 카운터

S2+S3+S4 = 10슬롯 공유 풀 (기본, 자산 증가 시 슬롯 추가)
S1(옥동자) = 비활성화
"""


def count_shared() -> tuple:
    """S2~S4 각 포지션 수 반환 → (s2_n, s3_n, s4_n)"""
    from data.ma_store import get_positions as _s2
    from data.canslim_store import load_positions as _s3
    from data.sepa_store import load_positions as _s4
    return len(_s2()), len(_s3()), len(_s4())


def total_shared() -> int:
    return sum(count_shared())
