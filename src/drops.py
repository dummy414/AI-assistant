"""
drops.py — "많이 떨어진 종목" 탭: 이례적으로 떨어진 종목과, 기사들이 말하는 이유
=========================================================================
'오늘의 관심종목'은 뉴스를 먼저 읽고 화제가 된 종목을 고른다. 이 화면은 반대다.
**먼저 가격이 떨어진 것을 통계로 고르고**, 그다음에 "왜 그랬는지" 기사를 찾아 붙인다.
그래서 기사가 안 났지만 크게 떨어진 종목도 여기에는 올라온다 — 그런 경우
"이유를 설명하는 기사를 찾지 못했습니다"라고 그대로 적는다. 그것도 사실이다.

**뉴스는 두 곳에서 모은다.** Alpaca는 벤징가 한 곳만 물어와서 종목에 따라 0건인
날이 있다. 실제로 가장 크게 떨어진 ALL(-5.5%)이 레이더에서 뉴스 0건이었다.
그래서 Finnhub로 한 번 더 긁어 합친다.

**이 화면은 "싸졌으니 사라"가 아니다.** 많이 떨어진 데는 이유가 있고, 그 이유가
사라졌는지 아닌지는 이 사이트가 알 수 없다. 얼마나·며칠째·평소 대비 몇 배인지와
기사가 뭐라고 했는지까지가 전부다.

실행:  python -m src.drops
"""

from __future__ import annotations
import os
import json
from datetime import datetime, timezone

from . import config, watchlist

TOP_N = 10
NEWS_PER_SYMBOL = 4


def _merge_news(alpaca: list[dict], finnhub: list[dict]) -> list[dict]:
    """두 출처를 합치되 같은 기사는 한 번만. 제목이 같으면 같은 기사로 본다."""
    out, seen = [], set()
    for item in list(alpaca) + list(finnhub):
        head = (item.get("headline") or "").strip()
        key = head.lower()[:90]
        if not head or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:NEWS_PER_SYMBOL]


def main():
    os.makedirs(config.DATA_DIR, exist_ok=True)

    print("=" * 60)
    print("1) 유니버스 전체에서 이례적으로 떨어진 종목 찾기")
    print("=" * 60)
    drops = watchlist.screen_drops(top_n=TOP_N)
    for d in drops:
        print(f"   {d['symbol']:6s} {d['name'][:30]:32s} "
              f"오늘 {d['day_return'] * 100:+.2f}% (평소의 {abs(d['return_z']):.1f}배) · "
              f"5일 {d['return_5d'] * 100:+.1f}% · {d['down_streak']}일 연속 · "
              f"고점 대비 {d['drawdown'] * 100:+.0f}%")

    if not drops:
        print("   오늘은 평소보다 크게 떨어진 종목이 없습니다.")
        items = []
    else:
        print("\n" + "=" * 60)
        print("2) 왜 떨어졌는지 — 기사 수집 (Alpaca + Finnhub)")
        print("=" * 60)
        from screen_news import fetch_news_batch, fetch_finnhub_news

        symbols = [d["symbol"] for d in drops]
        alpaca_map = fetch_news_batch(symbols, limit_per_symbol=NEWS_PER_SYMBOL)
        finnhub_map = fetch_finnhub_news(symbols)

        items = []
        for d in drops:
            sym = d["symbol"]
            news = _merge_news(alpaca_map.get(sym, []), finnhub_map.get(sym, []))
            print(f"   {sym:6s} Alpaca {len(alpaca_map.get(sym, []))}건 + "
                  f"Finnhub {len(finnhub_map.get(sym, []))}건 → 합쳐서 {len(news)}건"
                  + ("  ← 기사를 못 찾았습니다" if not news else ""))
            items.append({
                **d,
                "news": [{
                    "headline": n.get("headline"),
                    "summary": n.get("summary"),
                    "source": n.get("source"),
                    "url": n.get("url"),
                    "published_at": n.get("published_at"),
                    # 아래 두 개는 자동화가 채운다 (기사를 읽고 한국어로)
                    "korean_summary": None,
                } for n in news],
                # 자동화가 채운다: 기사들을 종합해 "왜 떨어졌다고 하는가" 한두 문장.
                # 기사가 없으면 null로 두고, 화면은 "찾지 못했습니다"라고 적는다.
                "why": None,
            })

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    path = os.path.normpath(os.path.join(config.DATA_DIR, "..", "card_news", "drops.json"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n저장 완료: {path} ({len(items)}개 종목)")
    if items:
        n_no_news = sum(1 for i in items if not i["news"])
        print(f"기사를 찾지 못한 종목: {n_no_news}개")
        print("\n다음: 각 종목의 why와 기사별 korean_summary를 채우세요.")
        print("      기사에 적힌 내용만 쓰고, 앞으로 오를지 내릴지는 쓰지 마세요.")


if __name__ == "__main__":
    main()
