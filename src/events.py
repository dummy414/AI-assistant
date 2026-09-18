"""
events.py — "이벤트 규모" 스캐너: 회사 크기 대비 큰 일이 벌어진 종목 찾기
=====================================================================
이 모듈은 "오를 종목"을 찾지 않는다. 찾는 것은 **금액이 명시된 기업 이벤트**이고,
보여주는 것은 **그 금액이 회사 규모 대비 몇 %인가**라는 나눗셈 결과뿐이다.

  예) 연매출 $2억인 회사가 $5억짜리 계약을 수주 → "연매출의 250%"

이건 예측이 아니라 공시된 두 숫자의 비율이다. 그래서 정직하다. 다만 정직하려면
**양방향이어야 한다** — 대형 수주만 잡고 대형 소송·리콜·감액은 안 잡으면 그건
호재 목록이고 사실상 종목 추천이 된다. 그래서 INFLOW(유입)와 OUTFLOW(유출)를
같은 기준으로 잡고, 어느 쪽이 좋은지는 판단하지 않는다.

금액은 반드시 기사에 명시된 숫자만 쓴다. 추정하지 않는다. 금액이 없으면 후보에서 뺀다.

파이프라인:
  1. 유동성 유니버스(src/universe.py, 약 1,000종목) 로드
  2. 최근 뉴스 전량 수집 (하루 약 400건)
  3. 유니버스 종목이 언급되고 + 금액이 명시되고 + 이벤트 키워드가 있는 기사만 추림
  4. 금액 파싱 + FMP로 회사 규모(연매출·시가총액) 조회 → 비율 계산
  5. data/event_candidates.json 저장 (문구 작성과 검증은 LLM 단계에서)

실행:  python -m src.events
"""

from __future__ import annotations
import json
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import config, universe

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
FMP_BASE = "https://financialmodelingprep.com/stable"

LOOKBACK_HOURS = 30       # 전날 장 마감 이후 뉴스를 넉넉히 포함
MIN_RATIO = 0.05          # 연매출 대비 5% 미만이면 "규모가 크다"고 보기 어렵다
MAX_CANDIDATES = 12

# 금액 표기: "$1.2 billion", "$100 million", "$500M", "US$3,000,000"
_MONEY_RE = re.compile(
    r"(?:US)?\$\s?(\d[\d,]*(?:\.\d+)?)\s*(trillion|billion|million|thousand|[TBMK])\b",
    re.I)
_MULT = {"trillion": 1e12, "t": 1e12, "billion": 1e9, "b": 1e9,
         "million": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}

# 이벤트 분류 — inflow(회사로 돈/자산이 들어오는 사건) / outflow(나가는 사건).
# 어느 쪽이 "좋다"고 말하지 않는다. 방향은 규모를 해석하기 위한 라벨일 뿐이다.
EVENT_TYPES = [
    ("계약 수주",   "inflow",  r"\b(contract|awarded?|wins?|won|order|purchase agreement|backlog|task order)\b"),
    ("인수·합병",   "inflow",  r"\b(acquir\w+|acquisition|merger|to buy|takeover)\b"),
    ("자금 조달",   "inflow",  r"\b(rais\w+|offering|private placement|financing|funding round|grant)\b"),
    ("소송·합의",   "outflow", r"\b(lawsuit|sued?|litigation|settle\w*|class action|verdict|damages)\b"),
    ("제재·벌금",   "outflow", r"\b(fine[sd]?|penalt\w+|sanction\w*|violation)\b"),
    ("리콜·결함",   "outflow", r"\b(recall\w*|defect\w*|safety issue)\b"),
    ("손상·상각",   "outflow", r"\b(impair\w+|write[- ]?down|write[- ]?off|charge)\b"),
    ("계약 해지",   "outflow", r"\b(terminat\w+|cancel\w+|lost contract|ends? partnership)\b"),
]
_COMPILED = [(k, d, re.compile(p, re.I)) for k, d, p in EVENT_TYPES]


def _strip_html(html: str) -> str:
    if not html:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def parse_amounts(text: str) -> list[float]:
    """텍스트에서 달러 금액을 모두 뽑아 숫자로 바꾼다."""
    out = []
    for num, unit in _MONEY_RE.findall(text or ""):
        try:
            out.append(float(num.replace(",", "")) * _MULT[unit.lower()])
        except (ValueError, KeyError):
            continue
    return out


def classify(text: str) -> tuple[str, str] | tuple[None, None]:
    """기사에서 이벤트 유형을 찾는다. 여러 개면 먼저 매칭된 것을 쓴다."""
    for label, direction, rx in _COMPILED:
        if rx.search(text or ""):
            return label, direction
    return None, None


def fetch_recent_news(hours: int = LOOKBACK_HOURS) -> list[dict]:
    """최근 뉴스를 본문 포함해 전량 가져온다 (페이지네이션)."""
    headers = {"APCA-API-KEY-ID": config.ALPACA_API_KEY,
               "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY}
    start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    items, token, pages = [], None, 0
    while pages < 60:
        q = {"start": start, "limit": 50, "include_content": "true", "sort": "desc"}
        if token:
            q["page_token"] = token
        req = urllib.request.Request(NEWS_URL + "?" + urllib.parse.urlencode(q), headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        batch = data.get("news", [])
        items.extend(batch)
        token = data.get("next_page_token")
        pages += 1
        if not token or not batch:
            break
    return items


MAX_SYMBOLS_PER_ARTICLE = 3   # 종목 여러 개를 나열하는 시황·라운드업 기사는 제외
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _event_sentences(text: str) -> list[dict]:
    """금액과 이벤트 키워드가 **같은 문장 안에** 있는 경우만 뽑는다.

    기사 전체에서 가장 큰 금액을 집으면 엉뚱한 숫자(회사 시가총액, 경쟁사 규모,
    시장 전체 규모)를 이벤트 금액으로 오인한다. 같은 문장 안에 있어야 그 금액이
    그 사건의 금액일 가능성이 높다 — 그래도 확실하지는 않아서, 최종 판단은
    LLM 검증 단계에 맡긴다.
    """
    hits = []
    for sent in _SENT_SPLIT.split(text or ""):
        if len(sent) > 400:
            continue
        amounts = parse_amounts(sent)
        if not amounts:
            continue
        label, direction = classify(sent)
        if not label:
            continue
        hits.append({
            "sentence": sent.strip(),
            "amounts": sorted(set(amounts), reverse=True),
            "event_type": label,
            "direction": direction,
        })
    return hits


def find_candidates(news: list[dict], liquid: dict[str, dict]) -> list[dict]:
    """유니버스 종목이 주인공이고, 금액과 이벤트가 한 문장에 함께 나오는 기사만 남긴다."""
    seen = set()
    cands = []
    for n in news:
        symbols = [s for s in (n.get("symbols") or []) if s in liquid]
        if not symbols or len(n.get("symbols") or []) > MAX_SYMBOLS_PER_ARTICLE:
            continue
        head = n.get("headline") or ""
        summary = n.get("summary") or ""
        body = _strip_html(n.get("content") or "")[:6000]

        hits = _event_sentences(f"{head}. {summary}") or _event_sentences(body[:3000])
        if not hits:
            continue
        top = hits[0]

        for sym in symbols:
            key = (sym, n.get("url"))
            if key in seen:
                continue
            seen.add(key)
            cands.append({
                "symbol": sym,
                "name": liquid[sym]["name"],
                "price": liquid[sym]["price"],
                "dollar_volume": liquid[sym]["dollar_volume"],
                "event_type": top["event_type"],
                "direction": top["direction"],
                "amount_hint": max(top["amounts"]),
                "evidence_sentence": top["sentence"],
                "other_event_sentences": [h["sentence"] for h in hits[1:4]],
                "headline": head,
                "summary": summary,
                "content_text": body[:4000],
                "url": n.get("url"),
                "source": n.get("source"),
                "published_at": n.get("created_at"),
            })
    return cands


def _fmp(path: str, **params):
    if not config.FMP_API_KEY or config.FMP_API_KEY.startswith("YOUR_"):
        return []
    params["apikey"] = config.FMP_API_KEY
    url = f"{FMP_BASE}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception:
        return []


def fetch_scale(symbols: list[str]) -> dict[str, dict]:
    """회사 규모(연매출·시가총액)를 조회한다.

    **검증을 통과한 종목에만 호출할 것.** FMP 무료 플랜은 일일 호출 한도가 있어서,
    버려질 후보까지 조회하면 금방 429가 난다 (실제로 한 번 겪었다).
    """
    out = {}
    for sym in dict.fromkeys(symbols):
        profile = _fmp("profile", symbol=sym)
        income = _fmp("income-statement", symbol=sym, limit=1)
        out[sym] = {
            "market_cap": (profile[0].get("marketCap") if profile else None),
            "revenue": (income[0].get("revenue") if income else None),
            "industry": (profile[0].get("industry") if profile else None),
            "company": (profile[0].get("companyName") if profile else None),
        }
    return out


def scale_cli(symbols_csv: str):
    """`python -m src.events scale GNRC,IREN,KEY` — 검증 후 규모 데이터만 따로 받는다."""
    symbols = [s.strip().upper() for s in symbols_csv.split(",") if s.strip()]
    scale = fetch_scale(symbols)
    path = os.path.join(config.DATA_DIR, "event_scale.json")
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(), "scale": scale},
                  f, ensure_ascii=False, indent=2)
    for sym, s in scale.items():
        rev = f"${s['revenue']/1e9:.2f}B" if s["revenue"] else "—"
        mc = f"${s['market_cap']/1e9:.2f}B" if s["market_cap"] else "—"
        print(f"  {sym:6s} 연매출 {rev:>10s}  시총 {mc:>10s}  {s['industry'] or ''}")
    print(f"저장 완료: {path}")


def main():
    os.makedirs(config.DATA_DIR, exist_ok=True)

    liquid = universe.load()
    if not liquid:
        raise SystemExit("유니버스가 없습니다. 먼저 `python -m src.universe`를 실행하세요.")
    print(f"유동성 유니버스: {len(liquid)}종목")

    news = fetch_recent_news()
    print(f"최근 {LOOKBACK_HOURS}시간 뉴스: {len(news)}건")

    cands = find_candidates(news, liquid)
    print(f"1차 후보(금액+이벤트가 한 문장에): {len(cands)}건")

    # 회사 규모(FMP)는 여기서 조회하지 않는다 — 검증을 통과한 소수 종목에만
    # `python -m src.events scale SYM1,SYM2`로 조회해야 API 한도를 아낄 수 있다.
    # amount_hint는 정규식이 뽑은 '추정' 금액이라 그대로 믿으면 안 된다.
    # (실측 결과 기사 주제와 무관한 숫자를 자주 집는다.) 정렬·게이팅에 쓰지 않고,
    # LLM 검증 단계에서 기사 본문을 읽고 확정하도록 근거 문장과 함께 넘긴다.
    cands.sort(key=lambda c: -(c.get("dollar_volume") or 0))

    print("\n--- 검증 대기 후보 (금액은 미확정) ---")
    for c in cands[:20]:
        print(f"  {c['symbol']:6s} {c['event_type']:8s} ({c['direction']})  {c['headline'][:80]}")

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe_size": len(liquid),
        "news_scanned": len(news),
        "verified": False,
        "note": ("amount_hint/event_type은 정규식 추정값이며 검증되지 않았습니다. "
                 "LLM이 evidence_sentence와 content_text를 읽고 (1) 이 사건이 이 회사의 "
                 "사건인지 (2) 금액이 사건의 규모인지 확인한 뒤에만 사용하세요. "
                 "확인되지 않으면 버려야 합니다."),
        "candidates": cands,
    }
    path = os.path.join(config.DATA_DIR, "event_candidates.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n저장 완료: {path} ({len(cands)}건, 모두 미검증)")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "scale":
        scale_cli(sys.argv[2])
    else:
        main()
