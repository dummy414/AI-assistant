"""
train.py — 메인 실행 스크립트
==============================
전체 파이프라인을 한 번에 실행한다:
  1. 데이터 수집 (config.DATA_SOURCE 설정에 따름)
  2. 특징 생성
  3. DQN 에이전트 학습 (조기 종료 포함)
  4. 학습된 정책으로 백테스트
  5. 성과 출력 + 차트 저장 + 모델 가중치 저장

실행:  python train.py
"""

import os
import json
import numpy as np

from src import config, data, features
from src import rl_agent, backtest


def main():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.MODEL_DIR, exist_ok=True)

    print("=" * 60)
    print(f"1) 데이터 수집  [소스: {config.DATA_SOURCE}] [종목: {config.SYMBOL}]")
    print("=" * 60)
    raw = data.fetch()
    feat = features.build(raw)
    print(f"   원본 {len(raw)}행 -> 피처 {len(feat)}행\n")

    fm = feat[features.FEATURE_COLS].values
    returns = feat["return_1d"].values

    print("=" * 60)
    print("2) 강화학습(DQN) 에이전트 학습")
    print("=" * 60)
    agent, history, best_ep = rl_agent.train(fm, returns)

    # ---- 학습된 정책으로 테스트 구간 백테스트 ----
    split = int(len(returns) * 0.7)
    test_feat = fm[split:]
    test_returns = returns[split:]

    signals = []
    pos = 0
    for t in range(len(test_returns) - 1):
        state = np.nan_to_num(np.append(test_feat[t], pos))
        pos = agent.act(state, greedy=True)
        signals.append(pos)
    signals.append(pos)
    signals = np.array(signals)

    print("\n" + "=" * 60)
    print("3) 백테스트 (테스트 구간, 거래비용 반영)")
    print("=" * 60)
    bt = backtest.run(test_returns[:len(signals)], signals)
    backtest.summarize(bt)

    # ---- 저장 ----
    np.savez(os.path.join(config.MODEL_DIR, "dqn_weights.npz"),
             W1=agent.q.W1, b1=agent.q.b1, W2=agent.q.W2, b2=agent.q.b2)
    with open(os.path.join(config.DATA_DIR, "rl_history.json"), "w") as f:
        json.dump({"history": history, "best_ep": best_ep}, f)
    print(f"\n모델 저장: {config.MODEL_DIR}/dqn_weights.npz")

    # ---- 차트 ----
    try:
        save_charts(history, bt, best_ep)
    except Exception as e:
        print(f"(차트 생성 건너뜀: {e})")


def save_charts(history, bt, best_ep):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm

    for p in ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/System/Library/Fonts/AppleSDGothicNeo.ttc",
              "C:/Windows/Fonts/malgun.ttf"]:
        if os.path.exists(p):
            fm.fontManager.addfont(p)
            plt.rcParams["font.family"] = fm.FontProperties(fname=p).get_name()
            break
    plt.rcParams["axes.unicode_minus"] = False

    # 학습 곡선
    eps = [h["episode"] for h in history]
    fig, ax = plt.subplots(figsize=(7, 4), dpi=130)
    ax.plot(eps, [h["test_reward"] for h in history], color="#C9A227", label="에이전트 테스트 성과")
    ax.plot(eps, [h["buy_hold"] for h in history], color="#5A6270", ls="--", label="매수 후 보유")
    ax.axvline(best_ep, color="#4A9E7F", ls=":", label=f"조기 종료 ({best_ep})")
    ax.set_xlabel("에피소드"); ax.set_ylabel("누적 보상(수익률 근사)")
    ax.set_title("강화학습 에이전트 학습 곡선"); ax.legend(); ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(); fig.savefig(os.path.join(config.DATA_DIR, "learning_curve.png"), facecolor="white")
    plt.close(fig)

    # 에쿼티 커브
    fig, ax = plt.subplots(figsize=(7, 4), dpi=130)
    ax.plot(bt["strategy_equity"].values, color="#C0392B", label="전략(에이전트)")
    ax.plot(bt["bh_equity"].values, color="#8C9DB5", ls="--", label="매수 후 보유")
    ax.set_xlabel("거래일"); ax.set_ylabel("자산 가치")
    ax.set_title("백테스트 성과"); ax.legend(); ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(); fig.savefig(os.path.join(config.DATA_DIR, "equity_curve.png"), facecolor="white")
    plt.close(fig)
    print(f"차트 저장: {config.DATA_DIR}/learning_curve.png, equity_curve.png")


if __name__ == "__main__":
    main()
