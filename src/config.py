"""
config.py — 프로젝트 전역 설정
================================
API 키는 코드에 직접 쓰지 말고 프로젝트 루트의 .env 파일에 넣는다.
(.env.example을 .env로 복사한 뒤 값을 채우면 아래 load_dotenv()가 자동으로 읽는다.)
.env는 .gitignore에 있으므로 실수로 GitHub에 키가 올라가는 사고를 막을 수 있다.

환경변수를 직접 설정해도 된다. 이미 설정된 환경변수가 .env보다 우선한다.
  PowerShell:  $env:ALPACA_API_KEY="..."
  bash/zsh:    export ALPACA_API_KEY="..."
"""

import os
from pathlib import Path

# ---- .env 로드 (없거나 python-dotenv 미설치여도 조용히 넘어간다) ----
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# ---- 데이터 소스 선택 ----
# "synthetic" : 인터넷 불필요, 기본값 (개발·학습용)
# "alpaca"    : Alpaca 실제 데이터 (무료 가입 필요)
# "massive"   : Massive 실제 거래소 데이터 (무료 티어 존재)
DATA_SOURCE = os.environ.get("DATA_SOURCE", "synthetic")

# ---- 거래 대상 ----
SYMBOL = os.environ.get("SYMBOL", "SPY")   # SPY = S&P500 추종 ETF
N_DAYS = 1200                               # 가져올 거래일 수

# ---- 재현성 ----
RANDOM_SEED = 42

# ---- 거래 비용 ----
TRANSACTION_COST = 0.001   # 편도 0.1% (수수료 + 슬리피지 가정)

# ---- API 키 (환경변수에서 읽음; 없으면 플레이스홀더) ----
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "YOUR_ALPACA_PAPER_KEY")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "YOUR_ALPACA_PAPER_SECRET")

# 데이터 피드: "iex" = 무료 플랜 기본값, "sip" = 전체 거래소 통합(유료 구독 필요).
# 무료 계정에서 sip을 쓰면 "subscription does not permit..." 오류가 난다.
ALPACA_FEED = os.environ.get("ALPACA_FEED", "iex")
MASSIVE_API_KEY = os.environ.get("MASSIVE_API_KEY", "YOUR_MASSIVE_KEY")
FMP_API_KEY = os.environ.get("FMP_API_KEY", "YOUR_FMP_KEY")  # financialmodelingprep.com — 우량주 탭 재무데이터

# ---- 강화학습 하이퍼파라미터 ----
RL_EPISODES = 120
RL_HIDDEN = 32
RL_LEARNING_RATE = 0.01
RL_GAMMA = 0.95
RL_EPS_DECAY = 0.92
RL_EPS_MIN = 0.05

# ---- 국면(regime) 탐지 하이퍼파라미터 (train_regime.py) ----
REGIME_N_STATES = 3          # 국면 개수 (기본 3 = 하락장/횡보장/상승장)
REGIME_TRAIN_FRAC = 0.6      # 학습 구간 비율 (나머지는 인과적 필터로만 평가)
REGIME_EM_ITERS = 200        # Baum-Welch(EM) 최대 반복 횟수
REGIME_EM_RESTARTS = 8       # 지역해(local optimum) 회피용 무작위 재시작 횟수
REGIME_TARGET_VOL = 0.10     # 목표 연환산 변동성 (변동성 타겟팅 사이징 기준, 10%)
REGIME_MAX_EXPOSURE = 1.0    # 최대 포지션 비중 (1.0 = 레버리지 없이 100%까지)

# ---- 경로 ----
DATA_DIR = "data"
MODEL_DIR = "models"
