"""
screen_news.py — 오늘의 관심종목 스크리닝 + 뉴스 수집
=========================================================
매매나 예측을 하지 않는다. 대신:
  1. watchlist.py의 유동성 높은 유니버스 안에서 오늘 등락·거래량이 두드러진
     종목 top N을 추린다 (원시 전체 시장 스크리너는 잡주가 상위를 휩쓸어서 쓰지 않는다).
  2. 각 종목의 최근 뉴스(헤드라인+요약+출처)를 Alpaca News API로 가져온다.
  3. data/watchlist_snapshot.json에 원문 그대로 저장한다.

이 스크립트는 뉴스를 "해석"하지 않는다 — 수집만 한다. 카드뉴스용 짧은 해설은
이 JSON을 사람(또는 LLM)이 읽고 별도로 작성한다. 실제 뉴스 없이 만들어낸
설명을 카드에 올리면 안 되기 때문에, 원문과 분리해서 무엇이 실제 데이터이고
무엇이 요약인지 항상 구분할 수 있게 한다.

실행:  python screen_news.py
"""

import os
import re
import json
from datetime import datetime, timedelta, timezone

from src import config, watchlist


def _strip_html(html: str) -> str:
    """뉴스 본문(content)은 HTML로 온다. 의존성 추가 없이 태그만 정규식으로 제거해
    사람이 읽을 수 있는 평문으로 바꾼다 (완벽한 파서는 아니지만 요약 작성엔 충분하다)."""
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_news_batch(symbols: list[str], limit_per_symbol: int = 4, lookback_days: int = 7) -> dict:
    """종목별 뉴스를 헤드라인/요약뿐 아니라 본문 전문(content)과 대표 이미지까지 가져온다.
    본문은 카드뉴스에서 "한국어로 자세히 보기"를 만들 때 쓰고, 이 함수 자체는 번역/요약을
    하지 않는다 (screen_news.py의 원칙: 수집만 하고 해석은 사람/LLM이 별도로 한다)."""
    from alpaca.data.historical.news import NewsClient
    from alpaca.data.requests import NewsRequest

    client = NewsClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
    start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    req = NewsRequest(symbols=",".join(symbols), start=start, limit=50, include_content=True)
    items = client.get_news(req).data["news"]

    by_symbol = {s: [] for s in symbols}
    for n in items:
        # 이미지 사이즈 중 "small"을 대표 이미지로 쓴다 (large는 너무 무겁고 thumb은 너무 작음)
        image = None
        for img in (n.images or []):
            size = getattr(img.size, "value", img.size)
            if size == "small":
                image = img.url
                break
        if image is None and n.images:
            image = n.images[0].url

        for s in n.symbols:
            if s in by_symbol and len(by_symbol[s]) < limit_per_symbol:
                by_symbol[s].append({
                    "headline": n.headline,
                    "summary": n.summary,
                    "content_text": _strip_html(n.content)[:4000],
                    "image": image,
                    "url": n.url,
                    "source": n.source,
                    "published_at": n.created_at.isoformat(),
                })
    return by_symbol


def compute_spark_and_relative(symbols: list[str], lookback_days: int = 25) -> tuple[dict, float]:
    """종목별 최근 종가(spark)와 SPY 대비 당일 상대강도(rel_strength)를 계산한다."""
    bars = watchlist._fetch_batch_bars(symbols + ["SPY"], lookback_days=lookback_days)
    spy = bars.loc["SPY"].sort_index()
    spy_day_return = float(spy["close"].iloc[-1] / spy["close"].iloc[-2] - 1)

    out = {}
    for sym in symbols:
        df = bars.loc[sym].sort_index()
        day_return = float(df["close"].iloc[-1] / df["close"].iloc[-2] - 1)
        out[sym] = {
            "spark": df["close"].tail(12).round(2).tolist(),
            "rel_strength": round(day_return - spy_day_return, 4),
        }
    return out, spy_day_return


def main():
    os.makedirs(config.DATA_DIR, exist_ok=True)

    print("=" * 60)
    print("1) 유니버스 스크리닝 (유동성 높은 종목 중 오늘 변동/거래량 상위)")
    print("=" * 60)
    table = watchlist.screen(top_n=10)
    print(table[["symbol", "name", "price", "day_return", "volume_ratio"]]
          .to_string(index=False, formatters={
              "price": "${:.2f}".format,
              "day_return": "{:+.2%}".format,
              "volume_ratio": "{:.2f}x".format,
          }))

    print("\n" + "=" * 60)
    print("2) 뉴스 수집 (Alpaca News API)")
    print("=" * 60)
    symbols = table["symbol"].tolist()
    news_map = fetch_news_batch(symbols)
    for s in symbols:
        print(f"   {s}: {len(news_map.get(s, []))}건")

    print("\n" + "=" * 60)
    print("3) 스파크라인 + SPY 대비 상대강도")
    print("=" * 60)
    spark_map, spy_day_return = compute_spark_and_relative(symbols)
    print(f"   SPY 당일: {spy_day_return:+.2%}")

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "spy_day_return": spy_day_return,
        "stocks": [],
    }
    for _, row in table.iterrows():
        sym = row["symbol"]
        snapshot["stocks"].append({
            "symbol": sym,
            "name": row["name"],
            "price": round(float(row["price"]), 2),
            "day_return": float(row["day_return"]),
            "return_5d": float(row["return_5d"]),
            "volume_ratio": float(row["volume_ratio"]),
            "spark": spark_map[sym]["spark"],
            "rel_strength": spark_map[sym]["rel_strength"],
            "news": news_map.get(sym, []),
        })

    path = os.path.join(config.DATA_DIR, "watchlist_snapshot.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    n_news = sum(len(s["news"]) for s in snapshot["stocks"])
    print(f"\n저장 완료: {path}")
    print(f"(종목 {len(snapshot['stocks'])}개, 뉴스 {n_news}건 — 뉴스 없는 종목은 카드에서 '뉴스 없음'으로 표시할 것)")


if __name__ == "__main__":
    main()
