"""
quality_stocks.py — 우량주 탭: 실제 재무데이터 기반 대형 우량주 스냅샷
=========================================================================
"오늘 화제인 종목"(watchlist.py)과는 성격이 다르다. 이건 예측이 아니라
이미 검증된 대형 우량기업 고정 리스트에 대해, 실제 재무지표(매출성장률·
순이익률·부채비율·PER)를 Financial Modeling Prep API로 가져와 보여준다.

"이 회사가 계속 오를 것이다"를 주장하지 않는다 — "이 회사가 지금 재무적으로
어떤 상태인지"만 사실대로 보여준다. note 필드는 사업 구조에 대한 정성적
설명이라 자주 바뀌지 않으므로 이 파일에 고정으로 적어둔다.

실행:  python -m src.quality_stocks
"""

from __future__ import annotations
import os
import json
import urllib.request
from datetime import datetime, timezone

from . import config

FMP_BASE = "https://financialmodelingprep.com/stable"

# ---- 고정 우량주 15종목 (섹터 분산) + 정성적 설명(한 번만 작성, 자주 안 바뀜) ----
COMPANIES = {
    "AAPL": "아이폰·맥·서비스 사업으로 세계 최대 시가총액을 유지하는 기술기업. 높은 브랜드 충성도와 서비스 매출 비중 확대가 특징입니다.",
    "MSFT": "윈도우·오피스에 이어 Azure 클라우드와 AI(Copilot) 사업이 성장 축. 기업용 소프트웨어 시장의 압도적 지배력을 보유합니다.",
    "GOOGL": "전 세계 검색·온라인 광고 시장의 절대 강자. 유튜브·클라우드·AI(Gemini)로 사업을 다각화하고 있습니다.",
    "AMZN": "전자상거래 1위 사업자이자, 고수익 사업부인 AWS 클라우드로 전체 이익의 상당 부분을 창출합니다.",
    "NVDA": "AI 반도체(GPU) 시장을 사실상 독점하다시피 하는 기업. 데이터센터 수요가 실적을 견인하지만 그만큼 경기·기술 변화에 민감합니다.",
    "V": "전 세계 카드 결제망을 운영하는 회사로, 직접 대출을 하지 않아 신용 리스크 없이 거래 수수료로 수익을 냅니다.",
    "MA": "비자와 함께 글로벌 결제 네트워크를 양분하는 회사. 현금 없는 사회로의 전환이 장기 성장 동력입니다.",
    "JNJ": "제약·의료기기 사업을 영위하는 헬스케어 대기업. 경기 방어적 성격이 강하고 배당을 오래 늘려온 기업입니다.",
    "PG": "질레트·팸퍼스 등 생활용품 브랜드를 다수 보유한 필수소비재 기업. 경기와 무관하게 꾸준한 수요가 특징입니다.",
    "KO": "전 세계 200개국 이상에서 판매되는 음료 브랜드 포트폴리오를 보유. 워런 버핏의 대표적 장기 보유 종목 중 하나입니다.",
    "WMT": "세계 최대 소매업체로, 오프라인 매장망과 온라인 사업을 함께 키우며 아마존과 경쟁하고 있습니다.",
    "COST": "회원제 창고형 할인매장. 박리다매 구조지만 회원비 수익과 높은 재구매율로 안정적 현금흐름을 냅니다.",
    "HD": "북미 최대 홈 인테리어·건축자재 소매업체. 주택시장·리모델링 수요에 실적이 연동됩니다.",
    "UNH": "미국 최대 건강보험사. 보험(UnitedHealthcare)과 의료서비스(Optum) 두 축으로 운영됩니다.",
    "JPM": "미국 최대 은행. 소비자금융·투자은행·자산관리를 아우르는 종합 금융그룹입니다.",
}


def _get(path: str, **params) -> list | dict:
    params["apikey"] = config.FMP_API_KEY
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{FMP_BASE}/{path}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        # 무료 플랜은 일부 종목/엔드포인트를 막아둔다 (402 Payment Required) — 그 종목만 건너뛴다.
        print(f"    ! {path}({params.get('symbol')}) 실패: HTTP {e.code}")
        return []


def fetch_one(symbol: str) -> dict | None:
    profile = _get("profile", symbol=symbol)
    ratios = _get("ratios-ttm", symbol=symbol)
    income = _get("income-statement", symbol=symbol, limit=2)
    if not profile or not ratios or len(income) < 2:
        return None
    p, r = profile[0], ratios[0]
    rev_growth = (income[0]["revenue"] - income[1]["revenue"]) / income[1]["revenue"]
    net_margin = r.get("netProfitMarginTTM")
    per = (p["price"] / r["netIncomePerShareTTM"]) if r.get("netIncomePerShareTTM") else None

    return {
        "symbol": symbol,
        "name": p["companyName"],
        "industry": p.get("industry", ""),
        "price": round(p["price"], 2),
        "day_return": round(p.get("changePercentage", 0) / 100, 4),
        "market_cap": p.get("marketCap"),
        "revenue_growth": round(rev_growth, 4),
        "net_margin": round(net_margin, 4) if net_margin is not None else None,
        "debt_to_equity": round(r.get("debtToEquityRatioTTM"), 2) if r.get("debtToEquityRatioTTM") is not None else None,
        "per": round(per, 1) if per else None,
        "note": COMPANIES[symbol],
    }


def main():
    if not config.FMP_API_KEY or config.FMP_API_KEY.startswith("YOUR_"):
        raise SystemExit("FMP_API_KEY가 설정되지 않았습니다 (.env 확인).")

    os.makedirs(config.DATA_DIR, exist_ok=True)
    companies = []
    for sym in COMPANIES:
        print(f"조회 중: {sym} ...")
        row = fetch_one(sym)
        if row:
            companies.append(row)
            print(f"  {row['name']}: 매출성장 {row['revenue_growth']:+.1%}, 순이익률 {row['net_margin']:.1%}, "
                  f"부채비율 {row['debt_to_equity']}, PER {row['per']}")
        else:
            print(f"  {sym}: 데이터 조회 실패 (건너뜀)")

    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "companies": companies}
    path = os.path.join(config.DATA_DIR, "..", "card_news", "quality.json")
    path = os.path.normpath(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n저장 완료: {path} ({len(companies)}개 종목)")


if __name__ == "__main__":
    main()
