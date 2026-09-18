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

from src import config, watchlist, events


# ---- 촉매(catalyst) 분류 ----
# "많이 움직인 종목"만 고르면 왜 움직였는지 모르는 밋밋한 카드가 섞인다. 그래서 뉴스를
# 읽어 **무슨 일이 있었는지**로 한 번 더 거른다. 단계가 높을수록 읽을거리가 있는 종목이다.
#   3: 금액이 명시된 구체적 사건 (계약·인수·소송·리콜 등)
#   2: 실적 발표 관련
#   1: 애널리스트 의견 변경
#   0: 뉴스는 있으나 위 분류에 안 걸림
#  -1: 뉴스 없음
_EARNINGS_RE = re.compile(
    r"\b(earnings|results|quarter|q[1-4]\s|guidance|revenue|beat|miss(?:ed|es)?|outlook|forecast)\b", re.I)
_ANALYST_RE = re.compile(
    r"\b(upgrade[sd]?|downgrade[sd]?|price target|initiat\w+ coverage|rating|analyst)\b", re.I)

CATALYST_LABEL = {3: "구체적 사건", 2: "실적", 1: "애널리스트", 0: "일반 뉴스", -1: "뉴스 없음"}


def catalyst_tier(news_items: list[dict]) -> tuple[int, str | None]:
    """종목의 뉴스 묶음에서 가장 강한 촉매 단계와 사건 유형을 판단한다."""
    if not news_items:
        return -1, None
    best, best_type = 0, None
    for n in news_items:
        text = f"{n.get('headline','')} {n.get('summary','')}"
        event_type, _ = events.classify(text)
        if event_type and events.parse_amounts(text):
            return 3, event_type          # 금액까지 명시된 사건이면 더 볼 것도 없다
        if event_type and best < 2:
            best, best_type = 2, event_type
        if _EARNINGS_RE.search(text) and best < 2:
            best, best_type = 2, "실적 발표"
        if _ANALYST_RE.search(text) and best < 1:
            best, best_type = 1, "애널리스트 의견"
    return best, best_type


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
    # 유니버스가 1,000종목으로 넓어지면 무명 종목이 상위에 올 수 있다. 넉넉히 뽑아서
    # 뉴스가 있는 종목을 우선 채우고, 모자라면 점수 순으로 마저 채운다
    # (뉴스 없는 카드가 여러 장이면 읽을 게 없어지므로).
    SHORTLIST = 24
    FINAL = 10
    shortlist = watchlist.screen(top_n=SHORTLIST)
    print(shortlist[["symbol", "name", "price", "day_return", "volume_ratio"]].head(SHORTLIST)
          .to_string(index=False, formatters={
              "price": "${:.2f}".format,
              "day_return": "{:+.2%}".format,
              "volume_ratio": "{:.2f}x".format,
          }))

    print("\n" + "=" * 60)
    print("2) 뉴스 수집 (Alpaca News API)")
    print("=" * 60)
    shortlist_symbols = shortlist["symbol"].tolist()
    news_map = fetch_news_batch(shortlist_symbols)

    print("\n" + "=" * 60)
    print("3) 촉매 분류 — 무슨 일이 있었는지로 한 번 더 거르기")
    print("=" * 60)
    # 움직임 순위(rank)는 그대로 두고, 촉매 단계를 1순위 정렬키로 쓴다.
    # 같은 단계 안에서는 원래의 등락·거래량 점수 순서가 유지된다.
    catalysts = {}
    for i, sym in enumerate(shortlist_symbols):
        tier, event_type = catalyst_tier(news_map.get(sym, []))
        catalysts[sym] = {"tier": tier, "event_type": event_type, "rank": i}

    ordered = sorted(shortlist_symbols,
                     key=lambda s: (-catalysts[s]["tier"], catalysts[s]["rank"]))
    symbols = ordered[:FINAL]
    table = (shortlist[shortlist["symbol"].isin(symbols)]
             .set_index("symbol").loc[symbols].reset_index())

    for tier in (3, 2, 1, 0, -1):
        group = [s for s in shortlist_symbols if catalysts[s]["tier"] == tier]
        if group:
            print(f"   [{CATALYST_LABEL[tier]}] {len(group)}개: {', '.join(group)}")
    print(f"\n   후보 {len(shortlist_symbols)}개 → 촉매가 강한 순으로 {len(symbols)}개 선정")
    for s in symbols:
        c = catalysts[s]
        tag = c["event_type"] or CATALYST_LABEL[c["tier"]]
        print(f"   {s}: 뉴스 {len(news_map.get(s, []))}건 · {tag}")

    print("\n" + "=" * 60)
    print("4) 스파크라인 + SPY 대비 상대강도")
    print("=" * 60)
    spark_map, spy_day_return = compute_spark_and_relative(symbols)
    print(f"   SPY 당일: {spy_day_return:+.2%}")

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "spy_day_return": spy_day_return,
        # 유니버스 크기를 기록해둔다. 어느 날 갑자기 65로 찍혀 있으면 universe 빌드가
        # 실패해 손으로 고른 목록으로 폴백했다는 뜻이다 (조용히 퇴화하는 걸 알아채려고).
        "universe_size": len(watchlist.active_universe()),
        "stocks": [],
    }
    for _, row in table.iterrows():
        sym = row["symbol"]
        c = catalysts[sym]
        snapshot["stocks"].append({
            "symbol": sym,
            "name": row["name"],
            "price": round(float(row["price"]), 2),
            "day_return": float(row["day_return"]),
            "return_5d": float(row["return_5d"]),
            "volume_ratio": float(row["volume_ratio"]),
            "catalyst_tier": c["tier"],
            "catalyst_label": CATALYST_LABEL[c["tier"]],
            "catalyst_event_type": c["event_type"],
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
