"""
replay.py — "이 종목, 예전에 이렇게 움직였을 때 무슨 일이 있었고 그 뒤 어떻게 됐나"
=====================================================================
초보에게 가장 없는 건 **기억**이다. 오늘 어떤 종목이 +18% 올랐다고 하면, 경험 있는
사람은 "얘 작년 실적 때도 이랬는데 그 뒤 도로 빠졌지" 같은 참조점을 갖고 본다.
그 참조점을 데이터로 만들어 카드에 붙이는 게 이 모듈이다.

**뉴스 분류로 과거 사건을 찾지 않는다.** 실제로 해봤더니 정규식이 과거 기사에서
"자금 조달"을 279건 중 62건이나 잡아냈는데 대부분 오탐이었다 (events.py가 같은
이유로 LLM 검증 단계를 따로 두고 있다). 대신 **주가 자체**를 기준으로 삼는다:

  그날 등락 ÷ 직전 60거래일 표준편차 ≥ 2.5  →  "평소와 다르게 움직인 날"

이건 계산이라 틀릴 여지가 없다. 그리고 그날의 뉴스 헤드라인을 **분류하지 않고
원문 그대로** 붙인다 — 무슨 일이었는지는 제목을 읽으면 사람이 안다.

표준편차는 **그날을 제외한 직전 60일**로 계산한다. 그날을 포함하면 큰 움직임이
자기 기준을 부풀려서 사건을 놓친다 (그리고 미래 정보를 쓰는 셈이 된다).

### 이건 예측이 아니다
과거 5번 중 3번 올랐다는 건 다음에 오른다는 뜻이 아니다. 표본이 5개면 동전
던지기와 구분되지 않는다. 그래서 이 모듈은:
  - 표본이 3개 미만이면 요약 통계를 아예 만들지 않는다 (`summary: null`)
  - 승률과 함께 **최저/최고**를 항상 같이 낸다 — 평균만 보면 편차가 숨는다
  - 어느 쪽으로 움직일지 말하지 않는다. 기록을 보여줄 뿐이다

실행:  python -m src.replay
"""

from __future__ import annotations
import json
import os
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import numpy as np

from . import config, watchlist

LOOKBACK_DAYS = 760        # 약 2년치 (거래일 기준 520봉 안팎)
VOL_WINDOW = 60            # 평소 변동폭을 재는 창
SIGMA_THRESHOLD = 2.5      # 평소의 몇 배부터 "사건"으로 볼지
MIN_ABS_MOVE = 0.03        # 저변동 종목에서 1~2% 움직임이 '사건'이 되는 걸 막는 하한
COOLDOWN_DAYS = 5          # 같은 사건의 연속일을 한 건으로 묶는다
MAX_EVENTS = 6             # 카드에 넣을 최대 건수 (최근 순)
MIN_SAMPLE = 3             # 이 미만이면 요약 통계를 내지 않는다
HORIZONS = [1, 5, 20]      # 사건 후 1 / 5 / 20거래일
NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
# 1순위는 이 종목이 주인공인 기사. 하지만 채굴주·반도체처럼 **업종이 통째로 움직이는**
# 종목은 개별 뉴스가 아예 없고 "Crypto-Related Stocks Surge" 같은 기사만 있다.
# 그게 진짜 설명이므로, 개별 기사가 없을 때만 2순위로 받아서 '업종 전체'라고 표시한다.
MAX_SYMBOLS_PER_ARTICLE = 3
MAX_SYMBOLS_SECTOR = 10
THROTTLE = 0.2

# 헤드라인 고르기 — 같은 날 기사가 20건씩 쏟아지므로 "무슨 일이었나"를 가장 잘
# 말해주는 한 건을 골라야 한다. 아래 목록은 실제 과거 기사를 눈으로 보고 만들었다.
_JUNK = re.compile(
    r"(\$[\d,]+ invested in|earnings preview|^expert outlook|through the eyes of|"
    r"most accurate analysts|market-moving news|complete transcript|earnings call|"
    r"what'?s going on with|here'?s how much|unusual options|options activity|"
    r"stocks? (?:moving|to watch)|(?:premarket|after[- ]hours?) (?:movers|trading)|"
    r"top \d+ .*(?:gainers|losers)|are on the rise|why .* stock is|"
    r"biggest movers|52-week (?:high|low)|"
    # 기술적 분석·시황 잡글: 사건을 설명하지 않고 주가 움직임만 되풀이한다
    r"stock of the day|(?:resistance|support) level|epic reversal|"
    r"shares? (?:are|is) (?:charging|climbing|surging|sliding|falling|trading|soaring)|"
    r"charging higher|key .{0,20}level|chart|technical|"
    r"whale alert|stock market today|intraday session)", re.I)
# "Maintains Buy / Reiterates Outperform"은 아무것도 바뀌지 않았다는 뜻이라 그날의
# 급등락을 설명하지 못한다. 반면 Upgrades/Downgrades는 실제로 바뀐 사건이다.
_NO_CHANGE = re.compile(r"\b(maintains?|reiterates?)\b", re.I)
_STRONG = re.compile(
    r"(\bq[1-4]\b.{0,40}\b(?:eps|sales|revenue)\b|beats?\b.{0,30}estimate|"
    r"miss(?:es|ed)?\b.{0,30}estimate|\bguidance\b|\boutlook\b|"
    r"\bawarded?\b|\bwins?\b|\bwon\b|\bselected\b|\bcontract\b|\border\b|"
    r"\bacquir\w+|\bacquisition\b|\bmerger\b|\bto buy\b|\bstake\b|"
    r"\bfda\b|\bapproval\b|\bclearance\b|\btrial\b|"
    r"\bpartnership\b|\bcollaborat\w+|\binvest\w+ in\b|"
    r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|\blayoffs?\b|\brecall\w*)", re.I)


# --------------------------------------------------------------------------
# 사건 찾기
# --------------------------------------------------------------------------
def find_events(closes: np.ndarray, dates: list[str]) -> tuple[list[dict], dict | None]:
    """평소 대비 이례적으로 움직인 날을 찾는다. (사건 목록, 마지막 봉 정보)"""
    n = len(closes)
    if n < VOL_WINDOW + 30:
        return [], None

    rets = np.concatenate([[np.nan], closes[1:] / closes[:-1] - 1])

    # 그날 직전 VOL_WINDOW일의 표준편차 — 그날은 넣지 않는다
    ratio = np.full(n, np.nan)
    for i in range(VOL_WINDOW + 1, n):
        sd = float(np.nanstd(rets[i - VOL_WINDOW:i]))
        if sd > 0:
            ratio[i] = abs(rets[i]) / sd

    last = n - 1
    # 1배 미만이면 소수 첫째 자리로 반올림했을 때 "0배"가 되어 고장난 것처럼 보인다
    last_ratio = None if np.isnan(ratio[last]) else float(ratio[last])
    today = {
        "date": dates[last],
        "return": round(float(rets[last]), 4),
        "move_vs_normal": (None if last_ratio is None
                           else round(last_ratio, 1 if last_ratio >= 1 else 2)),
        "direction": "up" if rets[last] > 0 else "down",
    }

    # 오늘(마지막 봉)은 사건 목록에 넣지 않는다 — 카드 본문이 이미 오늘 얘기다
    cands = [i for i in range(VOL_WINDOW + 1, last)
             if not np.isnan(ratio[i])
             and ratio[i] >= SIGMA_THRESHOLD
             and abs(rets[i]) >= MIN_ABS_MOVE]

    # 큰 것부터 집어가며 앞뒤 COOLDOWN_DAYS 안의 날은 버린다 (같은 사건의 여진)
    kept: list[int] = []
    for i in sorted(cands, key=lambda k: -ratio[k]):
        if all(abs(i - j) > COOLDOWN_DAYS for j in kept):
            kept.append(i)

    events = []
    for i in sorted(kept, reverse=True):
        forward = {}
        for h in HORIZONS:
            j = i + h
            forward[str(h)] = (round(float(closes[j] / closes[i] - 1), 4)
                               if j < n and closes[i] else None)
        events.append({
            "date": dates[i],
            "return": round(float(rets[i]), 4),
            "move_vs_normal": round(float(ratio[i]), 1),
            "direction": "up" if rets[i] > 0 else "down",
            "forward": forward,
        })
    return events, today


def summarize(events: list[dict], horizon: int) -> dict | None:
    """사건 후 N거래일 수익률 분포. 표본이 너무 적으면 만들지 않는다."""
    vals = [e["forward"][str(horizon)] for e in events
            if e["forward"].get(str(horizon)) is not None]
    if len(vals) < MIN_SAMPLE:
        return None
    return {
        "n": len(vals),
        "up": sum(1 for v in vals if v > 0),
        "down": sum(1 for v in vals if v <= 0),
        "mean": round(statistics.fmean(vals), 4),
        "median": round(statistics.median(vals), 4),
        "best": round(max(vals), 4),
        "worst": round(min(vals), 4),
    }


# --------------------------------------------------------------------------
# 그날의 헤드라인 붙이기
# --------------------------------------------------------------------------
def _score_headline(item: dict, event_day: str) -> float:
    """어느 기사가 '그날 무슨 일이었나'를 가장 잘 말해주는지 점수."""
    head = item.get("headline") or ""
    score = 0.0
    if _STRONG.search(head):
        score += 2
    if _JUNK.search(head):
        score -= 3
    if _NO_CHANGE.search(head):
        score -= 2
    if head.rstrip().endswith("?"):
        score -= 1.5         # 물음표로 끝나는 제목은 대개 사실이 아니라 추측이다
    created = (item.get("created_at") or "")[:19]
    day = created[:10]
    if day == event_day:
        score += 1
    elif created[11:13].isdigit() and int(created[11:13]) >= 20:
        score += 1           # 전날 장 마감 후 발표 → 다음 날 주가가 반응한다
    n_syms = len(item.get("symbols") or [])
    if n_syms == 1:
        score += 0.5         # 이 종목만 다룬 기사일수록 주인공일 가능성이 높다
    return score


def headline_for(symbol: str, event_day: str) -> dict | None:
    """사건일(및 전날 장 마감 후)의 뉴스 중 가장 설명력 있는 한 건."""
    d = date.fromisoformat(event_day)
    q = {"symbols": symbol,
         "start": (d - timedelta(days=1)).isoformat() + "T12:00:00Z",
         "end": (d + timedelta(days=1)).isoformat() + "T00:00:00Z",
         "limit": 30, "sort": "asc", "include_content": "false"}
    headers = {"APCA-API-KEY-ID": config.ALPACA_API_KEY,
               "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY}
    req = urllib.request.Request(NEWS_URL + "?" + urllib.parse.urlencode(q), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            items = json.loads(r.read()).get("news", [])
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"   ! {symbol} {event_day} 뉴스 조회 실패: {e}")
        return None

    def pick(cap: int):
        pool = [n for n in items if 0 < len(n.get("symbols") or []) <= cap]
        if not pool:
            return None
        # 점수가 같으면 먼저 나온 기사 — 최초 보도가 대개 사건 그 자체다
        best = min(pool, key=lambda n: (-_score_headline(n, event_day),
                                        n.get("created_at") or ""))
        # 쓸 만한 기사가 없으면 억지로 붙이지 않는다 (없다고 말하는 편이 낫다)
        return best if _score_headline(best, event_day) >= 0 else None

    best = pick(MAX_SYMBOLS_PER_ARTICLE)
    sector_wide = False
    if best is None:
        best = pick(MAX_SYMBOLS_SECTOR)
        sector_wide = best is not None
    if best is None:
        return None
    return {
        "headline": best.get("headline"),
        "url": best.get("url"),
        "source": best.get("source"),
        "published_at": best.get("created_at"),
        "articles_that_day": len(items),
        # True면 이 종목만의 소식이 아니라 업종이 함께 움직인 날이라는 뜻
        "sector_wide": sector_wide,
    }


# --------------------------------------------------------------------------
def build(symbols: list[str], with_news: bool = True) -> dict:
    bars = watchlist._fetch_batch_bars(symbols, lookback_days=LOOKBACK_DAYS)
    if bars.empty:
        return {"generated_at": datetime.now(timezone.utc).isoformat(),
                "ready": False, "reason": "시세를 불러오지 못했습니다.", "symbols": {}}
    available = set(bars.index.get_level_values(0))

    out: dict[str, dict] = {}
    for sym in symbols:
        if sym not in available:
            continue
        df = bars.loc[sym].sort_index()
        closes = df["close"].to_numpy(dtype=float)
        dates = [str(d)[:10] for d in df.index]

        events, today = find_events(closes, dates)
        if today is None:
            print(f"   {sym:6s} 데이터 부족 ({len(closes)}봉) — 건너뜀")
            continue

        shown = events[:MAX_EVENTS]
        if with_news:
            for ev in shown:
                time.sleep(THROTTLE)
                ev["news"] = headline_for(sym, ev["date"])

        # 오늘과 같은 방향이었던 사건만 따로 — 이게 가장 궁금한 숫자다
        same_dir = [e for e in events if e["direction"] == today["direction"]]

        out[sym] = {
            "bars": len(closes),
            "period": {"from": dates[0], "to": dates[-1]},
            "threshold_sigma": SIGMA_THRESHOLD,
            "today": today,
            # 오늘 자체가 이례적인 날일 때만 "오늘처럼"이라는 표현을 쓸 수 있다
            "today_is_unusual": bool(today["move_vs_normal"]
                                     and today["move_vs_normal"] >= 2.0),
            "events_found": len(events),
            "events": shown,
            "summary": {str(h): summarize(events, h) for h in HORIZONS},
            "summary_same_direction": {str(h): summarize(same_dir, h) for h in HORIZONS},
            "same_direction_n": len(same_dir),
        }
        with_news_n = sum(1 for e in shown if e.get("news"))
        print(f"   {sym:6s} {len(closes)}봉 · 사건 {len(events):2d}건 "
              f"(표시 {len(shown)}, 헤드라인 {with_news_n}) · "
              f"오늘 {today['return']:+.2%} = 평소의 {today['move_vs_normal']}배")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ready": bool(out),
        "lookback_days": LOOKBACK_DAYS,
        "vol_window": VOL_WINDOW,
        "threshold_sigma": SIGMA_THRESHOLD,
        "min_abs_move": MIN_ABS_MOVE,
        "horizons": HORIZONS,
        "min_sample": MIN_SAMPLE,
        "method": ("그날 등락률을 직전 60거래일 표준편차로 나눈 값이 2.5배 이상이고 "
                   "절대 등락이 3% 이상인 날을 '평소와 다르게 움직인 날'로 본다. "
                   "뉴스 헤드라인은 분류하지 않고 그날 기사 중 한 건을 원문 그대로 붙인다."),
        "disclaimer": ("과거 기록이며 예측이 아닙니다. 표본이 몇 건뿐이라 "
                       "다음에도 같으리라는 근거가 되지 못합니다."),
        "symbols": out,
    }


def _load_symbols() -> tuple[list[str], str]:
    """오늘 카드에 올라간 종목을 찾는다.

    파이프라인 안에서는 방금 만든 watchlist_snapshot.json이 정답이지만, 나중에
    이 모듈만 따로 돌릴 때는 그 스냅샷이 어제 것일 수 있다. 두 파일의
    generated_at을 비교해 **더 최신인 쪽**을 쓴다.
    """
    dist = os.path.normpath(os.path.join(config.DATA_DIR, "..", "card_news", "dist"))
    sources = [
        (os.path.join(config.DATA_DIR, "watchlist_snapshot.json"), "stocks", "스크리닝 스냅샷"),
        (os.path.join(dist, "data.json"), "cards", "배포된 카드"),
    ]
    best = None
    for path, key, label in sources:
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            syms = [s["symbol"] for s in doc.get(key, [])]
            when = datetime.fromisoformat(doc["generated_at"])
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            continue
        if syms and (best is None or when > best[0]):
            best = (when, syms, label)
    if best is None:
        raise SystemExit("종목 목록을 찾지 못했습니다 "
                         "(data/watchlist_snapshot.json 또는 card_news/dist/data.json 필요).")
    return best[1], f"{best[2]} {best[0]:%Y-%m-%d %H:%M UTC}"


def main():
    symbols, source = _load_symbols()
    print(f"종목 출처: {source}")

    print(f"과거 재생 — {len(symbols)}종목, 최근 {LOOKBACK_DAYS}일에서 "
          f"평소의 {SIGMA_THRESHOLD}배 이상 움직인 날 찾기")
    result = build(symbols)

    dist = os.path.normpath(os.path.join(config.DATA_DIR, "..", "card_news", "dist"))
    os.makedirs(dist, exist_ok=True)
    path = os.path.join(dist, "replay.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    total = sum(v["events_found"] for v in result["symbols"].values())
    heads = sum(1 for v in result["symbols"].values() for e in v["events"] if e.get("news"))
    print(f"\n저장 완료: {path}")
    print(f"종목 {len(result['symbols'])}개 · 과거 사건 {total}건 · 헤드라인 확보 {heads}건")


if __name__ == "__main__":
    main()
