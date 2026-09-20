"""
watchlist.py — 관심종목 유니버스 + "오늘 화제인 종목" 스크리닝
=====================================================================
Alpaca의 원시 top-movers/most-actives 스크리너는 유동성이 거의 없는
저가·소형주가 상위를 휩쓴다 (예: 하루 +450% 급등한 무명 티커).
그런 종목을 "추천"에 올리는 건 투자 조언으로서 무책임하다.

그래서 이 모듈은:
  1. 이미 이름이 알려진 유동성 높은 대형·중견주 유니버스(UNIVERSE) 안에서만 고르고
  2. 그 안에서 등락률·거래량 급증 정도로 "오늘 화제인 종목"을 랭킹한다.

UNIVERSE는 정적 리스트다. 실시간으로 상장폐지되거나 사명이 바뀔 수 있으니
주기적으로 손으로 갱신해야 한다 — 자동으로 시장 전체를 스캔하지 않는 것은
의도된 안전장치다.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

from . import config

# ---- 유동성 높은 대형·중견주 유니버스 (섹터별) ----
UNIVERSE: dict[str, str] = {
    # 기술
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "GOOGL": "Alphabet",
    "AMZN": "Amazon", "META": "Meta Platforms", "AVGO": "Broadcom", "ORCL": "Oracle",
    "CRM": "Salesforce", "ADBE": "Adobe", "AMD": "AMD", "INTC": "Intel",
    "QCOM": "Qualcomm", "TXN": "Texas Instruments", "CSCO": "Cisco", "IBM": "IBM",
    "NOW": "ServiceNow", "PANW": "Palo Alto Networks", "SNOW": "Snowflake",
    "PLTR": "Palantir", "SHOP": "Shopify", "UBER": "Uber", "ABNB": "Airbnb",
    # 통신/미디어
    "DIS": "Disney", "NFLX": "Netflix", "CMCSA": "Comcast", "T": "AT&T", "VZ": "Verizon",
    # 임의소비재
    "TSLA": "Tesla", "HD": "Home Depot", "MCD": "McDonald's", "NKE": "Nike",
    "SBUX": "Starbucks", "TGT": "Target", "LOW": "Lowe's",
    # 필수소비재
    "WMT": "Walmart", "PG": "Procter & Gamble", "KO": "Coca-Cola", "PEP": "PepsiCo",
    "COST": "Costco", "CL": "Colgate-Palmolive",
    # 금융
    "JPM": "JPMorgan Chase", "BAC": "Bank of America", "GS": "Goldman Sachs",
    "MA": "Mastercard", "V": "Visa", "PYPL": "PayPal", "AXP": "American Express",
    "SCHW": "Charles Schwab", "COIN": "Coinbase",
    # 헬스케어
    "UNH": "UnitedHealth", "JNJ": "Johnson & Johnson", "PFE": "Pfizer",
    "MRK": "Merck", "LLY": "Eli Lilly", "ABBV": "AbbVie",
    # 에너지
    "XOM": "ExxonMobil", "CVX": "Chevron", "COP": "ConocoPhillips",
    # 산업재
    "BA": "Boeing", "CAT": "Caterpillar", "GE": "GE Aerospace", "UPS": "UPS", "HON": "Honeywell",
}


def active_universe() -> dict[str, str]:
    """스크리닝에 쓸 유니버스(심볼 → 종목명).

    src/universe.py가 만들어둔 유동성 유니버스(약 1,000종목)가 있으면 그걸 쓰고,
    없으면 아래 손으로 고른 65종목으로 물러난다. 넓은 쪽을 쓰면 매일 나오는 종목이
    훨씬 다양해지지만, 유동성 하한(주가 $5+ / 일평균 거래대금 $5M+)이 방어선 역할을 한다.
    """
    from . import universe
    liquid = universe.load()
    if liquid:
        return {sym: row["name"] for sym, row in liquid.items()}
    return dict(UNIVERSE)


def _fetch_batch_bars(symbols: list[str], lookback_days: int = 45) -> pd.DataFrame:
    """유니버스 전 종목의 일봉을 단일 배치 요청으로 가져온다 (API 호출 최소화)."""
    from datetime import datetime, timedelta
    from alpaca.data.historical.stock import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed

    client = StockHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
    end = datetime.now()
    start = end - timedelta(days=lookback_days)

    # 유니버스가 1,000종목 규모로 커지면 한 번에 요청하기엔 응답이 너무 크다.
    frames = []
    for i in range(0, len(symbols), 200):
        req = StockBarsRequest(symbol_or_symbols=symbols[i:i + 200], timeframe=TimeFrame.Day,
                               start=start, end=end, feed=DataFeed(config.ALPACA_FEED))
        try:
            df = client.get_stock_bars(req).df
        except Exception as e:
            print(f"  바 조회 실패(건너뜀) {i}~{i+200}: {e}")
            continue
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames)


def last_bar_date(symbol: str = "SPY") -> str | None:
    """마지막 거래일(YYYY-MM-DD). 실행 날짜와 다를 수 있다.

    이게 없어서 사고가 났다. 자동화는 매일 아침 도는데, 토요일 아침에 돌면
    시장은 금요일이 마지막이다. 그런데 카드에는 실행 날짜가 '시장 날짜'로 적혀서
    "9월 19일 미국 증시는…"이라는 문장이 나왔다 — 9월 19일은 토요일이었다.
    날짜는 실행 시각이 아니라 **데이터에서** 가져와야 한다.
    """
    bars = _fetch_batch_bars([symbol], lookback_days=12)
    if bars.empty or symbol not in set(bars.index.get_level_values(0)):
        return None
    df = bars.loc[symbol].sort_index()
    return str(df.index[-1])[:10]


def screen(top_n: int = 10, min_price: float = 5.0) -> pd.DataFrame:
    """
    유니버스 안에서 '오늘 화제인 종목' top_n개를 고른다.

    점수 = (전일대비 등락률 절대값 순위) + (거래량 급증배수 순위) — 둘 다 순위가
    높을수록(=변동이 크고 거래가 몰릴수록) 뉴스에서 다뤄질 만한 종목일 가능성이 크다.
    """
    names = active_universe()
    symbols = list(names)
    bars = _fetch_batch_bars(symbols)
    available = set(bars.index.get_level_values(0)) if not bars.empty else set()

    rows = []
    for sym in symbols:
        if sym not in available:
            continue
        df = bars.loc[sym].sort_index()
        if len(df) < 6:
            continue
        last_close = df["close"].iloc[-1]
        prev_close = df["close"].iloc[-2]
        day_ret = last_close / prev_close - 1
        ret_5d = last_close / df["close"].iloc[-6] - 1 if len(df) >= 6 else np.nan
        avg_vol_20 = df["volume"].iloc[-21:-1].mean() if len(df) >= 21 else df["volume"].iloc[:-1].mean()
        vol_ratio = df["volume"].iloc[-1] / avg_vol_20 if avg_vol_20 > 0 else 1.0

        if last_close < min_price:
            continue
        rows.append({
            "symbol": sym, "name": names[sym], "price": last_close,
            "day_return": day_ret, "return_5d": ret_5d, "volume_ratio": vol_ratio,
        })

    table = pd.DataFrame(rows)
    table["rank_ret"] = table["day_return"].abs().rank(ascending=False)
    table["rank_vol"] = table["volume_ratio"].rank(ascending=False)
    table["score"] = table["rank_ret"] + table["rank_vol"]
    table = table.sort_values("score").head(top_n).reset_index(drop=True)
    return table


def stock_stats(symbols: list[str], lookback_days: int = 400) -> dict[str, dict]:
    """선정된 종목들의 '자기 과거 대비' 통계를 계산한다.

    레이더 포착과 같은 지표지만, 여기서는 이례성 랭킹이 아니라 카드에 붙일 맥락으로 쓴다.
    "오늘 등락이 평소 변동폭의 몇 배인가", "52주 범위의 어디쯤인가" 같은 건 예측이 아니라
    관측된 사실이라 카드에 넣어도 정직하다.
    """
    bars = _fetch_batch_bars(symbols, lookback_days=lookback_days)
    if bars.empty:
        return {}
    available = set(bars.index.get_level_values(0))

    out: dict[str, dict] = {}
    for sym in symbols:
        if sym not in available:
            continue
        df = bars.loc[sym].sort_index()
        if len(df) < 30:
            continue
        close = df["close"].values
        volume = df["volume"].values
        rets = close[1:] / close[:-1] - 1
        today_ret = float(rets[-1])
        base = rets[:-1]
        ret_std = float(base.std())
        vol_base = volume[:-1]
        vol_std = float(vol_base.std())

        sign_today = np.sign(today_ret)
        streak, i = 1, len(rets) - 2
        while i >= 0 and sign_today != 0 and np.sign(rets[i]) == sign_today:
            streak += 1
            i -= 1

        window = close[-252:] if len(close) >= 252 else close
        high, low = float(window.max()), float(window.min())
        last = float(close[-1])

        # ---- 기간별 수익률 (1일/5일/1개월/3개월/1년) ----
        def ret_over(days: int):
            if len(close) <= days:
                return None
            return round(float(close[-1] / close[-1 - days] - 1), 4)

        periods = {
            "1d": round(today_ret, 4),
            "5d": ret_over(5),
            "1m": ret_over(21),
            "3m": ret_over(63),
            "1y": ret_over(252),
        }

        # ---- 변동성 기반 예상 범위 ----
        # 방향이 아니라 '폭'이다. 일간 변동성 σ를 기간 길이의 제곱근으로 스케일링하면
        # 그 기간 등락이 ±σ 안에 들어올 확률이 대략 68%, ±2σ면 약 95%다.
        # (정규분포 가정이라 실제로는 꼬리가 더 두껍다 — 화면에 그 한계를 같이 적는다.)
        expected = None
        if ret_std > 0:
            expected = {
                "1d": round(ret_std, 4),
                "5d": round(ret_std * (5 ** 0.5), 4),
                "1m": round(ret_std * (21 ** 0.5), 4),
            }

        # ---- 과거 상승일 비율 (예측이 아니라 관측된 빈도) ----
        recent = rets[-252:] if len(rets) >= 252 else rets
        up_days = int((recent > 0).sum())
        up_day_ratio = round(up_days / len(recent), 3) if len(recent) else None

        out[sym] = {
            "periods": periods,
            "expected_move": expected,
            "up_day_ratio": up_day_ratio,
            "up_day_sample": int(len(recent)),
            # 오늘 등락이 평소 하루 변동폭의 몇 배인지 (절대값)
            "move_vs_normal": round(abs(today_ret) / ret_std, 1) if ret_std > 0 else None,
            "return_z": round((today_ret - float(base.mean())) / ret_std, 2) if ret_std > 0 else None,
            "vol_z": round((float(volume[-1]) - float(vol_base.mean())) / vol_std, 2) if vol_std > 0 else None,
            "streak": int(streak),
            "streak_direction": "상승" if sign_today > 0 else ("하락" if sign_today < 0 else None),
            "high_52w": round(high, 2),
            "low_52w": round(low, 2),
            # 52주 범위에서 현재가의 위치 (0=최저가, 1=최고가)
            "pct_in_52w_range": round((last - low) / (high - low), 3) if high > low else None,
            "annual_volatility": round(ret_std * (252 ** 0.5), 3),
        }
    return out


def screen_anomalies(top_n: int = 8, lookback_days: int = 260, min_price: float = 5.0) -> list[dict]:
    """
    "레이더 포착" — 오를지 내릴지를 예측하지 않고, 각 종목이 '자기 자신의 평소 패턴'과
    비교해 통계적으로 이례적인지만 판단한다. 방향성 예측이 아니라 기술 서술이다.

    종목별로 자기 자신의 과거 분포와 비교해 아래 네 가지를 체크한다:
      1. return_z  : 오늘 등락률이 최근 변동폭(표준편차) 대비 몇 배 이례적인가
      2. vol_z     : 오늘 거래량이 평소 대비 몇 배 이례적인가
      3. streak    : 같은 방향으로 며칠 연속 움직였는가
      4. 52주 고점/저점 근접 여부

    이 중 하나라도 임계값을 넘으면 "심상치 않음" 후보에 올린다.
    """
    names = active_universe()
    symbols = list(names)
    bars = _fetch_batch_bars(symbols, lookback_days=lookback_days)
    available = set(bars.index.get_level_values(0)) if not bars.empty else set()

    candidates = []
    for sym in symbols:
        if sym not in available:
            continue
        df = bars.loc[sym].sort_index()
        if len(df) < 30:
            continue
        close = df["close"].values
        volume = df["volume"].values
        last_close = close[-1]
        if last_close < min_price:
            continue

        rets = close[1:] / close[:-1] - 1
        today_ret = rets[-1]
        baseline_rets = rets[:-1]
        ret_std = baseline_rets.std()
        return_z = (today_ret - baseline_rets.mean()) / ret_std if ret_std > 0 else 0.0

        today_vol = volume[-1]
        baseline_vol = volume[:-1]
        vol_std = baseline_vol.std()
        vol_z = (today_vol - baseline_vol.mean()) / vol_std if vol_std > 0 else 0.0

        sign_today = np.sign(today_ret)
        streak = 1
        i = len(rets) - 2
        while i >= 0 and sign_today != 0 and np.sign(rets[i]) == sign_today:
            streak += 1
            i -= 1

        window_close = close[-252:] if len(close) >= 252 else close
        high_w = window_close.max()
        low_w = window_close.min()
        near_high = last_close >= high_w * 0.98
        near_low = last_close <= low_w * 1.02

        reasons = []
        if abs(return_z) >= 2.5:
            direction = "급등" if today_ret > 0 else "급락"
            reasons.append({
                "type": "return_z", "value": round(float(return_z), 2),
                "text": f"오늘 {direction}이 최근 {lookback_days}일 평소 변동폭 대비 {abs(return_z):.1f}배로 통계적으로 이례적입니다.",
            })
        if vol_z >= 3.0:
            reasons.append({
                "type": "vol_z", "value": round(float(vol_z), 2),
                "text": f"오늘 거래량이 평소 평균보다 {vol_z:.1f}표준편차만큼 이례적으로 많습니다.",
            })
        if streak >= 5:
            direction = "상승" if sign_today > 0 else "하락"
            reasons.append({
                "type": "streak", "value": streak,
                "text": f"{streak}일 연속으로 {direction} 흐름이 이어지고 있어 우연으로 보기엔 드문 패턴입니다.",
            })
        if near_high:
            reasons.append({
                "type": "near_high", "value": round(float(last_close / high_w), 4),
                "text": "52주 최고가 근처에 바짝 붙어 있습니다.",
            })
        if near_low:
            reasons.append({
                "type": "near_low", "value": round(float(last_close / low_w), 4),
                "text": "52주 최저가 근처에 바짝 붙어 있습니다.",
            })

        if not reasons:
            continue

        anomaly_score = abs(return_z) + max(vol_z, 0) / 2 + (streak >= 5) * 2 + (near_high or near_low) * 1.5
        candidates.append({
            "symbol": sym, "name": names[sym], "price": round(float(last_close), 2),
            "day_return": round(float(today_ret), 4), "return_z": round(float(return_z), 2),
            "vol_z": round(float(vol_z), 2), "streak": int(streak),
            "near_high": bool(near_high), "near_low": bool(near_low),
            "reasons": reasons, "anomaly_score": round(float(anomaly_score), 2),
        })

    candidates.sort(key=lambda c: c["anomaly_score"], reverse=True)
    return candidates[:top_n]


if __name__ == "__main__":
    t = screen()
    print(t[["symbol", "name", "price", "day_return", "return_5d", "volume_ratio"]]
          .to_string(index=False, formatters={
              "price": "${:.2f}".format,
              "day_return": "{:+.2%}".format,
              "return_5d": "{:+.2%}".format,
              "volume_ratio": "{:.2f}x".format,
          }))
