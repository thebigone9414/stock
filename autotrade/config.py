"""autotrade 설정 로더

config.yaml을 Python dict로 로드. 모든 값은 config.yaml 수정으로 조정.
pydantic 없이 가벼운 dict 기반.
"""
from __future__ import annotations

import yaml
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "autotrade" / "config.yaml"


@lru_cache(maxsize=1)
def load() -> dict:
    """config.yaml을 1회 로드하여 캐시. 변경 시 프로세스 재시작 필요."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get(key_path: str, default: Any = None) -> Any:
    """'capital.initial' 같은 점 표기법으로 값 조회."""
    cfg = load()
    cur: Any = cfg
    for key in key_path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def abspath(rel: str) -> Path:
    """config 경로를 레포 루트 기준 절대경로로 변환."""
    return ROOT / rel
