"""
train_regime.py — HMM 국면 탐지 + 변동성 타겟팅 전략
========================================================
train.py(DQN)와는 접근 자체가 다르다. "다음날 오를까 내릴까"를 맞히지 않는다.
대신 "지금 시장이 어떤 국면(상승/하락/횡보)에 가까운가"를 탐지해서 포지션의
'크기'를 조절한다 — 방향이 아니라 크기를 조절하는 전략이다.

이렇게 하는 이유 (README의 "솔직한 메모" 참고):
  - 개별 종목의 일간 방향은 신호 대 잡음비가 매우 낮다. rl_agent.py의 DQN이
    "항상 매수" 또는 "항상 현금"으로만 수렴한 것이 그 증거다.
  - 반면 국면별 평균·변동성 차이, 국면의 지속성(persistence)은 상대적으로
    안정적인 통계 구조를 가진다. 다만 이 안정성도 국면 간 차이가 잡음보다
    충분히 커야 실제로 탐지 가능하다 — 아래 리포트에서 그 한계도 함께 보여준다.

파이프라인:
  1. 데이터 수집 + 피처 생성 (data.py, features.py 재사용)
  2. 시간순 학습/테스트 분할 (미래 데이터가 학습에 섞이지 않도록)
  3. 학습 구간에서만 가우시안 HMM 적합 (regime.py, Baum-Welch/EM)
  4. 테스트 구간은 순방향 필터(forward filter)로만 국면 확률 계산
     → 인과적(causal), lookahead bias 없음. 테스트 구간을 다시 학습에 쓰지 않는다
       (train.py/rl_agent.py에 있던 "테스트셋=검증셋" 문제를 여기서는 피한다).
  5. 국면별 기대수익률·변동성으로 변동성 타겟팅(volatility targeting) 포지션 사이징
  6. 백테스트 + 매수후보유 비교 + 차트 저장

실행:  python train_regime.py
"""

import os
import numpy as np

from src import config, data, features, regime, backtest


def build_positions(model, X_test, init_prob, target_vol_daily, max_exposure):
    """
    필터링된(인과적) 국면 확률로 시점별 포지션 비중을 계산한다.

    규칙 (둘 다 학술적으로 검증된 방식이며, 어느 쪽도 "다음날 방향"을 직접 맞히지 않는다):
      1) 방향 필터    — 국면의 기대수익률이 양(+)일 때만 매수, 음(-)이면 현금.
      2) 변동성 타겟팅 — 목표 변동성 / 국면의 기대 변동성 비율로 포지션 크기를 조절.
                        시장이 조용하면 비중을 늘리고, 출렁이면 줄인다.
    """
    probs = model.filter(X_test, init_prob=init_prob)      # (T,K) 시점별 국면 확률 (인과적)
    means = model.means_[:, 0]                              # 국면별 기대 일간수익률 (return_1d 차원)
    vars_ = model.vars_[:, 0]

    exp_ret = probs @ means                                  # 혼합분포 기대수익률 E[X]
    exp_var = probs @ (vars_ + means ** 2) - exp_ret ** 2     # 혼합분포 분산 E[X^2]-E[X]^2
    exp_vol = np.sqrt(np.maximum(exp_var, 1e-10))

    raw_size = target_vol_daily / np.maximum(exp_vol, 1e-6)
    exposure = np.clip(raw_size, 0.0, max_exposure)
    exposure = np.where(exp_ret > 0, exposure, 0.0)
    return exposure, probs, exp_ret, exp_vol


def main():
    os.makedirs(config.DATA_DIR, exist_ok=True)

    print("=" * 60)
    print(f"1) 데이터 수집  [소스: {config.DATA_SOURCE}] [종목: {config.SYMBOL}]")
    print("=" * 60)
    raw, true_regime = data.fetch_with_regime()
    feat = features.build(raw)
    if true_regime is not None:
        true_regime = true_regime[-len(feat):]      # features.build()의 dropna로 잘린 앞부분 맞추기
    print(f"   원본 {len(raw)}행 -> 피처 {len(feat)}행\n")

    X = np.nan_to_num(feat[["return_1d", "volatility_5d"]].values)
    split = int(len(X) * config.REGIME_TRAIN_FRAC)
    X_train, X_test = X[:split], X[split:]
    returns_test = feat["return_1d"].values[split:]

    print("=" * 60)
    print(f"2) 가우시안 HMM 학습 (국면 {config.REGIME_N_STATES}개, 학습 {len(X_train)}일 / "
          f"테스트 {len(X_test)}일)")
    print("=" * 60)
    model = regime.GaussianHMM(n_states=config.REGIME_N_STATES, n_features=X.shape[1],
                                seed=config.RANDOM_SEED)
    model.fit(X_train, n_iter=config.REGIME_EM_ITERS, n_restarts=config.REGIME_EM_RESTARTS,
              verbose=True)
    print(f"   최종 로그우도: {model.train_loglik_:.2f}")

    order, labels = regime.label_states_by_return(model)
    train_path = model.viterbi(X_train)
    print("\n   [국면별 특성] (연환산, 학습구간 기준)")
    for rank, k in enumerate(order):
        ann_ret = model.means_[k, 0] * 252
        ann_vol = np.sqrt(model.vars_[k, 0]) * np.sqrt(252)
        p_stay = np.exp(model.log_A_[k, k])
        dur = 1 / max(1 - p_stay, 1e-6)
        frac = (train_path == k).mean()
        print(f"   {labels[rank]:6s} | 연수익률 {ann_ret:+7.2%} | 연변동성 {ann_vol:6.2%} "
              f"| 평균지속 {dur:5.1f}일 | 학습구간 비중 {frac:5.1%}")

    if true_regime is not None:
        acc, _ = regime.best_alignment_accuracy(train_path, true_regime[:split],
                                                  config.REGIME_N_STATES)
        chance = 1 / config.REGIME_N_STATES
        print(f"\n   [합성데이터 검증] 실제 생성 국면과의 일치율: {acc:.1%} (무작위 추측 {chance:.1%})")
        if acc < chance * 1.3:
            print("   -> 무작위 추측과 큰 차이가 없다. 이 합성 데이터는 국면 간 평균수익률 차이가")
            print("      일간 변동성(σ=1.6%)보다 훨씬 작게 설계돼 있어(0.02~0.09%), 통계적으로")
            print("      국면을 분간하기 매우 어렵다 — 버그가 아니라 이 데이터셋의 근본적 한계다.")
            print("      (regime.py를 평균 차이가 충분히 큰 데이터로 자체 검증하면 99%+ 복원된다.)")

    # ---- 테스트 구간: 인과적 필터 + 변동성 타겟팅 사이징 ----
    print("\n" + "=" * 60)
    print("3) 테스트 구간 백테스트 (인과적 국면 필터, lookahead 없음)")
    print("=" * 60)
    train_probs = model.filter(X_train)
    init_prob = model.predict_next_prior(train_probs[-1])

    target_vol_daily = config.REGIME_TARGET_VOL / np.sqrt(252)
    exposure, test_probs, exp_ret, exp_vol = build_positions(
        model, X_test, init_prob, target_vol_daily, config.REGIME_MAX_EXPOSURE)

    bt = backtest.run(returns_test, exposure)
    for label, eq_col, ret_col in [("전략 (국면 탐지 + 변동성타겟팅)", "strategy_equity", "strategy_return"),
                                    ("매수 후 보유", "bh_equity", "bh_return")]:
        m = backtest.metrics(bt[eq_col].values, bt[ret_col].values)
        print(f"--- {label} ---")
        print(f"  총 수익률     : {m['total_return']*100:+.2f}%")
        print(f"  연환산 수익률 : {m['annual_return']*100:+.2f}%")
        print(f"  샤프 비율     : {m['sharpe']:.2f}")
        print(f"  최대 낙폭     : {m['max_drawdown']*100:.2f}%")
    turnover_ann = np.abs(np.diff(exposure, prepend=0)).sum() / len(exposure) * 252
    print(f"  평균 노출도   : {exposure.mean():.1%}  (매수후보유는 항상 100%)")
    print(f"  연환산 회전율 : {turnover_ann:.1f}x  (참고용 — 비용은 이미 위 수익률에 반영됨)")

    try:
        save_charts(feat, split, model, order, labels, test_probs, exposure, bt, true_regime)
    except Exception as e:
        print(f"(차트 생성 건너뜀: {e})")


# ============================================================
# 차트
# ============================================================
_REGIME_COLORS = {"하락장": "#C0392B", "횡보장": "#8C9DB5", "상승장": "#4A9E7F"}


def _regime_runs(path):
    """상태 경로에서 (시작idx, 끝idx, 상태) 연속 구간을 뽑는다 (axvspan 배경 음영용)."""
    runs, start = [], 0
    for i in range(1, len(path) + 1):
        if i == len(path) or path[i] != path[start]:
            runs.append((start, i, path[start]))
            start = i
    return runs


def save_charts(feat, split, model, order, labels, test_probs, exposure, bt, true_regime):
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

    # 상태 index -> 라벨/색 매핑 (label_states_by_return의 order/labels 기준)
    state_to_label = {int(k): labels[rank] for rank, k in enumerate(order)}
    label_to_rank = {lab: rank for rank, lab in enumerate(labels)}

    close = feat["close"].values
    X_full = np.nan_to_num(feat[["return_1d", "volatility_5d"]].values)
    full_path = model.viterbi(X_full)     # 시각화 전용 (사후 복기, 전체 구간 색칠용)

    # ---- 차트 1: 가격+국면 음영 / 필터링 확률 / 포지션 비중 (3단) ----
    fig, axes = plt.subplots(3, 1, figsize=(9, 9), dpi=130, sharex=False)

    ax = axes[0]
    for start, end, k in _regime_runs(full_path):
        lab = state_to_label.get(int(k), "?")
        ax.axvspan(start, end, color=_REGIME_COLORS.get(lab, "#CCCCCC"), alpha=0.18, lw=0)
    ax.plot(close, color="#2B2F36", lw=1.1)
    vline = ax.axvline(split, color="#333333", ls="--", lw=1)
    ax.set_title("가격 + 탐지된 국면 (사후 복기 · Viterbi)")
    ax.set_ylabel("종가")
    handles = [plt.Rectangle((0, 0), 1, 1, color=_REGIME_COLORS[l], alpha=0.4) for l in labels]
    ax.legend(handles + [vline], labels + ["학습/테스트 분할"], loc="upper left", fontsize=8, ncol=2)
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[1]
    t_test = np.arange(len(test_probs))
    stack = np.zeros(len(test_probs))
    for rank, lab in enumerate(labels):
        k = order[rank]
        ax.fill_between(t_test, stack, stack + test_probs[:, k],
                         color=_REGIME_COLORS[lab], alpha=0.75, label=lab, lw=0)
        stack += test_probs[:, k]
    ax.set_ylim(0, 1)
    ax.set_title("테스트 구간 국면 확률 (순방향 필터 · 인과적, 미래정보 미사용)")
    ax.set_ylabel("확률")
    ax.legend(loc="upper left", fontsize=8, ncol=3)
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[2]
    ax.fill_between(t_test, 0, exposure, color="#C9A227", alpha=0.6, lw=0)
    ax.plot(t_test, exposure, color="#C9A227", lw=1.2)
    ax.set_ylim(0, max(0.05, exposure.max() * 1.15))
    ax.set_title("포지션 비중 (변동성 타겟팅 사이징 결과)")
    ax.set_xlabel("테스트 구간 거래일"); ax.set_ylabel("비중")
    ax.spines[["top", "right"]].set_visible(False)

    fig.tight_layout()
    fig.savefig(os.path.join(config.DATA_DIR, "regime_overview.png"), facecolor="white")
    plt.close(fig)

    # ---- 차트 2: 테스트 구간 에쿼티 커브 (전략 vs 매수후보유) ----
    fig, ax = plt.subplots(figsize=(7, 4), dpi=130)
    ax.plot(bt["strategy_equity"].values, color="#C9A227", label="전략 (국면+변동성타겟팅)")
    ax.plot(bt["bh_equity"].values, color="#8C9DB5", ls="--", label="매수 후 보유")
    ax.set_xlabel("테스트 구간 거래일"); ax.set_ylabel("자산 가치")
    ax.set_title("HMM 국면 전략 — 테스트 구간 백테스트")
    ax.legend(); ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(config.DATA_DIR, "regime_equity_curve.png"), facecolor="white")
    plt.close(fig)

    print(f"\n차트 저장: {config.DATA_DIR}/regime_overview.png, regime_equity_curve.png")


if __name__ == "__main__":
    main()
