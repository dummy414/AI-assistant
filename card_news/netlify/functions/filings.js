// filings.js — 한 종목이 SEC에 낸 것을 그대로 돌려주는 함수
// ============================================================================
// '종목 한 장'에서 쓴다. 브라우저가 SEC를 직접 부르지 못하는 게 이유다:
// data.sec.gov/submissions 만 CORS를 열어두고, 티커→CIK 맵과 XBRL은 막혀 있다.
//
// 돌려주는 것은 두 가지뿐이고 둘 다 가공하지 않는다:
//   filings    — 회사가 "이건 중요하다"며 신고한 8-K 목록 (항목번호 = 사건의 종류)
//   financials — 회사가 태그를 붙여 보고한 연간 수치
//
// 공시는 자주 바뀌지 않으므로 CDN에 30분 캐시한다 — 같은 종목을 여러 번 열어도
// SEC를 다시 부르지 않는다.

const sec = require("./lib/sec");
const guard = require("./lib/guard");

// 30분 CDN 캐시가 1차 방어다. 이건 캐시를 우회하는 요청(매번 다른 티커)을 막는 장치 —
// SEC는 초당 10건을 권고하므로 우리 쪽에서 먼저 조여둔다.
const RATE = { max: 40, windowMs: 60 * 1000 };

exports.handler = async function (event) {
  const raw = (event.queryStringParameters && event.queryStringParameters.symbol) || "";
  const symbol = String(raw).trim().toUpperCase();
  if (!/^[A-Z.]{1,8}$/.test(symbol)) {
    return { statusCode: 400, body: JSON.stringify({ error: "올바른 티커가 아닙니다." }) };
  }
  const rl = guard.rateLimit("filings:" + guard.clientId(event), RATE);
  if (rl.limited) {
    return {
      statusCode: 429,
      headers: { "Content-Type": "application/json", "Retry-After": String(rl.retryAfter) },
      body: JSON.stringify({ error: "요청이 너무 잦습니다." }),
    };
  }

  try {
    const cik = await sec.tickerToCik(symbol);
    if (!cik) {
      return {
        statusCode: 200,
        headers: { "Content-Type": "application/json", "Cache-Control": "public, max-age=1800" },
        body: JSON.stringify({ symbol, found: false, reason: "SEC에 등록된 티커가 아닙니다 (해외 상장·ETF 등)." }),
      };
    }
    const sub = await sec.fetchSubmissions(cik);
    const rec = (sub && sub.filings && sub.filings.recent) || null;
    if (!rec || !rec.form) {
      return {
        statusCode: 200,
        headers: { "Content-Type": "application/json", "Cache-Control": "public, max-age=600" },
        body: JSON.stringify({ symbol, found: false, reason: "공시 목록을 불러오지 못했습니다." }),
      };
    }

    const [filings, financials] = [
      await sec.listEightK(cik, rec, { days: 365, max: 10, withBody: false }),
      await sec.fetchFinancials(cik),
    ];

    // 정기보고서는 링크만 (본문은 너무 크고, 파싱하면 틀린다)
    const periodic = [];
    for (const form of ["10-K", "10-Q"]) {
      const i = rec.form.indexOf(form);
      if (i < 0) continue;
      periodic.push({
        form,
        date: rec.filingDate[i],
        url: `https://www.sec.gov/Archives/edgar/data/${Number(cik)}/${rec.accessionNumber[i].replace(/-/g, "")}/${rec.primaryDocument[i]}`,
      });
    }

    return {
      statusCode: 200,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "public, max-age=1800, s-maxage=1800",
      },
      body: JSON.stringify({
        symbol,
        found: true,
        cik,
        company: sub.name || null,
        sic: sub.sicDescription || null,
        edgar_url: sec.edgarUrl(cik),
        filings,
        periodic,
        financials,
        generated_at: new Date().toISOString(),
      }),
    };
  } catch (err) {
    return { statusCode: 500, body: JSON.stringify({ error: String(err) }) };
  }
};
