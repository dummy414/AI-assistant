"""
backtest.py — 백테스트 및 성과 지표
====================================
학습된 에이전트(또는 임의의 신호 함수)를 과거 데이터에 적용해
거래비용을 반영한 성과를 계산한다. 매수 후 보유(Buy & Hold)와 비교한다.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

from . import config


def run(returns: np.ndarray, signals: np.ndarray,
        cost: float = config.TRANSACTION_COST, initial: float = 10_000_000) -> pd.DataFrame:
    """
    returns : 각 시점의 '다음날' 실제 수익률 배열
    signals : 각 시점의 포지션(0=현금, 1=매수) 배열 (returns와 같은 길이)
    """
    n = len(returns)
    pos = np.asarray(signals, dtype=float)
    pos_change = np.abs(np.diff(pos, prepend=0))
    costs = pos_change * cost
    strat_ret = pos * returns - costs
    strat_equity = initial * np.cumprod(1 + strat_ret)

    bh_ret = returns.copy()
    bh_ret[0] -= cost
    bh_equity = initial * np.cumprod(1 + bh_ret)

    return pd.DataFrame({
        "strategy_return": strat_ret,
        "strategy_equity": strat_equity,
        "bh_return": bh_ret,
        "bh_equity": bh_equity,
        "position": pos,
    })


def metrics(equity: np.ndarray, returns: np.ndarray, initial: float = 10_000_000) -> dict:
    n = len(equity)
    total = equity[-1] / initial - 1
    ann = (1 + total) ** (252 / n) - 1 if n > 0 else 0
    sharpe = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else float("nan")
    running_max = np.maximum.accumulate(equity)
    mdd = (equity / running_max - 1).min()
    return {"total_return": total, "annual_return": ann, "sharpe": sharpe, "max_drawdown": mdd}


def summarize(df: pd.DataFrame, initial: float = 10_000_000) -> None:
    for label, eq, ret in [
        ("전략 (모델 매매)", df["strategy_equity"].values, df["strategy_return"].values),
        ("매수 후 보유", df["bh_equity"].values, df["bh_return"].values),
    ]:
        m = metrics(eq, ret, initial)
        print(f"--- {label} ---")
        print(f"  총 수익률     : {m['total_return']*100:+.2f}%")
        print(f"  연환산 수익률 : {m['annual_return']*100:+.2f}%")
        print(f"  샤프 비율     : {m['sharpe']:.2f}")
        print(f"  최대 낙폭     : {m['max_drawdown']*100:.2f}%")
