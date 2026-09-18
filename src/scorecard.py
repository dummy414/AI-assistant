"""
scorecard.py — 이 사이트가 올렸던 종목들이 그 뒤 어떻게 됐는지, 전부 공개한다
=====================================================================
증권 앱도 뉴스레터도 자기가 소개한 종목의 뒷이야기는 보여주지 않는다. 보여주더라도
잘된 것만 고른다. 여기서는 **고르지 않는다** — history.json에 기록된 모든 선정 건을
스크립트가 기계적으로 계산한다. 사람이 고를 여지가 없다.

목적은 자랑이 아니라 **보정(calibration)**이다. 매일 화려한 카드를 보다 보면
"여기 올라온 건 오르겠지"라고 착각하기 쉬운데, 실제 숫자를 보면 그렇지 않다는 걸
알 수 있다. 성적이 나쁘게 나오면 그게 이 사이트가 예측 도구가 아니라는 증거이고,
그 사실을 감추지 않는 것이 이 프로젝트의 원칙이다.

비교 기준으로 같은 기간 SPY(S&P500 ETF) 수익률을 함께 계산한다. 그게 없으면
"절반이 올랐다"는 숫자는 의미가 없다 — 시장이 오른 날은 원래 대부분 오르니까.

주의: 선정 시점 가격은 그날 카드가 쓰일 때의 종가(전 거래일 종가)다. 그래서 기준봉은
'선정 날짜 직전 거래일'로 잡는다.

실행:  python -m src.scorecard
"""

from __future__ import annotations
import json
import os
import statistics
import urllib.request
from datetime import datetime, timedelta, timezone

from . import config, watchlist

LIVE_HISTORY_URL = "https://today-watchlist-kr.netlify.app/history.json"
HORIZONS = [1, 5]          # 선정 후 1거래일 / 5거래일
BENCHMARK = "SPY"


def _load_history() -> dict:
    """로컬 dist 파일을 우선 쓰고, 없으면 배포된 사이트에서 받아온다."""
    local = os.path.normpath(
        os.path.join(config.DATA_DIR, "..", "card_news", "dist", "history.json"))
    try:
        with open(local, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        pass
    try:
        req = urllib.request.Request(LIVE_HISTORY_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"기록을 불러오지 못했습니다: {e}")
        return {}


def _forward_returns(bars, anchor_date: str) -> dict[int, float]:
    """기준일(선정 직전 거래일) 종가 대비 N거래일 뒤 종가의 수익률."""
    dates = [str(d)[:10] for d in bars.index]
    closes = list(bars["close"].values)

    # 선정 날짜보다 앞선 마지막 봉 = 그 카드가 쓸 때 본 종가
    anchor = None
    for i, d in enumerate(dates):
        if d < anchor_date:
            anchor = i
        else:
            break
    if anchor is None:
        return {}

    out = {}
    for h in HORIZONS:
        j = anchor + h
        if j < len(closes) and closes[anchor]:
            out[h] = float(closes[j] / closes[anchor] - 1)
    return out


def build() -> dict:
    history = _load_history()
    days = history.get("days", [])
    if not days:
        return {"generated_at": datetime.now(timezone.utc).isoformat(),
                "ready": False, "reason": "기록이 없습니다."}

    picks = []
    for day in days:
        date = day.get("date") or day.get("market_date")
        for s in day.get("stocks", []):
            picks.append({"symbol": s["symbol"], "date": date,
                          "tier": s.get("catalyst_tier"), "tag": s.get("tag")})

    symbols = sorted({p["symbol"] for p in picks} | {BENCHMARK})
    oldest = min(p["date"] for p in picks)
    span_days = (datetime.now(timezone.utc) - datetime.fromisoformat(oldest).replace(
        tzinfo=timezone.utc)).days
    bars = watchlist._fetch_batch_bars(symbols, lookback_days=max(span_days + 20, 40))
    if bars.empty:
        return {"generated_at": datetime.now(timezone.utc).isoformat(),
                "ready": False, "reason": "시세를 불러오지 못했습니다."}
    available = set(bars.index.get_level_values(0))

    # 같은 기간 시장(SPY) 수익률 — 이게 없으면 승률 숫자는 의미가 없다
    bench = {}
    if BENCHMARK in available:
        spy = bars.loc[BENCHMARK].sort_index()
        for p in picks:
            for h, v in _forward_returns(spy, p["date"]).items():
                bench.setdefault(h, []).append(v)

    results = []
    for p in picks:
        if p["symbol"] not in available:
            continue
        fr = _forward_returns(bars.loc[p["symbol"]].sort_index(), p["date"])
        if not fr:
            continue
        results.append({**p, "forward": {str(k): round(v, 4) for k, v in fr.items()}})

    def summarize(vals: list[float]) -> dict | None:
        if not vals:
            return None
        return {
            "n": len(vals),
            "win_rate": round(sum(1 for v in vals if v > 0) / len(vals), 3),
            "mean": round(statistics.fmean(vals), 4),
            "median": round(statistics.median(vals), 4),
            "best": round(max(vals), 4),
            "worst": round(min(vals), 4),
        }

    overall, by_tier, benchmark = {}, {}, {}
    for h in HORIZONS:
        key = str(h)
        vals = [r["forward"][key] for r in results if key in r["forward"]]
        overall[key] = summarize(vals)
        benchmark[key] = summarize(bench.get(h, []))

        tiers = {}
        for r in results:
            if key not in r["forward"] or r.get("tier") is None:
                continue
            tiers.setdefault(str(r["tier"]), []).append(r["forward"][key])
        by_tier[key] = {t: summarize(v) for t, v in sorted(tiers.items(), reverse=True)}

    # 가장 좋았던 / 나빴던 건 (1거래일 기준)
    ranked = sorted((r for r in results if "1" in r["forward"]),
                    key=lambda r: r["forward"]["1"])
    extremes = {
        "worst": ranked[:3],
        "best": list(reversed(ranked[-3:])),
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ready": bool(results),
        "days_recorded": len(days),
        "picks_total": len(picks),
        "picks_measured": len(results),
        "period": {"from": oldest, "to": days[-1].get("date")},
        "overall": overall,
        "benchmark": benchmark,
        "by_tier": by_tier,
        "extremes": extremes,
    }


def main():
    result = build()
    dist = os.path.normpath(os.path.join(config.DATA_DIR, "..", "card_news", "dist"))
    os.makedirs(dist, exist_ok=True)
    path = os.path.join(dist, "scorecard.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    if not result.get("ready"):
        print(f"아직 채점할 수 없습니다: {result.get('reason', '데이터 부족')}")
        print(f"저장 완료: {path}")
        return

    print(f"기록 {result['days_recorded']}일 · 선정 {result['picks_total']}건 중 "
          f"{result['picks_measured']}건 측정 가능\n")
    for h in HORIZONS:
        o = result["overall"].get(str(h))
        b = result["benchmark"].get(str(h))
        if not o:
            print(f"  선정 후 {h}거래일: 아직 측정 불가 (시간이 더 지나야 함)")
            continue
        line = (f"  선정 후 {h}거래일: {o['n']}건 · 상승 {o['win_rate']:.0%} · "
                f"평균 {o['mean']:+.2%} · 중앙값 {o['median']:+.2%}")
        if b:
            line += f"   (같은 기간 SPY 평균 {b['mean']:+.2%})"
        print(line)
    print(f"\n저장 완료: {path}")


if __name__ == "__main__":
    main()
