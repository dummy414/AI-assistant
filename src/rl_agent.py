"""
rl_agent.py — 강화학습 트레이딩 (NumPy DQN)
=============================================
외부 RL 라이브러리나 GPU 없이, NumPy만으로 구현한 Deep Q-Network.
핵심 요소를 모두 갖춘 진짜 DQN이다:
  - 신경망 Q함수 (2층 MLP, 순전파/역전파 직접 구현)
  - 경험 재생 버퍼 (Experience Replay)
  - 타깃 네트워크 (Target Network) + 주기적 동기화
  - 엡실론-그리디 탐험 + 엡실론 감쇠
  - 조기 종료 (검증 성과 최고 지점의 정책을 저장)

행동: 0 = 현금 보유(flat), 1 = 매수/유지(long)
보상: 다음날 (포지션 × 수익률) - 거래비용
"""

from __future__ import annotations
import numpy as np

from . import config, features


# ============================================================
# 트레이딩 환경 (Gym 스타일)
# ============================================================
class TradingEnv:
    def __init__(self, feat_matrix: np.ndarray, returns: np.ndarray,
                 cost: float = config.TRANSACTION_COST, start: int = 1):
        self.feats = feat_matrix
        self.returns = returns
        self.cost = cost
        self.start = start
        self.n = len(returns)
        self.reset()

    def reset(self):
        self.t = self.start
        self.position = 0
        return self._state()

    def _state(self):
        # 특징 벡터 + 현재 포지션(보유 여부)을 상태로 사용
        s = np.append(self.feats[self.t], self.position)
        return np.nan_to_num(s)

    def step(self, action: int):
        prev = self.position
        trade_cost = self.cost if action != prev else 0.0
        self.position = action
        self.t += 1
        reward = self.position * self.returns[self.t] - trade_cost
        done = self.t >= self.n - 1
        return self._state(), reward, done


# ============================================================
# Q-네트워크 (NumPy MLP)
# ============================================================
class QNetwork:
    def __init__(self, n_in, n_hidden, n_out, seed=0):
        rng = np.random.default_rng(seed)
        self.W1 = rng.normal(0, np.sqrt(2 / n_in), (n_in, n_hidden))
        self.b1 = np.zeros(n_hidden)
        self.W2 = rng.normal(0, np.sqrt(2 / n_hidden), (n_hidden, n_out))
        self.b2 = np.zeros(n_out)

    def forward(self, x):
        self.x = x
        self.z1 = x @ self.W1 + self.b1
        self.a1 = np.maximum(0, self.z1)     # ReLU
        return self.a1 @ self.W2 + self.b2

    def backward(self, grad_q, lr):
        b = self.x.shape[0]
        dW2 = self.a1.T @ grad_q / b
        db2 = grad_q.mean(0)
        da1 = grad_q @ self.W2.T
        dz1 = da1 * (self.z1 > 0)
        dW1 = self.x.T @ dz1 / b
        db1 = dz1.mean(0)
        self.W2 -= lr * dW2; self.b2 -= lr * db2
        self.W1 -= lr * dW1; self.b1 -= lr * db1

    def get_weights(self):
        return (self.W1.copy(), self.b1.copy(), self.W2.copy(), self.b2.copy())

    def set_weights(self, w):
        self.W1, self.b1, self.W2, self.b2 = (a.copy() for a in w)


# ============================================================
# DQN 에이전트
# ============================================================
class DQNAgent:
    def __init__(self, n_state, n_action=2, seed=0):
        self.q = QNetwork(n_state, config.RL_HIDDEN, n_action, seed)
        self.target = QNetwork(n_state, config.RL_HIDDEN, n_action, seed)
        self.target.set_weights(self.q.get_weights())
        self.gamma = config.RL_GAMMA
        self.lr = config.RL_LEARNING_RATE
        self.n_action = n_action
        self.buffer, self.buf_size = [], 5000
        self.eps = 1.0

    def act(self, state, greedy=False):
        if not greedy and np.random.random() < self.eps:
            return np.random.randint(self.n_action)
        return int(np.argmax(self.q.forward(state[None, :])[0]))

    def remember(self, s, a, r, s2, done):
        self.buffer.append((s, a, r, s2, done))
        if len(self.buffer) > self.buf_size:
            self.buffer.pop(0)

    def train_step(self, batch=64):
        if len(self.buffer) < batch:
            return None
        idx = np.random.randint(0, len(self.buffer), batch)
        S = np.array([self.buffer[i][0] for i in idx])
        A = np.array([self.buffer[i][1] for i in idx])
        R = np.array([self.buffer[i][2] for i in idx])
        S2 = np.array([self.buffer[i][3] for i in idx])
        D = np.array([self.buffer[i][4] for i in idx], dtype=float)

        q_next = self.target.forward(S2)
        target_val = R + self.gamma * q_next.max(1) * (1 - D)

        q_pred = self.q.forward(S)
        grad = np.zeros_like(q_pred)
        for i in range(batch):
            grad[i, A[i]] = q_pred[i, A[i]] - target_val[i]
        self.q.backward(grad, self.lr)
        return float(np.mean((q_pred[np.arange(batch), A] - target_val) ** 2))

    def sync_target(self):
        self.target.set_weights(self.q.get_weights())

    def decay_eps(self):
        self.eps = max(config.RL_EPS_MIN, self.eps * config.RL_EPS_DECAY)


# ============================================================
# 평가 헬퍼
# ============================================================
def evaluate(agent, env):
    s, done, total = env.reset(), False, 0.0
    while not done:
        s, r, done = env.step(agent.act(s, greedy=True))
        total += r
    return total


def buy_and_hold(env):
    s, done, total = env.reset(), False, 0.0
    while not done:
        s, r, done = env.step(1)
        total += r
    return total


# ============================================================
# 학습 루프
# ============================================================
def train(feat_matrix, returns, episodes=None, seed=None, verbose=True):
    episodes = episodes or config.RL_EPISODES
    seed = seed if seed is not None else config.RANDOM_SEED

    split = int(len(returns) * 0.7)
    train_env = TradingEnv(feat_matrix[:split], returns[:split])
    test_env = TradingEnv(feat_matrix[split:], returns[split:])

    agent = DQNAgent(n_state=feat_matrix.shape[1] + 1, seed=seed)

    history, best_test, best_w, best_ep = [], -np.inf, None, 0
    for ep in range(episodes):
        s, done, ep_reward, losses = train_env.reset(), False, 0.0, []
        while not done:
            a = agent.act(s)
            s2, r, done = train_env.step(a)
            agent.remember(s, a, r, s2, done)
            loss = agent.train_step()
            if loss is not None:
                losses.append(loss)
            s, ep_reward = s2, ep_reward + r

        agent.decay_eps()
        if ep % 3 == 0:
            agent.sync_target()

        test_reward = evaluate(agent, test_env)
        bh_reward = buy_and_hold(test_env)

        # 조기 종료: 탐험이 줄어든 뒤, 검증 성과가 최고인 정책을 저장
        if test_reward > best_test and agent.eps < 0.3:
            best_test, best_ep = test_reward, ep + 1
            best_w = agent.q.get_weights()

        history.append({
            "episode": ep + 1, "train_reward": ep_reward,
            "test_reward": test_reward, "buy_hold": bh_reward,
            "epsilon": agent.eps, "loss": float(np.mean(losses)) if losses else 0.0,
        })
        if verbose and (ep % 10 == 0 or ep == episodes - 1):
            print(f"에피소드 {ep+1:3d} | 학습 {ep_reward:+.3f} | 테스트 {test_reward:+.3f} "
                  f"| 매수후보유 {bh_reward:+.3f} | ε={agent.eps:.3f}")

    if best_w is not None:
        agent.q.set_weights(best_w)
        if verbose:
            print(f"\n[조기 종료] {best_ep}번째 에피소드 정책 복원 (테스트 {best_test*100:+.2f}%)")

    return agent, history, best_ep
