"""
data.py — 시장 데이터 수집 모듈
================================
세 가지 데이터 소스를 지원한다. config.py의 DATA_SOURCE 값으로 전환한다.

  "synthetic" : 인터넷 없이 동작. 실제 주가의 통계적 특성(추세+변동성+국면전환)을
                재현한 합성 데이터. 개발·테스트·학습용 기본값.
  "alpaca"    : Alpaca의 실제 시장 데이터 (무료 가입, 페이퍼 트레이딩 계좌).
  "massive"   : Massive.com의 실제 거래소 데이터 (무료 티어 존재).

어떤 소스를 쓰든 반환 형식은 동일하다:
  pandas.DataFrame(index=날짜, columns=["open","high","low","close","volume"])
그래서 다른 모듈(features, env, backtest)은 소스를 몰라도 된다.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

from . import config


# ---------------------------------------------------------------------------
# 1. 합성 데이터 (기본값, 인터넷 불필요)
# ---------------------------------------------------------------------------
def _fetch_synthetic(symbol: str, n_days: int, seed: int | None = None):
    """가격 DataFrame과 함께, 각 날짜의 '실제' 숨은 국면(state) 배열도 반환한다.
    이 국면 배열은 데이터를 생성할 때만 알 수 있는 정답이므로 모델 학습에는
    당연히 쓰지 않고, regime.py로 탐지한 국면이 얼마나 정확한지 검증할 때만 쓴다."""
    seed = seed if seed is not None else (config.RANDOM_SEED + abs(hash(symbol)) % 1000)
    rng = np.random.default_rng(seed)

    # 국면(regime) 전환이 있는 시장: 상승장/하락장/횡보장이 번갈아 등장
    mu_states = [0.0009, -0.0007, 0.0002]
    sigma = 0.016
    rets, states, state = [], [], 0
    for _ in range(n_days):
        if rng.random() < 0.012:
            state = rng.integers(0, len(mu_states))
        rets.append(rng.normal(mu_states[state], sigma))
        states.append(state)
    rets = np.array(rets)
    states = np.array(states)

    close = 150 * np.exp(np.cumsum(rets))
    spread = rng.uniform(0.002, 0.012, n_days)
    open_ = close * (1 + rng.normal(0, 0.003, n_days))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    volume = rng.lognormal(15, 0.3, n_days) * (1 + 5 * np.abs(rets))

    dates = pd.bdate_range(end=pd.Timestamp.today(), periods=n_days)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )
    return df, states


# ---------------------------------------------------------------------------
# 2. Alpaca 실제 데이터
# ---------------------------------------------------------------------------
def _fetch_alpaca(symbol: str, n_days: int) -> pd.DataFrame:
    from datetime import datetime, timedelta
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed

    client = StockHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
    end = datetime.now()
    start = end - timedelta(days=int(n_days * 1.5))  # 휴장일 감안
    req = StockBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        # 피드를 지정하지 않으면 서버가 sip으로 처리해 무료 플랜에서 거부당한다.
        feed=DataFeed(config.ALPACA_FEED),
    )
    df = client.get_stock_bars(req).df.reset_index()
    df = df[df["symbol"] == symbol].set_index("timestamp")
    df = df[["open", "high", "low", "close", "volume"]]
    return df.tail(n_days)   # 합성 데이터와 같이 거래일 n_days개로 맞춘다


# ---------------------------------------------------------------------------
# 3. Massive 실제 데이터
# ---------------------------------------------------------------------------
def _fetch_massive(symbol: str, n_days: int) -> pd.DataFrame:
    # pip install massive  (massive.com 문서 참고)
    from datetime import date, timedelta
    from massive import RESTClient

    client = RESTClient(config.MASSIVE_API_KEY)
    end = date.today()
    start = end - timedelta(days=int(n_days * 1.5))

    aggs = []
    for a in client.list_aggs(symbol, 1, "day", start.isoformat(), end.isoformat(), limit=50000):
        aggs.append(a)

    df = pd.DataFrame([{
        "timestamp": pd.to_datetime(a.timestamp, unit="ms"),
        "open": a.open, "high": a.high, "low": a.low,
        "close": a.close, "volume": a.volume,
    } for a in aggs]).set_index("timestamp")
    return df[["open", "high", "low", "close", "volume"]]


# ---------------------------------------------------------------------------
# 공개 함수: 소스에 상관없이 이것만 호출하면 된다
# ---------------------------------------------------------------------------
def fetch(symbol: str | None = None, n_days: int | None = None, seed: int | None = None) -> pd.DataFrame:
    """가격 DataFrame만 필요할 때 쓰는 기존 공개 함수 (하위 호환 유지)."""
    df, _ = fetch_with_regime(symbol, n_days, seed)
    return df


def fetch_with_regime(symbol: str | None = None, n_days: int | None = None, seed: int | None = None):
    """(가격 DataFrame, 실제 국면 배열) 튜플을 반환한다.
    synthetic 소스가 아니면 국면 배열은 None (실제 시장에는 '정답'이 없으므로)."""
    symbol = symbol or config.SYMBOL
    n_days = n_days or config.N_DAYS
    src = config.DATA_SOURCE.lower()

    if src == "synthetic":
        return _fetch_synthetic(symbol, n_days, seed)
    if src == "alpaca":
        return _fetch_alpaca(symbol, n_days), None
    if src == "massive":
        return _fetch_massive(symbol, n_days), None
    raise ValueError(f"알 수 없는 DATA_SOURCE: {config.DATA_SOURCE!r} "
                     f"(synthetic / alpaca / massive 중 하나여야 함)")


if __name__ == "__main__":
    df = fetch()
    print(f"[{config.DATA_SOURCE}] {config.SYMBOL} 데이터 {len(df)}행")
    print(df.tail())
