# Portfolio Manager Agent — 기획 문서 v0.1 (2026-09-06)

> 상태: **플래닝 단계**. 코드 작성 전. 아래 "열린 질문"에 대한 답이 정해지면 v0.2로 갱신.
> 리서치는 2026-09-06 기준 웹/GitHub 조사이며, `[불확실]` 표시는 1차 출처를 직접 확인하지 못한 항목.

---

## 1. 목표

Fidelity 계좌의 **포지션 + 거래 활동(Activity)** 을 읽어 들여, 사용자가 병행 중인 여러 전략을
각각 평가하고, 사용자 성향에 맞는 **근거 있는 제안**을 투명하게 제시하는 개인용 에이전트 팀 앱.

평가 대상 전략(사용자 언급):
1. 롱텀 투자 (장기 보유)
2. 옵션 셀링 (CSP / CC / Wheel)
3. 숏 포지션
4. 레버리지 투자 (레버리지 ETF 등)

원칙:
- **v1은 읽기 전용(advisory)**. 주문 실행은 없음. 실행은 별도 단계에서, 사람 승인 게이트 뒤에.
- **숫자는 코드가 계산하고, LLM은 해석·우선순위·설명만** 한다. (FinRobot, ibkr-options-assistant 등 실무 프로젝트의 공통 결론)
- 모든 제안은 **어떤 데이터(as_of 시각 포함)에 근거했는지** 명시. 근거 없는 숫자가 들어간 메모는 리젝.
- 제안·수용/거절·N일 후 결과를 로그로 남겨 **에이전트 자체의 조언 품질을 나중에 채점**.

---

## 2. 리서치 요약

### 2.1 Fidelity 데이터 접근 (가장 큰 병목)

| 경로 | 가능 여부 | 비고 |
|---|---|---|
| Fidelity 공식 리테일 API | **없음** | 2026-08 기준 여전히 없음. Fidelity Access는 애그리게이터 전용(개인 등록 경로 발견 못 함) |
| **SnapTrade** (애그리게이터) | **가능해 보임** | Free 플랜: 1 user / 5 연결 / 개인용. Fidelity는 OAuth 연결, 읽기 전용. 옵션 보유(OCC 심볼) 및 `OPTIONASSIGNMENT/OPTIONEXPIRATION` 활동 타입이 API 스키마에 있음. **단, Fidelity 피드가 실제로 숏 옵션·배정 이벤트를 채워주는지는 미검증 `[불확실]`** → 샌드박스/무료 계정으로 먼저 확인 필요 |
| Plaid Investments | **불가** | Fidelity가 Plaid 네트워크에서 빠짐 (2023-11, Fidelity 공식 X 2025-12 재확인) |
| Akoya / Finicity / Yodlee / MX | 사실상 불가 | 기업 계약·보안심사 필요. 개인 개발자 사례 없음 |
| **CSV 수동 다운로드** | **가능, 확실함** | Positions CSV(16열) + Activity History CSV(12~13열). 옵션 거래·배정·만기 행이 **실제로 포함됨** (공개 레포의 실제 export로 확인). 한 파일당 약 90일 `[불확실]`, 최대 5년 소급 `[불확실]` |
| Playwright 스크래퍼 (`fidelity-api` 등) | **비추천** | 2026-06부터 로그인 차단 이슈 다수, ToS 위반, Customer Protection Guarantee 상실 |
| OFX Direct Connect | **불가** | 2025-12경 종료된 것으로 보임 |

Fidelity CSV 옵션 심볼 포맷 예: ` -TSLA250418P300` (앞에 공백+하이픈, 스트라이크 미패딩) → OCC `TSLA  250418P00300000` 로 변환 필요. 숏 포지션은 Quantity 음수.

**결론**: 1순위 SnapTrade(검증 후), 2순위(그리고 반드시 함께) CSV 임포트. 스크래퍼는 배제.

### 2.2 유사 프로젝트 (핵심만)

| 프로젝트 | 배울 점 | 한계 |
|---|---|---|
| TradingAgents (102k★, Apache) | 애널리스트 → Bull/Bear 토론 → 리스크팀 → PM 구조, 의사결정 로그, point-in-time 데이터 규율 | 티커 단위 매매 판단. 내 계좌·옵션·숏 개념 없음 |
| ai-hedge-fund (63k★, MIT) | "Ledger" 개념: 모든 사이클 기록, 백테스트 = 같은 루프 재생 | 유료 데이터 API 종속, 옵션 없음 |
| FinRobot (Apache) | **계산은 코드, LLM은 서술** 원칙, 데이터 공급자 failover | 리서치 노트북 중심 |
| thetagang (AGPL) | Wheel 롤링·배정·VIX 헤지 규칙의 성숙한 레퍼런스 | IBKR 전용, 설명 계층 없음, AGPL |
| ibkr-options-assistant (MIT) | 결정적 스크립트 → JSON → Claude 해석. 주문은 env var + `--confirm` 이중 게이트 | 소규모 |
| premium-tracker (MIT) | Wheel 지표 정의(프리미엄 캡처, 연환산 ROC, 배정률, 담보) | IBKR CSV 전용 |
| Ghostfolio (AGPL) | Activity 데이터 모델, MCP로 포트폴리오 노출 패턴 | 옵션/숏/레버리지 없음 |
| Alpaca 공식 MCP 서버 (MIT) | 80+ 툴, 옵션 Greeks, 활동 조회 | 이 레포의 Alpaca 페이퍼 계좌용으로 재사용 가능 |
| anthropics/financial-services | Wealth Management 스킬(`/client-review`, `/rebalance`, `/tlh`), "사람 승인 전 추천 금지" 원칙 | 기관용, 유료 데이터 커넥터 |

**핵심 인사이트**: "실계좌 활동 + 혼합 전략(옵션/숏/레버리지) 의미론 + 설명 가능한 에이전트 팀"을 end-to-end로 하는 프로젝트는 아직 없는 것으로 보임. 빈틈은 이 셋의 **결합**.

### 2.3 아키텍처·평가 방법론

- **오케스트레이션**: Claude Agent SDK(Python) 서브에이전트 + 훅. `PreToolUse` 훅으로 주문 툴 하드 차단, `PostToolUse` 훅으로 감사 로그, `output_format=json_schema`로 제안을 구조화. 필요 시 LangGraph는 나중에 승인 DAG용으로 병용 가능.
- **포트폴리오 지표**: `pyxirr`(MWR/XIRR), `empyrical-reloaded`/`quantstats`(TWR, Sharpe, MDD, 벤치마크), Fama-French 회귀(`statsmodels`), HHI 집중도.
- **옵션 지표**: `vollib`(Greeks/IV), `optionlab`(롤 시나리오·POP). 프리미엄 캡처, 연환산 ROC, 델타/IV Rank 버킷별 배정률, Wheel 사이클 P&L 분해, 포트폴리오 Δ/Θ/Vega($).
- **레버리지 ETF**: 변동성 손실 ≈ (L²−L)·σ²/2. 보유구간별 `LETF 누적수익 − L×기초자산 누적수익` 실측. 전용 라이브러리 없음, pandas로 직접 구현(수십 줄).
- **숏**: 대차 비용(iBorrowDesk), 숏 이자율/DTC(FINRA 무료 API), 마진콜까지 % 거리.
- **시장 데이터**: Alpaca(무료, 옵션 스냅샷 Greeks) + FRED(VIX·금리) + FINRA(숏 이자) + 필요 시 Tiingo/EODHD($20~30/월). yfinance는 fallback만.

---

## 3. 제안 아키텍처

```
portfolio/                         ← 새 패키지 (기존 bot/ 과 병행)
├── ingest/
│   ├── fidelity_csv.py            ← Positions/Activity CSV 파서 (옵션 심볼 정규화 포함)
│   ├── snaptrade_client.py        ← SnapTrade 읽기 전용 (검증 후)
│   ├── alpaca_source.py           ← 기존 bot의 Alpaca 페이퍼 계좌 (선택)
│   └── schema.py                  ← 정규화 모델: Account / Position / Activity / OptionLeg
├── analytics/                     ← 결정적 계산, LLM 없음
│   ├── performance.py             ← TWR / MWR / 벤치마크 / MDD
│   ├── longterm.py                ← 집중도, 섹터·팩터 노출, 보유기간별 수익
│   ├── options_income.py          ← Wheel 사이클 재구성, 프리미엄 캡처, ROC, 배정률, Greeks 집계
│   ├── shorts.py                  ← 대차비용, 숏 이자·DTC, 마진 거리
│   ├── leverage.py                ← 변동성 손실 실측, 보유기간 진단
│   └── risk.py                    ← 하드 룰 체크 (safety rules 이식)
├── agents/                        ← Claude Agent SDK
│   ├── team.py                    ← 서브에이전트 정의 + 훅 + 예산
│   ├── prompts/*.md               ← 역할별 프롬프트
│   └── schemas.py                 ← 제안/메모 JSON 스키마
├── memory/
│   ├── profile.yaml               ← 사용자 투자 성향·제약·목표 (인터뷰로 채움)
│   └── suggestions.jsonl          ← 제안 + 근거 + 수용/거절 + N일 후 결과
├── report/
│   └── render.py                  ← 일/주간 리포트 (Markdown → HTML)
└── cli.py                         ← `pm ingest`, `pm analyze`, `pm review`, `pm report`
```

### 에이전트 팀 (v1)

| 역할 | 툴 권한 | 산출물 |
|---|---|---|
| Data Steward | ingest/analytics 툴만 | 정규화된 스냅샷 + as_of 스탬프, 누락·오래된 데이터 명시 |
| Long-Term Analyst | 읽기 전용 | 집중도·팩터·보유 논리 점검 |
| Options-Income Analyst | 읽기 전용 | Wheel 사이클 평가, 롤 후보, 어닝 근접 경고 |
| Short/Leverage Analyst | 읽기 전용 | 대차비용·스퀴즈 위험, 변동성 손실 진단 |
| Macro/Regime Analyst | 읽기 전용 | VIX 존, 금리, 섹터 스트레스 |
| Bull/Bear Critic | 없음 | 각 제안에 반대 논거 필수 첨부 (evaluator-optimizer) |
| Risk Officer | `risk_check` 툴만 | `{veto, rule_violated}` 구조화 판정 → 훅이 강제 |
| Portfolio Manager | 없음 | 우선순위별 제안 메모(why / why_not / what_would_change_my_mind / confidence) |
| **사람(사용자)** | — | 승인/무시. v1에서는 아무것도 실행되지 않음 |

비용 통제: 데이터 수집·분석 에이전트는 Sonnet/Haiku, PM·Critic은 상위 모델. 실행당 `max_budget_usd` 상한.

---

## 4. 단계별 로드맵

| Phase | 내용 | 완료 기준 |
|---|---|---|
| **0. 검증 (1주)** | SnapTrade 무료 계정으로 Fidelity 연결 → 숏 옵션·배정 이벤트가 오는지 확인. 사용자가 CSV 2종(Positions, 90일 History) 샘플 제공 | SnapTrade 가/부 판정. CSV 파서 테스트 픽스처 확보 |
| **1. Ingest + 정규화** | CSV 파서(옵션 심볼 정규화, 배정/만기 매핑), 정규화 스키마, SQLite/JSONL 저장, 다중 파일 병합·중복 제거 | 내 실제 데이터로 포지션·활동이 정확히 재구성됨 |
| **2. Analytics (LLM 없음)** | 4개 전략별 지표 모듈 + 성과(TWR/MWR) + 리스크 룰 | 숫자 리포트만으로도 유용한 상태. 단위 테스트 |
| **3. Agent Team (advisory)** | Claude Agent SDK 팀, 구조화 제안, 감사 로그, 투자 성향 인터뷰(profile.yaml) | 주 1회 리뷰 메모 생성. 모든 숫자에 근거 ID |
| **4. 리포트·스케줄** | GitHub Actions 주간 실행(기존 패턴 재사용) 또는 Claude Code Routines, HTML 리포트 | 자동 주간 리포트 수신 |
| **5. (선택) 실행 연동** | Alpaca 페이퍼에서만, 이중 승인 게이트. Fidelity는 API가 없어 실행 불가 | 별도 리스크 리뷰 후 결정 |

---

## 5. 기존 레포와의 관계

- 기존 `bot/`(Alpaca 페이퍼 Wheel 실행 봇)은 그대로 두고, `portfolio/`를 병행 패키지로 추가하는 방향 제안.
- 재사용: `data_logger.py`의 JSONL 패턴, `config.py`의 safety rules(→ `analytics/risk.py`), GitHub Actions 스케줄 패턴, VIX 존 로직.
- 기존 봇의 Alpaca 페이퍼 계좌도 하나의 데이터 소스로 붙여 "봇 전략 평가"도 같은 프레임으로 가능.

### 즉시 조치 권고 (보안)
- `bot/config.py`에 Alpaca 페이퍼 API 키/시크릿이 **하드코딩되어 커밋**돼 있음. 페이퍼 계정이라도 키 회전 후 코드에서 제거하고 env var만 쓰는 것을 권장.
- Fidelity 데이터(CSV, SnapTrade 토큰)는 절대 레포에 커밋하지 않도록 `.gitignore` + 로컬 암호화 저장 설계 필요.

---

## 6. 열린 질문 (사용자 답 필요)

1. **데이터 경로**: SnapTrade 무료 계정을 만들어 Fidelity를 연결해 볼 의향이 있는지? (OAuth라 비밀번호 공유는 아님) 아니면 CSV 수동 다운로드만으로 시작할지?
2. **CSV 샘플**: Positions CSV와 최근 90일 Activity CSV를 (계좌번호 등 마스킹 후) 제공 가능한지? 파서·테스트의 기준 데이터가 됨.
3. **계좌 범위**: Fidelity 계좌 수(과세/IRA 등), 그리고 Alpaca 페이퍼 봇 계좌도 같은 리뷰에 포함할지?
4. **제안의 깊이**: "이 포지션 줄여라/롤해라" 수준의 구체 액션까지 원하는지, 아니면 진단·경고·리밸런스 방향 제시까지인지?
5. **레버리지 투자**의 형태: 레버리지 ETF(TQQQ 등)인지, 마진 매수인지, 둘 다인지?
6. **리뷰 주기와 채널**: 주간 리포트로 충분한지, 일간도 필요한지? 결과를 어디서 보고 싶은지(HTML 리포트, 채팅, 노션 등)?
7. **레포 구조**: 이 레포에 `portfolio/` 패키지로 병행 vs 새 레포 분리?
8. **개인정보**: 분석에 LLM(Anthropic API)이 쓰이므로 보유 종목·금액이 API로 전송됨. 금액을 비율로 마스킹해서 보낼지, 그대로 보낼지?

---

## 7. 주요 리스크·미확인 사항

- SnapTrade의 Fidelity 피드에서 옵션/배정 데이터 품질 `[불확실]` → Phase 0에서 판정.
- Fidelity CSV 다운로드 범위(90일/5년) `[불확실]` → UI에서 직접 확인.
- Claude Code Routines는 리서치 프리뷰 단계 → 스케줄은 GitHub Actions를 기본으로.
- LLM 재무 에이전트 벤치마크(Vals AI Finance Agent)에서 상위 모델도 정답률 60%대 → **advisory-only + 코드 계산 + 근거 강제**가 필수인 이유.
- 법적: 이 앱은 개인용 도구이며 투자 자문(RIA)이 아님. 리포트에 면책 문구 고정.

---

## 부록: 주요 출처
- Fidelity API 부재: https://blog.traderspost.io/article/does-fidelity-have-an-api
- SnapTrade Fidelity: https://snaptrade.com/brokerage-integrations/fidelity-api , https://snaptrade.com/pricing , https://docs.snaptrade.com/reference/Options/Options_listOptionHoldings
- Fidelity CSV 실제 포맷(옵션 행 포함): https://github.com/brooksbol/options-prototype , https://github.com/earlisreal/eJournal , https://github.com/hbchoi0917/trading
- fidelity-api 스크래퍼 이슈: https://github.com/kennyboy106/fidelity-api/issues
- TradingAgents: https://github.com/TauricResearch/TradingAgents ; ai-hedge-fund: https://github.com/virattt/ai-hedge-fund ; FinRobot: https://github.com/AI4Finance-Foundation/FinRobot
- thetagang: https://github.com/brndnmtthws/thetagang ; ibkr-options-assistant: https://github.com/AlexLiu0130/ibkr-options-assistant ; premium-tracker: https://github.com/Marfusios/premium-tracker
- Alpaca MCP: https://github.com/alpacahq/alpaca-mcp-server ; anthropics/financial-services: https://github.com/anthropics/financial-services
- Claude Agent SDK: https://code.claude.com/docs/en/agent-sdk/overview (subagents / hooks / permissions / structured-outputs)
- Building effective agents: https://www.anthropic.com/engineering/building-effective-agents
- 라이브러리: https://github.com/Anexen/pyxirr , https://pypi.org/project/quantstats/ , https://pypi.org/project/vollib/ , https://github.com/rgaveiga/optionlab , https://pypi.org/project/riskfolio-lib/
- 레버리지 ETF decay: https://www.leveraged-etfs.com/education/decay ; FINRA 숏 이자: https://www.finra.org/finra-data/browse-catalog/equity-short-interest
