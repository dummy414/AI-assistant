"""
live_trade.py — Alpaca 페이퍼 트레이딩 실행
============================================
학습된 DQN 에이전트의 신호에 따라 Alpaca 모의투자 계좌에 실제 주문을 넣는다.
매일 장 마감 후 한 번씩 실행하도록 스케줄러(cron / 작업 스케줄러)에 등록하면
자동 매매가 된다.

사전 준비:
  1. https://alpaca.markets 무료 가입 -> Paper Trading API 키 발급
  2. pip install alpaca-py
  3. 환경변수 설정:
       export ALPACA_API_KEY="..."
       export ALPACA_SECRET_KEY="..."
       export DATA_SOURCE="alpaca"
  4. python -m src.live_trade

주의: 이 모듈은 실제 주문을 넣으므로 반드시 Paper Trading(모의) 모드인지 확인할 것.
"""

from __future__ import annotations
import os
import numpy as np
import pandas as pd

from . import config, data, features
from .rl_agent import DQNAgent, QNetwork

RISK_FRACTION = 0.5  # 매수 신호 시 보유 현금의 몇 %를 투입할지
MODEL_FILE = os.path.join(config.MODEL_DIR, "dqn_weights.npz")
LOG_FILE = os.path.join(config.DATA_DIR, "live_trade_log.csv")


def load_agent(n_state: int) -> DQNAgent:
    agent = DQNAgent(n_state=n_state)
    if os.path.exists(MODEL_FILE):
        w = np.load(MODEL_FILE)
        agent.q.set_weights((w["W1"], w["b1"], w["W2"], w["b2"]))
        agent.eps = 0.0  # 실거래에서는 탐험하지 않음
        print(f"학습된 모델 불러옴: {MODEL_FILE}")
    else:
        raise FileNotFoundError(
            f"{MODEL_FILE} 이 없습니다. 먼저 train.py로 에이전트를 학습·저장하세요.")
    return agent


def get_position_qty(trading_client, symbol) -> float:
    from alpaca.common.exceptions import APIError
    try:
        return float(trading_client.get_open_position(symbol).qty)
    except APIError:
        return 0.0  # 포지션 없으면 404


def rebalance(trading_client, symbol, target_qty, current_qty):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    diff = target_qty - current_qty
    if diff == 0:
        print("포지션 변경 없음")
        return None
    side = OrderSide.BUY if diff > 0 else OrderSide.SELL
    order = trading_client.submit_order(order_data=MarketOrderRequest(
        symbol=symbol, qty=abs(diff), side=side, time_in_force=TimeInForce.DAY))
    print(f"주문: {side.value} {abs(diff)}주 {symbol}")
    return order


def log(date, signal, price, qty_before, qty_after):
    row = pd.DataFrame([{
        "date": date, "signal": signal, "price": round(float(price), 2),
        "qty_before": qty_before, "qty_after": qty_after,
    }])
    row.to_csv(LOG_FILE, mode="a", header=not os.path.exists(LOG_FILE), index=False)


def run_once():
    from alpaca.trading.client import TradingClient

    # 1) 데이터 -> 특징
    raw = data.fetch()
    feat = features.build(raw, add_target=False)
    fm = feat[features.FEATURE_COLS].values

    # 2) 에이전트 로드 후 오늘 신호 계산 (상태 = 특징 + 현재포지션)
    agent = load_agent(n_state=fm.shape[1] + 1)
    trading_client = TradingClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=True)
    current_qty = get_position_qty(trading_client, config.SYMBOL)
    state = np.append(fm[-1], 1 if current_qty > 0 else 0)
    signal = agent.act(np.nan_to_num(state), greedy=True)

    # 3) 목표 수량 결정 후 주문
    account = trading_client.get_account()
    last_price = float(raw["close"].iloc[-1])
    if signal == 1:
        target_qty = int(float(account.cash) * RISK_FRACTION // last_price)
    else:
        target_qty = 0

    print(f"[{feat.index[-1].date()}] {config.SYMBOL} 신호={signal} "
          f"| 현재 {current_qty:.0f}주 -> 목표 {target_qty}주 | 현금 ${float(account.cash):,.0f}")
    rebalance(trading_client, config.SYMBOL, target_qty, current_qty)
    log(feat.index[-1], signal, last_price, current_qty, target_qty)


if __name__ == "__main__":
    run_once()
