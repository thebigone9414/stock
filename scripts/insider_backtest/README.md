# Insider Form 4 백테스트 데이터 추출 v1

**지시서**: `자동매매 구현 지시서 v1 (2026-10-17)`. 미국 Form 4 공개시장 매수(P)가 이후 수익률을 예측하는지 20년치로 검증하기 위한 데이터만 추출. 규칙 설계·백테스트는 별도.

## 구조

```
scripts/insider_backtest/
  common.py          — SEC HTTP (UA, rate limit), 로깅
  download_form4.py  — SEC Insider Transactions Data Sets 분기 zip 다운로드 → raw/
  parse_form4.py     — SUBMISSION+REPORTINGOWNER+NONDERIV_TRANS+FOOTNOTES 조인
                        → out/insider_P.csv.gz, out/insider_S_agg.csv.gz
  fetch_prices.py    — yfinance 200개씩 다운 → prices/{T}.parquet + out/prices_bench.csv.gz
  build_events.py    — out/events.csv.gz + out/paths.npz
  verify.py          — out/README.md + out/ticker_mismatch.csv
  run_all.py         — 전체 파이프라인 + zip 패키징
  out/               — 산출물 (gitignore)
  raw/               — SEC zip + 압축 해제 txt (gitignore)
  prices/            — yfinance parquet 캐시 (gitignore)
  logs/              — 로그 (gitignore)
```

## 실행

### GitHub Actions (권장)
1. Actions → **"Insider Form 4 백테스트 데이터 추출"** → Run workflow → **mode: all** (약 1~3시간)
2. 완료 후 artifact `insider-backtest-all-<run_id>.zip` 다운로드

mode별 재실행도 가능 (디버그·중단 복구):
- `download` — SEC raw zip만
- `parse` — raw → insider_P/S csv.gz
- `fetch` — yfinance 가격 캐시
- `build` — events + paths
- `verify` — README + ticker_mismatch

### 로컬 (디버그용)
```bash
cd scripts/insider_backtest
pip install pandas numpy requests yfinance pyarrow
export SEC_USER_AGENT="Your Name you@email.com"
python run_all.py
```

## 산출물 (`out/`)

| 파일 | 내용 |
|------|------|
| `insider_P.csv.gz` | 공개시장 매수 원장 (1행 = 거래) — 지시서 §2-1 전체 열 |
| `insider_S_agg.csv.gz` | 매도 집계 (issuer × trans_date) — 지시서 §2-4 |
| `events.csv.gz` | 공시 단위 사건 (1행 = accession × issuer) + 가격 열 — 지시서 §2-2 |
| `paths.npz` | 사건별 130거래일 수정종가 경로 (float16) + SPY/IWM 동일 기간 |
| `prices_bench.csv.gz` | SPY, IWM 일별 Date/Open/Close/Adj Close |
| `ticker_mismatch.csv` | yfinance shortName vs issuer_name 첫 단어 불일치 종목 |
| `README.md` | 검증 리포트 (연도별 행 수, JPM Dimon 재현, SPY 샘플 등) |
| `insider_backtest_data_YYYYMMDD.zip` | 위 전부 묶음 + 코드 복사본 |

## 환경 요구

- Python 3.10+
- 패키지: pandas, numpy, requests, yfinance, pyarrow
- SEC rate limit: 초당 10회 이하 (`common.SECSession` 이 자동 처리)
- SEC User-Agent 필수 (환경변수 `SEC_USER_AGENT`)
- 디스크: raw ~4GB, prices ~1~2GB, out ~500MB

## 제약 (지시서 §4)

- 규칙 판단·점수·포트폴리오 계산 **없음** (별도 단계)
- 파생상품 표(DERIV_TRANS) **사용 안 함**
- 시가총액 **없음** (대신 adv20_usd, close_t0 활용)
- 2026년 4분기 실시간 공시(EDGAR XML) **없음**
