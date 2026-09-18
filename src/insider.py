"""
insider.py — 내부자(임원·이사) 거래 공시를 SEC 원본에서 직접 읽는다
=====================================================================
미국 상장사의 임원·이사·10% 이상 주주는 자기 회사 주식을 사고팔면 2영업일 안에
SEC에 Form 4를 제출해야 한다. 이건 기사가 아니라 **1차 공시 자료**다.

SEC EDGAR는 API 키가 필요 없고 무료다 (User-Agent만 요구). 그래서 이 기능은
추가 비용이 0원이면서, 한국에서 쓰는 도구 대부분이 보여주지 않는 정보를 준다.

**핵심은 거래 코드를 구분하는 것이다.** Form 4의 대부분은 시그널이 아니다:
  P = 장내 매수      ← 자기 돈으로 샀다. 드물고, 그래서 눈에 띈다
  S = 장내 매도      ← 팔았다. 다만 분산투자·세금 등 이유가 많다
  A = 주식 부여      ← 회사가 보상으로 준 것. 본인이 산 게 아니다
  M = 옵션 행사      ← 권리 행사. 보통 S와 짝을 이룬다
  F = 세금납부용 원천징수 ← 자동 처리. 아무 의미 없다
  G = 증여

A·M·F를 '매수/매도'로 뭉뚱그리면 완전히 잘못된 그림이 된다. 초보가 "임원이 팔았다!"며
놀라는 경우 대부분이 F(세금)다. 그래서 여기서는 P/S만 따로 집계하고 나머지는 분리해 둔다.

그리고 이건 **예측이 아니다.** Form 4는 이미 공개된 사실이고 주가에 반영됐을 수 있다.
"임원이 샀으니 오른다"는 주장은 하지 않는다.

실행:  python -m src.insider
"""

from __future__ import annotations
import json
import os
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from . import config

UA = {"User-Agent": "today-watchlist-kr chocanxi@gmail.com"}
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
LOOKBACK_DAYS = 90
MAX_FILINGS_PER_SYMBOL = 10
THROTTLE = 0.15          # SEC 권고: 초당 10건 이하

CODE_LABEL = {
    "P": "장내 매수", "S": "장내 매도", "A": "주식 부여(보상)",
    "M": "옵션 행사", "F": "세금납부용 원천징수", "G": "증여", "C": "전환",
}
MEANINGFUL = {"P", "S"}   # 본인 판단으로 사고판 것


def _get(url: str, as_json: bool = False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read()
    return json.loads(raw) if as_json else raw.decode("utf-8", "replace")


def ticker_to_cik() -> dict[str, str]:
    data = _get(TICKER_MAP_URL, as_json=True)
    return {v["ticker"]: str(v["cik_str"]).zfill(10) for v in data.values()}


def _text(node, path):
    el = node.find(path)
    if el is None:
        return None
    v = el.find("value")
    txt = v.text if v is not None else el.text
    return (txt or "").strip() or None


def parse_form4(cik: str, accession: str) -> list[dict]:
    """Form 4 XML에서 비파생 거래(실제 주식 거래)만 뽑는다."""
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"
    try:
        idx = _get(f"{base}/index.json", as_json=True)
    except Exception:
        return []
    xmls = [f["name"] for f in idx["directory"]["item"] if f["name"].endswith(".xml")]
    if not xmls:
        return []
    time.sleep(THROTTLE)
    try:
        root = ET.fromstring(_get(f"{base}/{xmls[0]}"))
    except Exception:
        return []

    owner = root.find(".//reportingOwner")
    name = _text(owner, "reportingOwnerId/rptOwnerName") if owner is not None else None
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    title = None
    if rel is not None:
        title = _text(rel, "officerTitle")
        if not title:
            if (rel.findtext("isDirector") or "").strip() in ("1", "true"):
                title = "이사"
            elif (rel.findtext("isTenPercentOwner") or "").strip() in ("1", "true"):
                title = "10% 이상 주주"

    out = []
    for tx in root.findall(".//nonDerivativeTransaction"):
        code = _text(tx, "transactionCoding/transactionCode")
        shares = _text(tx, "transactionAmounts/transactionShares")
        price = _text(tx, "transactionAmounts/transactionPricePerShare")
        try:
            shares_f = float(shares) if shares else None
            price_f = float(price) if price else None
        except ValueError:
            shares_f = price_f = None
        out.append({
            "date": _text(tx, "transactionDate"),
            "code": code,
            "label": CODE_LABEL.get(code, code),
            "owner": name,
            "title": title,
            "shares": shares_f,
            "price": price_f,
            "value": round(shares_f * price_f) if (shares_f and price_f) else None,
            "shares_after": _text(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction"),
        })
    return out


def collect(symbols: list[str]) -> dict[str, dict]:
    cikmap = ticker_to_cik()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
    result = {}

    for sym in symbols:
        cik = cikmap.get(sym)
        if not cik:
            continue
        time.sleep(THROTTLE)
        try:
            sub = _get(f"https://data.sec.gov/submissions/CIK{cik}.json", as_json=True)
        except Exception as e:
            print(f"   ! {sym} 공시목록 실패: {e}")
            continue

        rec = sub.get("filings", {}).get("recent", {})
        forms, dates, accs = rec.get("form", []), rec.get("filingDate", []), rec.get("accessionNumber", [])
        targets = [(dates[i], accs[i]) for i in range(len(forms))
                   if forms[i] == "4" and dates[i] >= cutoff][:MAX_FILINGS_PER_SYMBOL]

        txs = []
        for filed, acc in targets:
            time.sleep(THROTTLE)
            for t in parse_form4(cik, acc):
                t["filed"] = filed
                txs.append(t)

        buys = [t for t in txs if t["code"] == "P"]
        sells = [t for t in txs if t["code"] == "S"]
        other = [t for t in txs if t["code"] not in MEANINGFUL]

        result[sym] = {
            "cik": cik,
            "company": sub.get("name"),
            "lookback_days": LOOKBACK_DAYS,
            "filings_checked": len(targets),
            "buy_count": len(buys),
            "sell_count": len(sells),
            "buy_value": sum(t["value"] or 0 for t in buys) or None,
            "sell_value": sum(t["value"] or 0 for t in sells) or None,
            "other_count": len(other),
            # 화면에는 본인 판단 거래(P/S)만 자세히 보여준다
            "transactions": sorted(
                [t for t in txs if t["code"] in MEANINGFUL],
                key=lambda t: t.get("date") or "", reverse=True)[:6],
        }
        b, s = result[sym]["buy_count"], result[sym]["sell_count"]
        print(f"   {sym:6s} 공시 {len(targets):2d}건 → 장내매수 {b} / 장내매도 {s} / 기타 {len(other)}")
    return result


def main():
    snap_path = os.path.join(config.DATA_DIR, "watchlist_snapshot.json")
    with open(snap_path, encoding="utf-8") as f:
        symbols = [s["symbol"] for s in json.load(f)["stocks"]]

    print(f"내부자 거래 조회 (SEC EDGAR, 최근 {LOOKBACK_DAYS}일) — {len(symbols)}종목")
    data = collect(symbols)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "SEC EDGAR Form 4",
        "lookback_days": LOOKBACK_DAYS,
        "symbols": data,
    }
    dist = os.path.normpath(os.path.join(config.DATA_DIR, "..", "card_news", "dist"))
    os.makedirs(dist, exist_ok=True)
    path = os.path.join(dist, "insider.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    with_buys = [s for s, v in data.items() if v["buy_count"]]
    print(f"\n저장 완료: {path}")
    print(f"장내 매수가 있었던 종목: {', '.join(with_buys) if with_buys else '없음'}")


if __name__ == "__main__":
    main()
