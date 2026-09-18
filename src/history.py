"""
history.py — 날짜별 기록을 이어붙여 "어제와 오늘"을 연결한다
=====================================================================
매일 새 목록만 보여주면 추적이 안 된다. "이 종목 3일째 나오네", "어제 계약 뉴스로
올랐던 종목이 오늘은 어떻게 됐지" 같은 게 실제로 쓸 때 필요한 정보다.

문제: 자동화는 저장소에 커밋하지 않고 data/*.json은 gitignore라, 어제 만든 데이터가
로컬에도 저장소에도 남지 않는다. 매일 빈 상태에서 시작한다.

해결: **이미 배포된 사이트에서 어제치를 다시 받아온다.** Netlify는 직전 배포 파일을
계속 서빙하므로, 새로 배포하기 전에 live history.json을 가져와 오늘 것을 덧붙이면
git 없이도 기록이 누적된다.

실행:  python -m src.history            (card_news/data.json을 오늘치로 기록)
"""

from __future__ import annotations
import json
import os
import urllib.request
from datetime import datetime, timezone

from . import config

LIVE_HISTORY_URL = "https://today-watchlist-kr.netlify.app/history.json"
KEEP_DAYS = 30


def _fetch_live_history() -> list[dict]:
    """배포된 사이트에서 기존 기록을 가져온다. 없으면 빈 기록으로 시작."""
    try:
        req = urllib.request.Request(LIVE_HISTORY_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        days = data.get("days", [])
        print(f"   기존 기록 {len(days)}일치를 이어받았습니다.")
        return days
    except Exception as e:
        print(f"   기존 기록을 못 가져왔습니다({e}) — 새로 시작합니다.")
        return []


def _date_key(data: dict) -> str:
    """정렬·중복제거에 쓸 날짜 키(YYYY-MM-DD).

    market_date는 사람이 읽는 값이라 형식이 섞인다("2026년 9월 16일" vs "2026-09-18").
    그걸 키로 쓰면 정렬이 깨지므로, 기계가 만든 generated_at에서 날짜만 뽑아 쓴다.
    """
    gen = data.get("generated_at") or ""
    if len(gen) >= 10 and gen[4] == "-" and gen[7] == "-":
        return gen[:10]
    return str(data.get("market_date") or "")


def _entry_from_cards(data: dict) -> dict:
    """하루치 기록은 추적에 필요한 최소 정보만 남긴다 (파일이 무한정 커지지 않도록)."""
    return {
        "date": _date_key(data),
        "market_date": data.get("market_date"),
        "generated_at": data.get("generated_at"),
        "stocks": [
            {
                "symbol": c["symbol"],
                "name": c.get("name"),
                "price": c.get("price"),
                "day_return": c.get("day_return"),
                "tag": c.get("tag"),
                "hook": c.get("hook"),
            }
            for c in data.get("cards", [])
        ],
    }


def build(data_path: str | None = None) -> dict:
    data_path = data_path or os.path.normpath(
        os.path.join(config.DATA_DIR, "..", "card_news", "data.json"))
    with open(data_path, encoding="utf-8") as f:
        today = json.load(f)

    days = _fetch_live_history()
    entry = _entry_from_cards(today)

    # 같은 날짜가 이미 있으면(하루에 여러 번 실행) 덮어쓴다.
    # 예전 기록에는 date 키가 없을 수 있어 그때는 그 자리에서 만들어 준다.
    for d in days:
        d.setdefault("date", _date_key(d))
    days = [d for d in days if d["date"] != entry["date"]]
    days.append(entry)
    days.sort(key=lambda d: d["date"])
    days = days[-KEEP_DAYS:]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "keep_days": KEEP_DAYS,
        "days": days,
    }


def summarize(history: dict) -> dict[str, dict]:
    """종목별 등장 이력을 요약한다 — 카드에 '3일 연속' 같은 배지를 달기 위한 재료."""
    days = history.get("days", [])
    out: dict[str, dict] = {}

    for idx, day in enumerate(days):
        for s in day.get("stocks", []):
            rec = out.setdefault(s["symbol"], {"appearances": [], "first_seen": None})
            rec["appearances"].append({
                "date": day.get("date") or day.get("market_date"),
                "price": s.get("price"),
                "day_return": s.get("day_return"),
                "tag": s.get("tag"),
                "index": idx,
            })

    last_idx = len(days) - 1
    for sym, rec in out.items():
        apps = rec["appearances"]
        rec["first_seen"] = apps[0]["date"]
        rec["count"] = len(apps)
        rec["count_7d"] = sum(1 for a in apps if a["index"] > last_idx - 7)

        # 오늘부터 거꾸로 연속 등장한 일수
        streak = 0
        idxs = {a["index"] for a in apps}
        i = last_idx
        while i in idxs:
            streak += 1
            i -= 1
        rec["streak"] = streak

        # 처음 등장했을 때 가격 대비 지금 가격 (등장 이후 흐름 — 예측이 아니라 사실 기록)
        first_price, last_price = apps[0].get("price"), apps[-1].get("price")
        rec["change_since_first"] = (
            round(last_price / first_price - 1, 4)
            if first_price and last_price else None)
        rec.pop("appearances")
    return out


def main():
    history = build()
    # 통계를 파일에 같이 담아둔다 — 프론트엔드가 같은 계산을 다시 하지 않도록.
    stats = summarize(history)
    history["stats"] = stats

    dist = os.path.normpath(os.path.join(config.DATA_DIR, "..", "card_news", "dist"))
    os.makedirs(dist, exist_ok=True)
    path = os.path.join(dist, "history.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"저장 완료: {path} ({len(history['days'])}일치)")

    today_symbols = [s["symbol"] for s in history["days"][-1]["stocks"]]
    repeats = [(s, stats[s]) for s in today_symbols if stats[s]["count"] > 1]
    if repeats:
        print("\n오늘 종목 중 이전에도 나왔던 것:")
        for sym, st in sorted(repeats, key=lambda kv: -kv[1]["streak"]):
            chg = (f"{st['change_since_first']:+.1%}"
                   if st["change_since_first"] is not None else "—")
            print(f"  {sym:6s} {st['streak']}일 연속 · 최근7일 {st['count_7d']}회 · "
                  f"첫 등장 이후 {chg}")
    else:
        print("\n오늘 종목은 모두 처음 등장했습니다 (기록이 쌓이면 연속 등장이 보입니다).")


if __name__ == "__main__":
    main()
