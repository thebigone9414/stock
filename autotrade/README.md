# autotrade — 월요일 가동 가이드

**첫 가동**: 2026-10-12(월) 20:10 (판정) → 10-13(화) 15:18 (ETF pat#7 매수)

## 1. 사전 체크 (10/10 금 ~ 10/11 토)

### 1-1. GitHub Secrets 확인
Repository Settings → Secrets and variables → Actions 에 다음이 등록돼 있어야 함:
- `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT_NO` — 한국투자증권 API 키
- `KIS_IS_PAPER_TRADING` — `"true"` (모의) / `"false"` (실전). **첫 2주는 "true" 또는 config.yaml `dry_run: true`**
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — 보고 수신용

### 1-2. 수집 실패 종목 5개 조사 (중요)
engine_v3 데이터에 추가 못한 5종목: 025070, 108600, 239010, 261660, 403450
- 상장폐지·종목코드 변경이면 → `data/kospi200_cache.json` / `data/kosdaq150_cache.json` 에서 제거
- 또는 월요일 datafeed가 KIS로 당일 데이터 받아올 때 자동 처리됨 (당일 결측률 5% 이하면 통과)

### 1-3. positions 상태 확인
```bash
cat engine_v3/positions_v3.csv     # 헤더만 (빈 포지션)
cat engine_v3/positions_stock.csv  # 헤더만 (빈 포지션)
```
수동 보유 종목(KODEX 2차전지·현대차·두산·삼성E&A 등)은 **engine_v3 포지션에 넣지 않음**
→ 이 자동매매 시스템은 ETF 11패턴·종목 신호 전용. 수동 종목은 사용자가 직접 관리.

### 1-4. 레거시 cron 비활성화 확인
- cron-job.org 에서 **옛 S2/S3/S4/S5 트리거 전부 disable 확인** (사용자가 이미 조치함)
- `.github/workflows/legacy_trading.yml` (= trading.yml) 은 workflow_dispatch만, cron 없음

## 2. cron-job.org 등록 (평일 KST, Mon-Fri)

GitHub Actions workflow_dispatch 호출 URL:
```
POST https://api.github.com/repos/thebigone9414/stock/actions/workflows/autotrade.yml/dispatches
Headers:
  Authorization: Bearer <PAT with workflow scope>
  Accept: application/vnd.github+json
Body:
  {"ref":"dev","inputs":{"cmd":"<CMD>"}}
```

| KST 시각 | cmd | 설명 |
|---------|-----|------|
| 08:20 | reconcile | 잔고 대사 |
| 08:50 | sell | 매도 집행 |
| 08:55 | stop-register | STOP 하이브리드 등록 |
| 09:05 | stop-check | 체크포인트 1 |
| 10:00 | stop-check | 체크포인트 2 |
| 11:00 | stop-check | 체크포인트 3 |
| 12:00 | stop-check | 체크포인트 4 |
| 13:00 | stop-check | 체크포인트 5 |
| 14:00 | stop-check | 체크포인트 6 |
| 15:00 | stop-check | 체크포인트 7 |
| 15:15 | stop-check | 체크포인트 8 |
| 15:18 | buy | 매수 집행 |
| 15:35 | stop-confirm | STOP 체결 확인 |
| 20:10 | nightly | 데이터 수집 + 판정 + 보고 |

**간소화 옵션**: 체크포인트가 많으면 `config.yaml` `stoploss.checkpoints` 리스트에서 줄여서 커밋 → cron도 해당 시각만 등록.

## 3. 운영 모드

### 3-1. Dry-run (2주 권장)
`autotrade/config.yaml`:
```yaml
safety:
  dry_run: true
```
→ 실제 주문 접수 안 함. 로그·텔레그램 보고·상태 파일 전부 정상 동작. 금요일 저녁에 DRY-RUN 플래그만 떼면 실전 전환.

### 3-2. 중지 스위치
```bash
touch autotrade/STOP   # 매수 전면 중지, 매도·손절만 수행
rm autotrade/STOP      # 재개
```

## 4. 월요일 10/12 판정 미리보기

**10/8 데이터 기준** engine 테스트 결과:
```
[자동매매 10/12 20:10]  코스피200 1,044.52 (-2.71%)  국면: 하락 (62일선 1,064.7 / 20일 전 1,148.1)

■ ETF 규칙  배분 28,000,000원
  보유  없음
  내일 매도  없음
  내일 매수 (1건):
    [122630] KODEX 레버리지 #7 3일 연속 하락 + 248일선 위 + 60일 수익 음수
      금액 5,600,000원  손절선 940.07  신호일 10/08 (1,044.52)

■ 종목 규칙  배분 12,000,000원
  내일 매도  없음
  내일 매수  없음 (국면 하락)   참고 후보 7: 피에스케이홀딩스[B1,B2,B3] / ...

■ 내일 매수 필요 입금  총 5,600,000원
    ETF   5,600,000원
```

→ **월요일 10/12 저녁 입금 + 10/13(화) 15:18 KODEX 레버리지 자동 매수 (5.6M원)**

실제 10/12 월요일 종가가 반영된 판정 결과는 그날 저녁 20:10 알림으로 확인 (10/8 미리보기와 다를 수 있음).

## 5. 디버그 명령

```bash
# 로컬에서 현재 데이터로 판정만 (실제 주문 없음)
python -m autotrade --cmd judge

# 가장 최근 orders의 보고만 재생성
python -m autotrade --cmd report

# 특정일 리플레이 (검수용)
python -m autotrade --cmd judge --date 2026-07-28

# 데이터 수집 안 하고 판정만
python -m autotrade --cmd nightly --skip-datafeed
```

## 6. 긴급 대응

| 상황 | 조치 |
|------|------|
| 매도 체결 안 되고 "매도 대기" 상태 | 다음 거래일 08:50 자동 재시도 |
| 데이터 수집 실패 (20:10) | nightly 가 orders 저장 안 함 → 다음날 매수·매도 X. 수동 재실행 필요 |
| 잔고 대사 불일치 | 자동매매 중단 알림. 로컬 positions 수동 수정 후 재가동 |
| ETF 손절선 터치 (장중) | **그날은 아무것도 안 함** (v12 §2-1: ETF는 종가 판정). 20:10 판정에서 stop_hit=True → 다음날 08:50 매도 |
| 종목 손절선 터치 | 하이브리드 지정가 자동 체결 or 체크포인트 시장가 보강 |
| 전고점 -35% (ETF) / -45% (종목) 도달 | 신규 매수 자동 중지 + 알림. 재개는 수동 (STOP 파일 삭제 + config 수정) |
