# AI 트레이딩 봇 (강화학습 + HMM 국면탐지 + 실시간 API)

두 가지 서로 다른 전략을 담고 있습니다.

1. **`train.py`** — 기술적 지표를 입력으로 강화학습(DQN) 에이전트가 매수·현금 판단을
   스스로 학습합니다. "다음날 방향을 맞히는" 접근입니다.
2. **`train_regime.py`** — 가우시안 HMM(은닉 마르코프 모델)을 NumPy로 직접 구현해
   시장 국면(상승/하락/횡보)을 탐지하고, 변동성 타겟팅으로 포지션 **크기**를 조절합니다.
   방향을 맞히지 않고, 국면별 변동성에 맞춰 노출도만 조절하는 접근입니다.

두 전략 모두 거래비용을 반영해 백테스트하고, Alpaca 페이퍼 트레이딩으로 실전(모의)
매매까지 연결할 수 있는 학습·연구용 트레이딩 봇입니다.

> **면책** — 이 프로젝트는 교육·연구 목적입니다. 투자 조언이 아니며,
> 백테스트 성과가 미래 수익을 보장하지 않습니다. 실거래 전 충분한 검증이 필요합니다.

## 빠른 시작

```bash
# 1) 가상환경 (권장)
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2) 핵심 의존성 설치
pip install -r requirements.txt

# 3) 바로 실행 (인터넷 없이 합성 데이터로 학습+백테스트)
python train.py
```

실행하면 강화학습 에이전트가 학습하고, 백테스트 성과가 출력되며,
`data/` 폴더에 학습 곡선·에쿼티 커브 차트와 학습 이력이, `models/`에 학습된 가중치가 저장됩니다.

VS Code에서는 `F5`를 눌러 실행 구성(`.vscode/launch.json`)에서 원하는 스크립트를 고를 수 있습니다.

## 프로젝트 구조

```
ai_trading_bot/
├── train.py              # DQN: 데이터→학습→백테스트→차트/모델 저장
├── train_regime.py       # HMM: 국면탐지→변동성타겟팅→백테스트→차트
├── requirements.txt
├── .env.example          # API 키 템플릿 (.env로 복사해 사용)
├── src/
│   ├── config.py         # 전역 설정 · 데이터 소스 · 하이퍼파라미터
│   ├── data.py           # 데이터 수집 (synthetic / alpaca / massive)
│   ├── features.py       # 기술적 지표 (이동평균, RSI, 변동성 등)
│   ├── rl_agent.py       # 트레이딩 환경 + NumPy DQN 에이전트
│   ├── regime.py         # NumPy 가우시안 HMM (Baum-Welch/Viterbi 직접 구현)
│   ├── backtest.py       # 백테스트 · 성과 지표(샤프, MDD 등)
│   └── live_trade.py     # Alpaca 페이퍼 트레이딩 주문 실행
├── data/                 # 생성물 (차트, 로그) — git 제외
└── models/               # 학습된 가중치 — git 제외
```

## 실제 시장 데이터로 전환하기

기본은 인터넷 없이 도는 합성 데이터입니다. 실제 데이터를 쓰려면:

### Alpaca (무료, 페이퍼 트레이딩 포함)
1. [alpaca.markets](https://alpaca.markets) 가입 → **Paper Trading** 모드로 전환 → API 키 발급
   (Key ID와 Secret Key **두 개 모두** 필요합니다. Secret은 발급 직후 한 번만 표시됩니다.)
2. `pip install alpaca-py`
3. `.env.example`을 `.env`로 복사하고 키 입력:
   ```
   DATA_SOURCE=alpaca
   ALPACA_API_KEY=PK...
   ALPACA_SECRET_KEY=...
   ```
   `src/config.py`가 실행 시 `.env`를 자동으로 읽습니다.
4. 실행:
   ```bash
   python train.py           # 실제 데이터로 학습
   python -m src.live_trade  # 모의계좌에 실제 주문
   ```

`.env` 대신 환경변수를 직접 써도 됩니다 (이미 설정된 환경변수가 `.env`보다 우선):
```powershell
# PowerShell
$env:DATA_SOURCE="alpaca"; $env:ALPACA_API_KEY="..."; $env:ALPACA_SECRET_KEY="..."
```
```bash
# bash / zsh
export DATA_SOURCE=alpaca ALPACA_API_KEY="..." ALPACA_SECRET_KEY="..."
```

### Massive (실제 거래소 데이터, 무료 티어)
1. [massive.com](https://massive.com) 가입 → API 키 발급
2. `pip install massive`, requirements.txt의 massive 주석 해제
3. `.env`에 `DATA_SOURCE=massive`, `MASSIVE_API_KEY=...` 입력 후 실행

## 강화학습에 대한 솔직한 메모

이 프로젝트의 핵심 교훈은 "강화학습으로 시장을 이기기는 매우 어렵다"입니다.
학습 곡선을 보면 에이전트 성과가 특정 시점 이후 **과적합으로 붕괴**하는 경우가 많습니다.
그래서 검증 성과 최고 지점의 정책을 저장하는 **조기 종료(early stopping)**를 넣었습니다.
매수 후 보유(Buy & Hold)를 안정적으로 이기지 못하는 결과가 나오는 것은 실패가 아니라,
금융 시계열의 낮은 신호 대 잡음비를 정직하게 보여주는 것입니다.

## HMM 국면 탐지 전략 (`train_regime.py`)

DQN과는 완전히 다른 접근입니다. "다음날 오를까"를 맞히지 않고, "지금 국면이
상승/하락/횡보 중 어디에 가까운가"를 가우시안 HMM(Baum-Welch/Viterbi를 `src/regime.py`에
NumPy로 직접 구현)으로 탐지해서, 국면별 기대변동성에 맞춰 **포지션 크기**를 조절합니다
(변동성 타겟팅). 방향이 아니라 크기를 조절하는 전략이라는 점이 핵심입니다.

```bash
python train_regime.py
```

실행하면 콘솔에 국면별 연환산 수익률·변동성·평균 지속기간이 출력되고,
`data/regime_overview.png`(가격+국면 음영, 국면확률, 포지션 비중)와
`data/regime_equity_curve.png`(전략 vs 매수후보유)가 저장됩니다.

**lookahead bias(미래정보 유출) 방지**: HMM은 학습 구간에서만 적합(fit)하고,
테스트 구간은 순방향 필터(forward filter)로만 국면 확률을 계산합니다 — 미래
관측치를 전혀 쓰지 않는 인과적(causal) 방식입니다. `train.py`의 DQN은 조기
종료 기준으로 쓰는 구간과 백테스트 구간이 같다는 한계가 있는데, `train_regime.py`는
이 문제를 피하도록 설계했습니다.

**솔직한 결과** — 기본 합성 데이터(`DATA_SOURCE=synthetic`)는 국면 간 평균수익률
차이(0.02~0.09%)가 일간 변동성(1.6%)보다 훨씬 작게 설계돼 있어서, 국면 판별이
무작위 추측과 별 차이가 없습니다 (`python -m src.regime`로 직접 확인 가능 — 평균이
충분히 벌어진 데이터로는 99%+ 복원되어 알고리즘 자체는 검증되어 있습니다).
반면 **실제 SPY 데이터**(`DATA_SOURCE=alpaca`)는 국면 간 변동성 차이가 뚜렷해서
(하락장이 상승장보다 변동성이 3배 이상 높은 "레버리지 효과" 등 실제 시장 특성) 훨씬
잘 작동하며, 백테스트에서 매수후보유 대비 비슷한 샤프 비율에 최대낙폭은 1/3 수준으로
나온 사례가 있습니다. 다만 이는 특정 기간 특정 종목의 결과이고, 미래 성과를
보장하지 않습니다.

## 자동화 (선택)

매일 장 마감 후 자동 실행하려면 스케줄러에 등록하세요.
- macOS/Linux (cron): `0 17 * * 1-5 cd /경로/ai_trading_bot && .venv/bin/python -m src.live_trade`
- Windows: 작업 스케줄러에서 `python -m src.live_trade` 등록
