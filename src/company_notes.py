"""
company_notes.py — "이 회사가 뭐 하는 곳인지" 설명을 한 번 써두고 계속 재사용한다
=====================================================================
유니버스를 981종목으로 넓히면서 매일 낯선 이름이 올라온다. 카드에 "Vicor ·
Electrical Equipment · +17.66%"라고만 떠 있으면, 주식을 잘 모르는 사람에게는
읽을 수가 없다. 무엇을 파는 회사인지가 빠져 있기 때문이다.

사업 구조는 매일 바뀌지 않으므로 **한 번 써두고 계속 재사용**한다. 첫날은 16종목을
다 써야 하지만, 이미 써둔 종목은 건너뛰므로 시간이 갈수록 새로 쓸 게 줄어든다.
981종목을 미리 채우는 게 아니라 실제로 등장한 종목만 쌓인다.

저장 위치는 history.json과 같은 방식이다 — 저장소에 커밋하지 않으므로
**배포된 사이트에서 기존 파일을 받아와 이어붙인다.**

설명문 자체는 LLM이 쓴다(이 스크립트는 재료만 준비한다). 근거는 Finnhub 업종 정보와
그날 수집된 뉴스로 제한한다 — 모델 기억에 의존하면 중소형주에서 틀리기 쉽기 때문이다.

실행:  python -m src.company_notes
"""

from __future__ import annotations
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import config

LIVE_URL = "https://today-watchlist-kr.netlify.app/company_notes.json"
REWRITE_AFTER_DAYS = 180   # 사업 구조는 느리게 바뀌지만 영원하지는 않다


def _fetch_existing() -> dict:
    try:
        req = urllib.request.Request(LIVE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        notes = data.get("notes", {})
        print(f"   기존 회사 설명 {len(notes)}종목을 이어받았습니다.")
        return notes
    except Exception as e:
        print(f"   기존 설명을 못 가져왔습니다({e}) — 새로 시작합니다.")
        return {}


def fetch_profiles(symbols: list[str]) -> dict[str, dict]:
    """Finnhub 기업 개요 (업종·시가총액·홈페이지). 무료 플랜에서 제공된다."""
    if not config.FINNHUB_API_KEY:
        return {}
    out = {}
    for sym in symbols:
        url = "https://finnhub.io/api/v1/stock/profile2?" + urllib.parse.urlencode(
            {"symbol": sym, "token": config.FINNHUB_API_KEY})
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read())
        except Exception:
            continue
        if not isinstance(d, dict) or not d:
            continue
        out[sym] = {
            "industry": d.get("finnhubIndustry"),
            "market_cap_musd": d.get("marketCapitalization"),
            "weburl": d.get("weburl"),
            "country": d.get("country"),
            "ipo": d.get("ipo"),
        }
    return out


def fetch_descriptions(symbols: list[str]) -> dict[str, str]:
    """FMP의 사업 설명문. **새로 쓸 종목에만** 호출한다.

    Finnhub 무료는 업종·시총만 주고 사업 설명이 없다. 업종만으로 회사를 설명하려 하면
    모델이 기억에 의존하게 되고, 중소형주에서 틀리기 쉽다. FMP description이 가장
    믿을 만한 1차 자료라서 이것만 따로 가져온다. 호출량은 '오늘 처음 등장한 종목' 수라
    보통 하루 몇 건이다 (한도 초과 시에는 조용히 건너뛴다).
    """
    if not config.FMP_API_KEY or config.FMP_API_KEY.startswith("YOUR_"):
        return {}
    out = {}
    for sym in symbols:
        url = "https://financialmodelingprep.com/stable/profile?" + urllib.parse.urlencode(
            {"symbol": sym, "apikey": config.FMP_API_KEY})
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read())
        except Exception as e:
            print(f"   ! {sym} 사업설명 조회 실패: {e}")
            continue
        if isinstance(d, list) and d and d[0].get("description"):
            out[sym] = d[0]["description"][:1500]
    return out


def _needs_primer(note: dict | None) -> bool:
    if not note or not note.get("primer"):
        return True
    written = note.get("written_at") or ""
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(written).replace(tzinfo=timezone.utc)
        return age > timedelta(days=REWRITE_AFTER_DAYS)
    except ValueError:
        return True


def build(snapshot_path: str | None = None) -> dict:
    snapshot_path = snapshot_path or os.path.join(config.DATA_DIR, "watchlist_snapshot.json")
    with open(snapshot_path, encoding="utf-8") as f:
        snap = json.load(f)

    stocks = snap.get("stocks", [])
    symbols = [s["symbol"] for s in stocks]
    notes = _fetch_existing()
    profiles = fetch_profiles(symbols)

    todo = []
    for s in stocks:
        sym = s["symbol"]
        note = notes.get(sym, {})
        note.setdefault("symbol", sym)
        note["name"] = s.get("name") or note.get("name") or sym
        prof = profiles.get(sym, {})
        if prof:
            note["industry"] = prof.get("industry")
            note["market_cap_musd"] = prof.get("market_cap_musd")
            note["weburl"] = prof.get("weburl")

        if _needs_primer(note):
            note["primer"] = None
            # LLM이 근거로 쓸 재료만 넣어준다 (모델 기억이 아니라 이 재료로만 쓰게 하려고)
            note["_material"] = {
                "industry": prof.get("industry"),
                "market_cap_musd": prof.get("market_cap_musd"),
                "weburl": prof.get("weburl"),
                "headlines": [n.get("headline") for n in s.get("news", [])][:6],
                "summaries": [(n.get("summary") or "")[:300] for n in s.get("news", [])][:4],
            }
            todo.append(sym)
        else:
            note.pop("_material", None)
        notes[sym] = note

    # 사업 설명문은 새로 쓸 종목에만 조회한다 (API 한도 절약)
    if todo:
        descriptions = fetch_descriptions(todo)
        for sym, desc in descriptions.items():
            notes[sym]["_material"]["business_description"] = desc
        missing = [s for s in todo if s not in descriptions]
        if missing:
            print(f"   사업설명을 못 받은 종목: {', '.join(missing)} "
                  f"(업종·뉴스만으로 써야 하며, 부족하면 primer를 비워둘 것)")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rewrite_after_days": REWRITE_AFTER_DAYS,
        "todo": todo,
        "notes": notes,
    }


def main():
    result = build()
    dist = os.path.normpath(os.path.join(config.DATA_DIR, "..", "card_news", "dist"))
    os.makedirs(dist, exist_ok=True)
    path = os.path.join(dist, "company_notes.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    total = len(result["notes"])
    todo = result["todo"]
    print(f"\n저장 완료: {path}")
    print(f"  보유 설명: {total - len(todo)}종목 / 새로 써야 할 종목: {len(todo)}개")
    if todo:
        print(f"  작성 대상: {', '.join(todo)}")
        print("  → 각 종목의 _material만 근거로 primer(3~4문장)를 채우고 _material은 지우세요.")
    else:
        print("  모든 종목의 설명이 이미 있습니다 (새로 쓸 것 없음).")


if __name__ == "__main__":
    main()
