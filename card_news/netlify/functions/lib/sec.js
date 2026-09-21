// lib/sec.js — SEC EDGAR 공통 코드 (ask.js와 filings.js가 함께 쓴다)
// ============================================================================
// 이 파일은 functions/ 바로 아래가 아니라 하위 폴더에 있으므로 함수로 배포되지 않고
// 라이브러리로만 번들된다.
//
// 여기 있는 것들의 공통점: **파싱이 필요 없다.**
//   - 8-K 항목번호는 SEC가 메타데이터로 주는 값이다 (본문에서 뽑아내는 게 아니다)
//   - XBRL 수치는 회사가 태그를 붙여 제출한 값이다
// 10-K 본문에서 '위험요인' 섹션을 정규식으로 잘라내는 것도 해봤지만, 목차와
// 상호참조 문장("see Item 1A. Risk Factors...")을 섹션 제목으로 오인해서 버렸다.

const SEC_UA = { "User-Agent": "today-watchlist-kr chocanxi@gmail.com" };

// 8-K 항목번호 = 회사가 스스로 고른 '사건의 종류'.
const ITEM_LABEL = {
  "1.01": "중요 계약 체결", "1.02": "중요 계약 종료", "1.03": "파산·법정관리",
  "1.05": "중대 사이버 침해",
  "2.01": "자산 인수·매각 완료", "2.02": "실적 발표", "2.03": "채무 발생",
  "2.04": "채무 조기상환 사유 발생", "2.05": "구조조정 비용 결정", "2.06": "자산 손상",
  "3.01": "상장폐지 통보", "3.02": "미등록 주식 발행", "3.03": "주주 권리 변경",
  "4.01": "회계법인 변경", "4.02": "과거 재무제표 신뢰 불가",
  "5.01": "경영권 변동", "5.02": "임원·이사 선임/사임", "5.03": "정관 변경",
  "5.07": "주주총회 표결 결과", "5.08": "주주제안 관련",
  "7.01": "Reg FD 공개", "8.01": "기타 중요사항", "9.01": "재무제표·첨부자료",
};

// 초보에게 특히 눈여겨볼 만한 항목 (화면에서 강조한다)
const NOTABLE_ITEMS = new Set(["1.01", "1.02", "1.03", "1.05", "2.01", "2.05", "2.06",
  "3.01", "4.01", "4.02", "5.01", "5.02"]);

// 회사마다 쓰는 태그가 다르다(QCOM은 Revenues, AA는 RevenueFromContract...).
// 후보를 나열해두고 먼저 잡히는 것을 쓴다.
const XBRL_CONCEPTS = [
  ["매출", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]],
  ["영업이익", ["OperatingIncomeLoss"]],
  ["순이익", ["NetIncomeLoss"]],
  ["자산총계", ["Assets"]],
  ["부채총계", ["Liabilities"]],
  ["현금성자산", ["CashAndCashEquivalentsAtCarryingValue"]],
  ["연구개발비", ["ResearchAndDevelopmentExpense"]],
];

function stripHtml(html) {
  return String(html || "")
    .replace(/<(script|style)[^>]*>[\s\S]*?<\/\1>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&#160;|&nbsp;/g, " ")
    .replace(/&#8217;|&rsquo;|&#39;/g, "'")
    .replace(/&#8220;|&#8221;|&ldquo;|&rdquo;|&quot;/g, '"')
    .replace(/&#8212;|&mdash;/g, "—")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}

// 티커→CIK 맵은 10,000개짜리라 한 번 받으면 warm start 동안 재사용한다
let tickerMapCache = null;
async function tickerToCik(symbol) {
  if (!tickerMapCache) {
    const r = await fetch("https://www.sec.gov/files/company_tickers.json", { headers: SEC_UA });
    if (!r.ok) return null;
    const j = await r.json();
    tickerMapCache = {};
    for (const v of Object.values(j)) tickerMapCache[v.ticker] = String(v.cik_str).padStart(10, "0");
  }
  return tickerMapCache[symbol] || null;
}

function itemsToKorean(items) {
  return String(items || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((code) => ({ code, label: ITEM_LABEL[code] || "", notable: NOTABLE_ITEMS.has(code) }));
}

async function fetchSubmissions(cik) {
  const r = await fetch(`https://data.sec.gov/submissions/CIK${cik}.json`, { headers: SEC_UA });
  if (!r.ok) return null;
  return r.json();
}

// 8-K 목록. withBody=true면 본문까지 받아온다 (ask.js용 — 8-K는 원래 짧다).
async function listEightK(cik, rec, { days = 240, max = 6, withBody = false, bodyChars = 2200 } = {}) {
  const cutoff = new Date(Date.now() - days * 864e5).toISOString().slice(0, 10);
  const targets = [];
  for (let i = 0; i < rec.form.length && targets.length < max; i++) {
    if (rec.form[i] !== "8-K" || rec.filingDate[i] < cutoff) continue;
    const codes = String(rec.items[i] || "").split(",").map((s) => s.trim()).filter(Boolean);
    // 9.01(첨부자료)만 있는 건은 알맹이가 없다
    if (codes.length && codes.every((c) => c === "9.01")) continue;
    const acc = rec.accessionNumber[i];
    targets.push({
      date: rec.filingDate[i],
      items: rec.items[i],
      items_ko: itemsToKorean(rec.items[i]),
      url: `https://www.sec.gov/Archives/edgar/data/${Number(cik)}/${acc.replace(/-/g, "")}/${rec.primaryDocument[i]}`,
    });
  }
  if (!withBody) return targets;
  return Promise.all(targets.map(async (t) => {
    try {
      const res = await fetch(t.url, { headers: SEC_UA });
      if (!res.ok) return { ...t, text: "" };
      // 앞머리는 표지(주소·전화번호·거래소 코드)라 알맹이가 뒤에 있다
      const full = stripHtml(await res.text());
      const body = full.length > 1200 ? full.slice(600) : full;
      return { ...t, text: body.slice(0, bodyChars) };
    } catch (e) {
      return { ...t, text: "" };
    }
  }));
}

// XBRL 응답에서 '연간 수치'만 골라낸다. 같은 태그에 분기(3개월) 값과 연간(12개월)
// 값이 섞여 들어오므로 기간 길이로 거른다 — 안 거르면 분기 매출이 연매출 자리에 앉는다.
// 네트워크 없이 검증할 수 있도록 순수 함수로 떼어 두었다.
function pickAnnual(units) {
  const byYear = {};
  for (const u of units || []) {
    if (u.form !== "10-K" || !u.fy || u.val == null) continue;
    if (u.start) {
      const days = (new Date(u.end) - new Date(u.start)) / 864e5;
      if (days < 300 || days > 400) continue;   // 연간 구간만 (분기 제외)
    }
    byYear[u.fy] = u.val;
  }
  const years = Object.keys(byYear).map(Number).sort((a, b) => b - a).slice(0, 4);
  return years.length ? { years, byYear } : null;
}


async function fetchConcept(cik, tags) {
  for (const tag of tags) {
    try {
      const r = await fetch(
        `https://data.sec.gov/api/xbrl/companyconcept/CIK${cik}/us-gaap/${tag}.json`,
        { headers: SEC_UA }
      );
      if (!r.ok) continue;
      const j = await r.json();
      const units = (j.units && (j.units.USD || Object.values(j.units)[0])) || [];
      const picked = pickAnnual(units);
      if (picked) return picked;
    } catch (e) { /* 다음 태그 시도 */ }
  }
  return null;
}

async function fetchFinancials(cik) {
  const results = await Promise.all(
    XBRL_CONCEPTS.map(([, tags]) => fetchConcept(cik, tags).catch(() => null))
  );
  const out = [];
  XBRL_CONCEPTS.forEach(([label], i) => {
    if (results[i]) out.push({ label, years: results[i].years, byYear: results[i].byYear });
  });
  return out;
}

function edgarUrl(cik) {
  return `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${cik}&type=8-K&dateb=&owner=include&count=40`;
}

module.exports = {
  SEC_UA, ITEM_LABEL, NOTABLE_ITEMS, XBRL_CONCEPTS,
  stripHtml, tickerToCik, itemsToKorean, fetchSubmissions,
  listEightK, pickAnnual, fetchConcept, fetchFinancials, edgarUrl,
};
