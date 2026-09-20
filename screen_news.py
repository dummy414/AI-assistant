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
                    # 본문은 한국어 해설·요약의 재료다. 길수록 토큰을 쓰지만, 실적 기사처럼
                    # 숫자가 뒤쪽에 나오는 글은 잘리면 해설이 얕아진다 — 품질 우선으로 넉넉히 둔다.
                    "content_text": _strip_html(n.content)[:5000],
                    "image": image,
                    "url": n.url,
                    "source": n.source,
                    "published_at": n.created_at.isoformat(),
                })
    return by_symbol


def fetch_finnhub_news(symbols: list[str], lookback_days: int = 5,
                       limit_per_symbol: int = 8) -> dict:
    """Finnhub로 종목별 뉴스를 보강한다.

    Alpaca는 벤징가 한 곳만 물어와서 종목에 따라 뉴스가 0건인 날이 있다. Finnhub는
    Yahoo·SeekingAlpha·ChartMill 등 여러 매체를 모아줘서 그 구멍을 메운다.
    대신 본문 전문(content_text)은 주지 않으므로, 한국어 상세 요약은 Alpaca 기사로 쓰고
    Finnhub 기사는 '무슨 일이 있었나'(촉매 판단)와 커버리지 보강에 쓴다.
    """
    import urllib.request
    import urllib.parse

    if not config.FINNHUB_API_KEY:
        return {s: [] for s in symbols}

    today = datetime.now(timezone.utc).date()
    frm = (today - timedelta(days=lookback_days)).isoformat()
    out = {}
    for sym in symbols:
        params = {"symbol": sym, "from": frm, "to": today.isoformat(),
                  "token": config.FINNHUB_API_KEY}
        url = "https://finnhub.io/api/v1/company-news?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                articles = json.loads(r.read())
        except Exception as e:
            print(f"   ! Finnhub {sym} 실패: {e}")
            out[sym] = []
            continue
        items = []
        for a in (articles if isinstance(articles, list) else [])[:limit_per_symbol]:
            published = datetime.fromtimestamp(a.get("datetime", 0), timezone.utc).isoformat()
            items.append({
                "headline": a.get("headline", ""),
                "summary": a.get("summary", ""),
                "content_text": "",          # Finnhub는 본문을 주지 않는다
                "image": a.get("image") or None,
                "url": a.get("url"),
                "source": a.get("source"),
                "published_at": published,
            })
        out[sym] = items
    return out


def fetch_earnings_dates(symbols: list[str], ahead_days: int = 90) -> dict[str, str]:
    """다음 실적 발표 예정일을 가져온다 (Finnhub 무료).

    "다음에 뭘 지켜봐야 하나"는 실제로 쓸 때 가장 자주 궁금한 정보인데, 지금까지는
    카드에 그런 게 전혀 없었다. 예정일은 사실이고 예측이 아니므로 넣어도 된다.
    """
    import urllib.request
    import urllib.parse

    if not config.FINNHUB_API_KEY:
        return {}

    today = datetime.now(timezone.utc).date()
    params = {"from": today.isoformat(),
              "to": (today + timedelta(days=ahead_days)).isoformat(),
              "token": config.FINNHUB_API_KEY}
    url = "https://finnhub.io/api/v1/calendar/earnings?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"   ! 실적 일정 조회 실패: {e}")
        return {}

    wanted = set(symbols)
    out: dict[str, str] = {}
    for item in data.get("earningsCalendar", []):
        sym = item.get("symbol")
        date = item.get("date")
        if sym in wanted and date and (sym not in out or date < out[sym]):
            out[sym] = date
    return out


def _norm_headline(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (h or "").lower()).strip()


def merge_news(primary: dict, extra: dict, limit_per_symbol: int = 6) -> dict:
    """Alpaca(본문 있음)를 앞에 두고 Finnhub 기사를 뒤에 붙인다. 제목이 같으면 버린다."""
    merged = {}
    for sym in set(primary) | set(extra):
        items = list(primary.get(sym, []))
        seen = {_norm_headline(n.get("headline", "")) for n in items}
        for n in extra.get(sym, []):
            key = _norm_headline(n.get("headline", ""))
            if key and key not in seen:
                seen.add(key)
                items.append(n)
        merged[sym] = items[:limit_per_symbol]
    return merged


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


LIVE_DATA_URL = "https://today-watchlist-kr.netlify.app/data.json"
EXIT_NO_NEW_SESSION = 2      # 새 거래일이 없어서 할 일이 없다는 뜻 (오류가 아니다)


def deployed_market_date() -> str | None:
    """지금 사이트에 올라가 있는 데이터가 어느 거래일 것인지."""
    import urllib.request
    try:
        req = urllib.request.Request(LIVE_DATA_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read()).get("market_date")
    except Exception as e:
        print(f"   (배포된 data.json을 못 읽었습니다: {type(e).__name__} — 비교를 건너뜁니다)")
        return None


def main():
    os.makedirs(config.DATA_DIR, exist_ok=True)

    # ---- 새 거래일이 있는지 먼저 확인 ----
    # 자동화는 매일 아침 돌지만 시장은 주말·공휴일에 쉰다. 토요일·일요일 아침에
    # 돌면 금요일과 똑같은 봉을 보고 똑같은 종목을 뽑는다. 실제로 어제와 오늘
    # 선정이 16개 중 14개가 겹쳤고, 등락률은 소수점까지 같았다.
    # 같은 걸 다시 만드느라 세션 예산을 쓰지 말고 여기서 끝낸다.
    market_date = watchlist.last_bar_date()
    print(f"마지막 거래일: {market_date or '확인 실패'}")
    if market_date:
        already = deployed_market_date()
        if already == market_date:
            print(f"\n이미 {market_date} 데이터가 올라가 있습니다 — 새 거래일이 없습니다(휴장일).")
            print("갱신할 것이 없으므로 여기서 종료합니다.")
            raise SystemExit(EXIT_NO_NEW_SESSION)
        if already:
            print(f"   (사이트에 올라간 것: {already} → 새로 만듭니다)")

    print("=" * 60)
    print("1) 유니버스 스크리닝 (유동성 높은 종목 중 오늘 변동/거래량 상위)")
    print("=" * 60)
    # 유니버스가 1,000종목으로 넓어지면 무명 종목이 상위에 올 수 있다. 넉넉히 뽑아서
    # 뉴스가 있는 종목을 우선 채우고, 모자라면 점수 순으로 마저 채운다
    # (뉴스 없는 카드가 여러 장이면 읽을 게 없어지므로).
    SHORTLIST = 36
    FINAL = 16
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
    alpaca_news = fetch_news_batch(shortlist_symbols)
    print(f"   Alpaca: {sum(len(v) for v in alpaca_news.values())}건 "
          f"(뉴스 없는 종목 {sum(1 for v in alpaca_news.values() if not v)}개)")

    finnhub_news = fetch_finnhub_news(shortlist_symbols)
    print(f"   Finnhub: {sum(len(v) for v in finnhub_news.values())}건 "
          f"(뉴스 없는 종목 {sum(1 for v in finnhub_news.values() if not v)}개)")

    news_map = merge_news(alpaca_news, finnhub_news)
    print(f"   병합 후: {sum(len(v) for v in news_map.values())}건 "
          f"(뉴스 없는 종목 {sum(1 for v in news_map.values() if not v)}개)")

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

    print("\n" + "=" * 60)
    print("5) 카드 맥락 — 자기 과거 대비 / 유니버스 내 순위 / 다음 실적일")
    print("=" * 60)
    stats_map = watchlist.stock_stats(symbols)
    earnings_map = fetch_earnings_dates(symbols)

    # 오늘 유니버스 전체에서 이 종목의 등락이 어느 위치인지 (상위 몇 %)
    all_returns = shortlist["day_return"].abs().tolist()
    universe_n = len(shortlist)

    for s in symbols[:6]:
        st = stats_map.get(s, {})
        ed = earnings_map.get(s)
        print(f"   {s:6s} 평소 대비 {st.get('move_vs_normal', '—')}배 · "
              f"52주 위치 {st.get('pct_in_52w_range', '—')} · "
              f"실적일 {ed or '미정'}")

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # 데이터에서 읽은 실제 거래일. 실행 날짜와 다를 수 있으므로(주말·공휴일)
        # 카드에 쓸 날짜는 반드시 이 값을 써야 한다.
        "market_date": market_date,
        "spy_day_return": spy_day_return,
        # 유니버스 크기를 기록해둔다. 어느 날 갑자기 65로 찍혀 있으면 universe 빌드가
        # 실패해 손으로 고른 목록으로 폴백했다는 뜻이다 (조용히 퇴화하는 걸 알아채려고).
        "universe_size": len(watchlist.active_universe()),
        "stocks": [],
    }
    for _, row in table.iterrows():
        sym = row["symbol"]
        c = catalysts[sym]
        day_ret = float(row["day_return"])
        rank = sum(1 for r in all_returns if r > abs(day_ret)) + 1
        snapshot["stocks"].append({
            "symbol": sym,
            "name": row["name"],
            "price": round(float(row["price"]), 2),
            "day_return": day_ret,
            "return_5d": float(row["return_5d"]),
            "volume_ratio": float(row["volume_ratio"]),
            "catalyst_tier": c["tier"],
            "catalyst_label": CATALYST_LABEL[c["tier"]],
            "catalyst_event_type": c["event_type"],
            "stats": stats_map.get(sym, {}),
            "next_earnings": earnings_map.get(sym),
            "movement_rank": {"rank": rank, "of": universe_n},
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
