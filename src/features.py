"""
features.py — 기술적 지표 계산
===============================
가격 DataFrame을 받아 모델·에이전트가 쓸 특징(feature)들을 만든다.
data.py의 출력(open/high/low/close/volume)을 입력으로 받는다.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

FEATURE_COLS = ["return_1d", "ma_ratio", "rsi14", "volume_chg", "volatility_5d"]


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def build(df: pd.DataFrame, add_target: bool = True) -> pd.DataFrame:
    """가격 DataFrame -> 특징 + (선택)타깃이 붙은 DataFrame. 결측 행은 제거한다."""
    out = df.copy()
    out["return_1d"] = out["close"].pct_change()
    out["ma5"] = out["close"].rolling(5).mean()
    out["ma20"] = out["close"].rolling(20).mean()
    out["ma50"] = out["close"].rolling(50).mean()
    out["ma_ratio"] = out["ma5"] / out["ma20"]
    out["rsi14"] = compute_rsi(out["close"], 14)
    out["volume_chg"] = out["volume"].pct_change()
    out["volatility_5d"] = out["return_1d"].rolling(5).std()

    if add_target:
        # 다음날 종가가 오늘보다 높으면 1, 아니면 0
        out["target"] = (out["close"].shift(-1) > out["close"]).astype(int)

    return out.dropna()


if __name__ == "__main__":
    from . import data
    raw = data.fetch()
    feat = build(raw)
    print(f"원본 {len(raw)}행 -> 피처 생성 후 {len(feat)}행")
    print(feat[FEATURE_COLS + ["target"]].tail())
