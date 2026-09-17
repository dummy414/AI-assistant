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
    req = StockBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Day,
                            start=start, end=end, feed=DataFeed(config.ALPACA_FEED))
    return client.get_stock_bars(req).df


def screen(top_n: int = 10, min_price: float = 5.0) -> pd.DataFrame:
    """
    유니버스 안에서 '오늘 화제인 종목' top_n개를 고른다.

    점수 = (전일대비 등락률 절대값 순위) + (거래량 급증배수 순위) — 둘 다 순위가
    높을수록(=변동이 크고 거래가 몰릴수록) 뉴스에서 다뤄질 만한 종목일 가능성이 크다.
    """
    symbols = list(UNIVERSE.keys())
    bars = _fetch_batch_bars(symbols)

    rows = []
    for sym in symbols:
        if sym not in bars.index.get_level_values(0):
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
            "symbol": sym, "name": UNIVERSE[sym], "price": last_close,
            "day_return": day_ret, "return_5d": ret_5d, "volume_ratio": vol_ratio,
        })

    table = pd.DataFrame(rows)
    table["rank_ret"] = table["day_return"].abs().rank(ascending=False)
    table["rank_vol"] = table["volume_ratio"].rank(ascending=False)
    table["score"] = table["rank_ret"] + table["rank_vol"]
    table = table.sort_values("score").head(top_n).reset_index(drop=True)
    return table


if __name__ == "__main__":
    t = screen()
    print(t[["symbol", "name", "price", "day_return", "return_5d", "volume_ratio"]]
          .to_string(index=False, formatters={
              "price": "${:.2f}".format,
              "day_return": "{:+.2%}".format,
              "return_5d": "{:+.2%}".format,
              "volume_ratio": "{:.2f}x".format,
          }))
