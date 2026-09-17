"""
radar_stocks.py — "레이더 포착" 탭: 평소와 다르게 움직이는 종목 탐지
=========================================================================
"오를지 내릴지"를 예측하지 않는다. 대신 각 종목을 그 종목 자신의 과거
패턴과 비교해서, 오늘의 움직임이 통계적으로 이례적인지만 판단한다
(watchlist.screen_anomalies 참고: 등락률 z-score, 거래량 z-score,
연속 streak, 52주 고점/저점 근접).

이 스크립트는 quality_stocks.py와 같은 성격이다 — LLM이 글을 새로 쓰지
않고, 어떤 조건에 걸렸는지에 따라 문장을 코드로 조립한다. 그래서 매일
자동 실행해도 비용이 거의 들지 않는다.

실행:  python -m src.radar_stocks
"""

from __future__ import annotations
import os
import json
from datetime import datetime, timezone

from . import config, watchlist


def main():
    os.makedirs(config.DATA_DIR, exist_ok=True)

    print("=" * 60)
    print("1) 유니버스 전체에서 이례적 움직임 탐지 (레이더 포착)")
    print("=" * 60)
    anomalies = watchlist.screen_anomalies(top_n=8)
    for a in anomalies:
        print(f"   {a['symbol']} ({a['name']}) score={a['anomaly_score']} — "
              + " / ".join(r["text"] for r in a["reasons"]))

    if not anomalies:
        print("   오늘은 이례적인 움직임을 보인 종목이 없습니다.")
        items = []
    else:
        print("\n" + "=" * 60)
        print("2) 뉴스 수집 (Alpaca News API)")
        print("=" * 60)
        from screen_news import fetch_news_batch  # 루트의 수집 함수 재사용

        symbols = [a["symbol"] for a in anomalies]
        news_map = fetch_news_batch(symbols, limit_per_symbol=3)
        for s in symbols:
            print(f"   {s}: {len(news_map.get(s, []))}건")

        items = []
        for a in anomalies:
            items.append({**a, "news": news_map.get(a["symbol"], [])})

    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "items": items}
    path = os.path.join(config.DATA_DIR, "..", "card_news", "radar.json")
    path = os.path.normpath(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n저장 완료: {path} ({len(items)}개 종목)")


if __name__ == "__main__":
    main()
