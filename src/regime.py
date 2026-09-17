"""
regime.py — 은닉 마르코프 모델(HMM) 기반 시장 국면 탐지
===========================================================
외부 라이브러리(hmmlearn 등) 없이 NumPy만으로 가우시안 HMM을 직접 구현한다.
Baum-Welch(EM) 알고리즘으로 학습하고, Viterbi로 국면 경로를 복원하며,
순방향(forward) 필터만으로 "지금까지의 정보만 사용한" 실시간 국면 확률을 계산한다.

왜 방향 예측 대신 국면 탐지인가?
  - 개별 종목의 "다음날 오를까 내릴까"는 신호 대 잡음비가 매우 낮다
    (rl_agent.py의 DQN이 "항상 매수"나 "항상 현금"으로만 수렴한 이유이기도 하다).
  - 반면 "지금 시장이 상승/하락/횡보 중 어디에 가까운가"는 국면별 평균·변동성
    차이와 국면 지속성(persistence)이라는, 상대적으로 안정적인 통계 구조를 가진다.
  - 이 모듈은 각 날짜가 "오를지"를 맞히지 않는다. 대신 국면별 평균수익률·분산을
    추정해서, 변동성 타겟팅(volatility targeting) 방식으로 포지션 크기를 정하는 데
    쓴다 (train_regime.py 참고). 방향이 아니라 크기를 조절하는 전략이다.

lookahead bias(미래정보 유출) 방지 — 반드시 지킬 것:
  - fit()은 학습 구간에서만 수행한다. 전체 구간을 다시 적합하면 테스트 구간의
    정보가 파라미터에 섞여 들어간다.
  - filter()는 순방향(forward)만 사용해 시점 t까지의 관측치만으로 국면 확률을
    계산한다 (인과적/causal). Baum-Welch 내부의 forward-backward는 미래 관측치도
    쓰므로 "국면이 이랬을 것이다"를 복기하는 시각화(viterbi)에만 쓰고,
    실거래 신호 생성에는 반드시 filter()를 쓴다.
"""

from __future__ import annotations
from itertools import permutations

import numpy as np


def _logsumexp(a: np.ndarray, axis=None) -> np.ndarray:
    """log(sum(exp(a)))를 오버플로/언더플로 없이 계산한다."""
    a_max = np.max(a, axis=axis, keepdims=True)
    a_max = np.where(np.isfinite(a_max), a_max, 0.0)
    s = np.sum(np.exp(a - a_max), axis=axis, keepdims=True)
    out = np.log(s) + a_max
    if axis is None:
        return out.reshape(())          # 진짜 0-d 스칼라로 반환 (float() 캐스팅 시 경고 방지)
    return np.squeeze(out, axis=axis)


# ============================================================
# 가우시안 HMM (대각 공분산)
# ============================================================
class GaussianHMM:
    """
    관측치가 상태별 다변량 정규분포(대각 공분산)에서 나온다고 가정하는 HMM.

    상태(state) = 시장 국면. 전이행렬 A[i,j] = P(내일 국면=j | 오늘 국면=i).
    """

    def __init__(self, n_states: int, n_features: int, seed: int = 0, var_floor: float = 1e-6):
        self.n_states = n_states
        self.n_features = n_features
        self.seed = seed
        self.var_floor = var_floor
        # 학습 후 채워지는 파라미터
        self.means_ = None
        self.vars_ = None
        self.log_A_ = None
        self.log_pi_ = None
        self.train_loglik_ = None

    # ---- 내부: 로그 방출확률(emission) ----
    def _log_emission_matrix(self, X: np.ndarray, means: np.ndarray, variances: np.ndarray) -> np.ndarray:
        T = X.shape[0]
        K = means.shape[0]
        log_b = np.zeros((T, K))
        for k in range(K):
            var = np.maximum(variances[k], self.var_floor)
            diff2 = (X - means[k]) ** 2
            log_b[:, k] = -0.5 * np.sum(np.log(2 * np.pi * var) + diff2 / var, axis=1)
        return log_b

    # ---- 내부: 순방향/역방향 (로그공간, 미정규화 — EM 학습용) ----
    @staticmethod
    def _forward_raw(log_pi, log_A, log_B):
        T, K = log_B.shape
        log_alpha = np.zeros((T, K))
        log_alpha[0] = log_pi + log_B[0]
        for t in range(1, T):
            log_alpha[t] = _logsumexp(log_alpha[t - 1][:, None] + log_A, axis=0) + log_B[t]
        return log_alpha

    @staticmethod
    def _backward_raw(log_A, log_B):
        T, K = log_B.shape
        log_beta = np.zeros((T, K))
        for t in range(T - 2, -1, -1):
            m = log_A + (log_B[t + 1] + log_beta[t + 1])[None, :]
            log_beta[t] = _logsumexp(m, axis=1)
        return log_beta

    # ---- 내부: 무작위 초기화 (지속성을 선호하는 전이행렬 사전) ----
    def _init_params(self, X: np.ndarray, rng: np.random.Generator) -> dict:
        T, D = X.shape
        K = self.n_states
        assign = rng.integers(0, K, size=T)
        global_var = X.var(axis=0)
        means = np.zeros((K, D))
        variances = np.zeros((K, D))
        for k in range(K):
            mask = assign == k
            if mask.sum() < 5:                      # 표본이 너무 적은 국면은 무작위 재배정
                idx = rng.choice(T, size=5, replace=False)
                mask = np.zeros(T, dtype=bool)
                mask[idx] = True
            means[k] = X[mask].mean(axis=0)
            variances[k] = np.maximum(X[mask].var(axis=0), global_var * 0.1)

        # 시장 국면은 자주 안 바뀐다는 사전지식을 대각 우세 전이행렬로 반영
        A = np.full((K, K), 0.05 / max(K - 1, 1))
        np.fill_diagonal(A, 0.95)
        A = np.clip(A + rng.normal(0, 0.01, size=(K, K)), 1e-6, None)
        A /= A.sum(axis=1, keepdims=True)
        pi = np.full(K, 1.0 / K)
        return {"means": means, "vars": variances,
                "log_A": np.log(A), "log_pi": np.log(pi)}

    # ---- 내부: E-step ----
    def _e_step(self, log_alpha, log_beta, log_A, log_B):
        T, K = log_B.shape
        log_gamma = log_alpha + log_beta
        log_gamma -= _logsumexp(log_gamma, axis=1)[:, None]
        gamma = np.exp(log_gamma)

        xi_sum = np.zeros((K, K))
        for t in range(T - 1):
            log_xi_t = (log_alpha[t][:, None] + log_A
                        + (log_B[t + 1] + log_beta[t + 1])[None, :])
            log_xi_t -= _logsumexp(log_xi_t.ravel())
            xi_sum += np.exp(log_xi_t)
        return gamma, xi_sum

    # ---- 내부: M-step ----
    def _m_step(self, X, gamma, xi_sum):
        pi_new = gamma[0] / gamma[0].sum()
        denom = gamma[:-1].sum(axis=0)                      # sum_{t=0..T-2} gamma[t,i]
        A_new = xi_sum / denom[:, None]
        A_new /= A_new.sum(axis=1, keepdims=True)           # 수치오차 재정규화

        weights = gamma.sum(axis=0)
        means_new = (gamma.T @ X) / weights[:, None]
        variances_new = np.zeros_like(means_new)
        for k in range(self.n_states):
            diff2 = (X - means_new[k]) ** 2
            variances_new[k] = (gamma[:, k:k + 1] * diff2).sum(axis=0) / weights[k]
        variances_new = np.maximum(variances_new, self.var_floor)   # 분산 붕괴(0으로 수렴) 방지

        return {"means": means_new, "vars": variances_new,
                "log_A": np.log(np.clip(A_new, 1e-300, None)),
                "log_pi": np.log(np.clip(pi_new, 1e-300, None))}

    # ---- 공개: 학습 (Baum-Welch, 다중 무작위 재시작) ----
    def fit(self, X: np.ndarray, n_iter: int = 200, tol: float = 1e-4,
            n_restarts: int = 8, verbose: bool = False) -> "GaussianHMM":
        rng = np.random.default_rng(self.seed)
        best_ll, best_params, best_iters = -np.inf, None, 0

        for restart in range(n_restarts):
            params = self._init_params(X, rng)
            ll_prev = -np.inf
            it = 0
            for it in range(n_iter):
                log_B = self._log_emission_matrix(X, params["means"], params["vars"])
                log_alpha = self._forward_raw(params["log_pi"], params["log_A"], log_B)
                log_beta = self._backward_raw(params["log_A"], log_B)
                ll = float(_logsumexp(log_alpha[-1]))

                gamma, xi_sum = self._e_step(log_alpha, log_beta, params["log_A"], log_B)
                params = self._m_step(X, gamma, xi_sum)

                if abs(ll - ll_prev) < tol:
                    break
                ll_prev = ll

            if verbose:
                print(f"   [재시작 {restart + 1}/{n_restarts}] 로그우도={ll:.2f} ({it + 1}회 반복)")
            if ll > best_ll:
                best_ll, best_params, best_iters = ll, params, it + 1

        self.means_ = best_params["means"]
        self.vars_ = best_params["vars"]
        self.log_A_ = best_params["log_A"]
        self.log_pi_ = best_params["log_pi"]
        self.train_loglik_ = best_ll
        self.train_iters_ = best_iters
        return self

    # ---- 공개: 인과적(causal) 순방향 필터 — 실거래/테스트에 쓸 것 ----
    def filter(self, X: np.ndarray, init_prob: np.ndarray | None = None) -> np.ndarray:
        """시점 t의 국면 확률을 X[0..t]만으로 계산한다 (미래 관측치 사용 안 함).
        init_prob를 주면 그 분포에서 이어서 필터링한다 (학습구간 마지막 날 확률을
        한 스텝 전이시킨 값을 넘기면, 학습→테스트 경계에서 정보가 끊기지 않는다)."""
        log_B = self._log_emission_matrix(X, self.means_, self.vars_)
        T, K = log_B.shape
        log_start = self.log_pi_ if init_prob is None else np.log(np.clip(init_prob, 1e-300, None))

        log_alpha = np.zeros((T, K))
        log_alpha[0] = log_start + log_B[0]
        log_alpha[0] -= _logsumexp(log_alpha[0])
        for t in range(1, T):
            step = _logsumexp(log_alpha[t - 1][:, None] + self.log_A_, axis=0) + log_B[t]
            log_alpha[t] = step - _logsumexp(step)
        return np.exp(log_alpha)

    def predict_next_prior(self, filtered_row: np.ndarray) -> np.ndarray:
        """마지막 날 필터링 확률에 전이행렬을 한 스텝 적용해 '다음날 사전확률'을 구한다.
        학습 구간과 테스트 구간을 이어붙일 때 filter()의 init_prob로 넘겨준다."""
        return filtered_row @ np.exp(self.log_A_)

    # ---- 공개: Viterbi — 사후 복기용 (미래 정보 사용, 시각화 전용) ----
    def viterbi(self, X: np.ndarray) -> np.ndarray:
        log_B = self._log_emission_matrix(X, self.means_, self.vars_)
        T, K = log_B.shape
        log_delta = np.zeros((T, K))
        psi = np.zeros((T, K), dtype=int)
        log_delta[0] = self.log_pi_ + log_B[0]
        for t in range(1, T):
            m = log_delta[t - 1][:, None] + self.log_A_
            psi[t] = np.argmax(m, axis=0)
            log_delta[t] = np.max(m, axis=0) + log_B[t]
        path = np.zeros(T, dtype=int)
        path[-1] = int(np.argmax(log_delta[-1]))
        for t in range(T - 2, -1, -1):
            path[t] = psi[t + 1, path[t + 1]]
        return path


# ============================================================
# 해석/검증 헬퍼
# ============================================================
_DEFAULT_LABELS_3 = ["하락장", "횡보장", "상승장"]


def label_states_by_return(model: GaussianHMM, feature_index: int = 0):
    """상태(state)는 EM이 임의 순서로 부여하므로, 평균수익률(feature_index 차원) 기준으로
    정렬한 순서(order)와 사람이 읽을 라벨(labels)을 만든다.
    order[rank] = 원래 state index, labels[rank] = 그 순위에 붙일 이름."""
    order = np.argsort(model.means_[:, feature_index])
    K = model.n_states
    if K == 3:
        labels = _DEFAULT_LABELS_3
    else:
        labels = [f"국면{r}(수익률 {'낮음' if r == 0 else '높음' if r == K - 1 else '중간'})"
                  for r in range(K)]
    return order, labels


def best_alignment_accuracy(pred_states: np.ndarray, true_states: np.ndarray, n_states: int):
    """예측 상태 인덱스는 참값과 순서가 다를 수 있으므로, 가능한 모든 순열 중
    정확도가 가장 높은 매칭을 찾는다 (국면 개수가 작을 때만 적합 — 순열 완전탐색)."""
    best_acc, best_perm = 0.0, tuple(range(n_states))
    for perm in permutations(range(n_states)):
        remapped = np.array(perm)[pred_states]
        acc = float((remapped == true_states).mean())
        if acc > best_acc:
            best_acc, best_perm = acc, perm
    return best_acc, best_perm


if __name__ == "__main__":
    # 빠른 자체 점검: 합성 데이터의 '진짜' 국면을 얼마나 잘 복원하는지 확인
    from . import config, data, features

    raw, true_states = data.fetch_with_regime()
    feat = features.build(raw)
    true_states = true_states[-len(feat):]
    X = np.nan_to_num(feat[["return_1d", "volatility_5d"]].values)

    model = GaussianHMM(n_states=config.REGIME_N_STATES, n_features=X.shape[1],
                         seed=config.RANDOM_SEED)
    model.fit(X, n_iter=config.REGIME_EM_ITERS, n_restarts=config.REGIME_EM_RESTARTS, verbose=True)

    path = model.viterbi(X)
    acc, _ = best_alignment_accuracy(path, true_states, config.REGIME_N_STATES)
    print(f"\n전체 구간(사후 복기) 기준 실제 국면과의 일치율: {acc:.1%} "
          f"(무작위 추측 기준 {1/config.REGIME_N_STATES:.1%})")
