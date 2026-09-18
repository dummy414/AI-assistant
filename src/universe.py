"""
universe.py — 유동성 기준으로 걸러낸 확장 유니버스
=====================================================================
watchlist.py의 UNIVERSE는 손으로 고른 대형주 65개다. 그건 안전하지만
소형주에서 벌어지는 일을 아예 못 본다.

여기서는 미국 상장 주식 전체(약 1.3만개)에서 시작하되, **유동성 하한**을
방어선으로 세운다. 시가총액이 아니라 실제 거래대금을 기준으로 삼는 이유:
시총이 커도 거래가 안 되는 종목은 못 사고 못 팔며, 뉴스 하나에 비정상적으로
튀기 때문이다.

기준 (MIN_PRICE / MIN_DOLLAR_VOLUME):
  - 주가 $5 이상       : 페니주 제외
  - 일평균 거래대금 $5M 이상 : 작전주·펌프앤덤프가 대부분 걸러진다

이 기준으로 약 1,000종목이 남는다. 참고로 이 하한은 의도적으로 일부 종목을
버린다 — 예를 들어 Spire Global(SPIR)은 주가 $12지만 일평균 거래대금이
$0.3M이라 통과하지 못한다. 그런 종목은 호재 하나에 급등락하고 원할 때
빠져나오기 어려워서, 정보로 다루기에 위험이 크다고 판단한 것이다.

실행:  python -m src.universe
"""

from __future__ import annotations
import json
import os
from datetime import datetime, timedelta, timezone

from . import config

MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME = 5_000_000  # 20일 평균 거래대금
LOOKBACK_DAYS = 40
BATCH_SIZE = 200


# ETF·펀드는 계약 수주나 소송 같은 기업 이벤트가 없으므로 유니버스에서 뺀다.
# Alpaca 자산 정보에 ETF 플래그가 없어서 종목명으로 판별한다 (완벽하진 않다).
_FUND_WORDS = (" etf", " etn", " fund", " index", " portfolio", " trust series")
_FUND_ISSUERS = ("ishares", "vanguard", "spdr", "invesco", "proshares", "direxion",
                 "schwab strategic", "wisdomtree", "global x", "first trust",
                 "janus henderson", "capital group", "dimensional ", "jpmorgan exchange")


def _is_fund(name: str) -> bool:
    n = " " + name.lower()
    return any(w in n for w in _FUND_WORDS) or any(n.strip().startswith(p) for p in _FUND_ISSUERS)


def _candidate_assets() -> dict[str, str]:
    """거래 가능한 미국 주식 심볼 → 종목명. OTC·워런트/유닛(.)·ETF는 제외."""
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetAssetsRequest
    from alpaca.trading.enums import AssetClass, AssetStatus

    client = TradingClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=True)
    assets = client.get_all_assets(
        GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
    )
    return {
        a.symbol: (a.name or a.symbol)
        for a in assets
        if a.tradable
        and str(a.exchange) != "AssetExchange.OTC"
        and "." not in a.symbol
        and not _is_fund(a.name or "")
    }


def build(min_price: float = MIN_PRICE,
          min_dollar_volume: float = MIN_DOLLAR_VOLUME) -> list[dict]:
    """유동성 하한을 통과한 종목 목록을 만든다."""
    from alpaca.data.historical.stock import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed

    names = _candidate_assets()
    symbols = sorted(names)
    print(f"1차 후보(거래가능·비OTC): {len(symbols)}종목")

    client = StockHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
    end = datetime.now()
    start = end - timedelta(days=LOOKBACK_DAYS)

    rows = []
    for i in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[i:i + BATCH_SIZE]
        try:
            df = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=batch, timeframe=TimeFrame.Day,
                start=start, end=end, feed=DataFeed(config.ALPACA_FEED)
            )).df
        except Exception as e:
            print(f"  배치 {i//BATCH_SIZE + 1} 실패(건너뜀): {e}")
            continue
        if df.empty:
            continue
        for sym in df.index.get_level_values(0).unique():
            d = df.loc[sym]
            if len(d) < 15:
                continue
            price = float(d["close"].iloc[-1])
            dollar_volume = float((d["close"] * d["volume"]).tail(20).mean())
            if price >= min_price and dollar_volume >= min_dollar_volume:
                rows.append({
                    "symbol": sym,
                    "name": names.get(sym, sym),
                    "price": round(price, 2),
                    "dollar_volume": round(dollar_volume),
                })
        done = min(i + BATCH_SIZE, len(symbols))
        print(f"  {done}/{len(symbols)} 스캔... 통과 {len(rows)}종목", end="\r")

    rows.sort(key=lambda r: -r["dollar_volume"])
    print(f"\n유동성 통과: {len(rows)}종목 "
          f"(주가 ${min_price}+ / 일평균 거래대금 ${min_dollar_volume/1e6:.0f}M+)")
    return rows


def path() -> str:
    return os.path.join(config.DATA_DIR, "universe.json")


def save(rows: list[dict]) -> str:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "min_price": MIN_PRICE,
        "min_dollar_volume": MIN_DOLLAR_VOLUME,
        "count": len(rows),
        "stocks": rows,
    }
    p = path()
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return p


def load() -> dict[str, dict]:
    """저장된 유니버스를 symbol → row 딕셔너리로 읽는다. 없으면 빈 dict."""
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    return {r["symbol"]: r for r in data.get("stocks", [])}


def main():
    rows = build()
    p = save(rows)
    print(f"저장 완료: {p}")
    print("\n거래대금 상위 10:")
    for r in rows[:10]:
        print(f"  {r['symbol']:6s} {r['name'][:38]:38s} ${r['dollar_volume']/1e6:>8.0f}M")
    print("\n거래대금 하위 10 (방어선 근처):")
    for r in rows[-10:]:
        print(f"  {r['symbol']:6s} {r['name'][:38]:38s} ${r['dollar_volume']/1e6:>8.1f}M")


if __name__ == "__main__":
    main()
