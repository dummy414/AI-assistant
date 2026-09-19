// ask.js — 특정 종목을 '1차 자료'로 조사하는 리서치 함수 (서버리스)
// ============================================================================
// 보통의 종목 챗봇은 웹 검색 결과를 요약한다. 그건 기자가 쓴 글을 다시 쓰는 것이고
// 어느 도구나 할 수 있다. 여기서는 **회사가 SEC에 직접 제출한 것**을 먼저 읽는다:
//
//   1) 8-K 타임라인 — 회사가 "이건 중요하다"고 스스로 신고한 사건들.
//      항목번호(1.01 계약, 5.02 임원변경, 4.02 재무제표 오류…)가 곧 사건의 종류다.
//      이 번호는 SEC 메타데이터라서 본문을 파싱할 필요가 없다 = 틀릴 여지가 없다.
//   2) XBRL 재무 추이 — 회사가 태그를 붙여 보고한 실제 숫자 4년치.
//      데이터 벤더의 가공값이 아니라 공시 원본이다. 역시 파싱이 필요 없다.
//
// 10-K 본문에서 '위험요인' 섹션만 잘라내는 것도 시도했지만 포기했다. 정규식이
// 목차와 상호참조 문장("see Item 1A. Risk Factors...")을 섹션 제목으로 오인해서,
// 종목에 따라 엉뚱한 20만 자를 집거나 아예 못 찾았다. 파싱으로 독해를 대신하려다
// 실패한 전례가 이 저장소에 이미 두 번 있다(events.py, replay.py) — 같은 실수를
// 반복하지 않고, 대신 파싱이 필요 없는 구조화 자료만 쓴다.
//
// 이 기능의 차별점은 모델이 아니라 **자료**다. 실제로 공시를 넣어주자 답의 구체성이
// 확 올라갔다 (예: 제네락의 아마존 워런트 169만 주 중 30만 주 즉시 행사, 나머지는
// 납품액 8억 달러 도달에 연동 — 이런 건 어느 기사에도 없고 공시 원문에만 있다).
//
// 필요한 환경변수: ALPACA_API_KEY, ALPACA_SECRET_KEY, ANTHROPIC_API_KEY
// FMP_API_KEY는 선택 (없으면 시가총액·PER 없이 답변)

const sec = require("./lib/sec");

// 모델과 답변 길이는 **Netlify 무료 플랜의 함수 실행 10초 제한**에 맞춰 정했다.
// 이 제한을 넘기면 사용자는 답을 아예 못 받는다. 그래서 여유가 최우선이다.
//
// 같은 프롬프트로 실측한 총 소요시간(자료 수집 포함, 3종목 평균):
//   Sonnet  7.7 / 8.8 / 10.0초  ← 한 건이 제한에 정확히 걸렸다. 못 쓴다.
//   Haiku   5.6 / 6.6 / 6.7초   ← 3.3초 여유
// Netlify를 Pro로 올리면 제한이 26초가 되고 Sonnet + 웹검색까지 쓸 수 있다.
const MODEL = "claude-haiku-4-5-20251001";
// 한국어는 글자당 약 1.5토큰이다. 900토큰(=약 600자)으로 두었더니 모델이 상한까지
// 꽉 채워 쓰면서 총 11초가 나왔고 답변도 문장 중간에 잘렸다. 620토큰(=약 400자)이면
// 생성 6.5초 + 자료수집 1.9초 = 8.4초로 제한 안에 들어온다.
const MAX_TOKENS = 620;
// 웹검색은 1회에 4~6초가 더 붙어 제한을 넘긴다. 공시 원문을 이미 넣어주므로
// 검색 의존도가 낮아졌다고 보고 끈다 (경쟁사 비교 같은 질문은 답이 얕아진다).
const WEB_SEARCH_MAX_USES = 0;

const SYSTEM_PROMPT = `당신은 '오늘의 관심종목' 사이트의 종목 리서치 도우미입니다.
읽는 사람은 주식을 잘 모르는 초보입니다. 전문용어는 풀어 쓰세요.

**당신의 특징은 1차 자료를 읽는다는 것입니다.**
[공시 타임라인]은 회사가 "이건 중요하다"며 SEC에 직접 신고한 사건들이고,
[보고된 숫자]는 회사가 SEC에 제출한 재무 수치입니다. 기자의 해석이 아니라 원본입니다.
웹 검색은 쓸 수 없습니다. 주어진 자료 안에서만 답하고, 없는 것은 없다고 말하세요.

답변은 아래 네 항목으로. **각 항목은 2문장 이내, 전체 400자 이내**로 압축하세요.
길게 쓰면 중간에 잘려서 아무 쓸모가 없습니다. 중요한 것부터 쓰고 나머지는 버리세요.

■ 공시
  질문과 가장 관련 있는 신고 1~2건만. 반드시 (제출일, 항목번호)를 같이 적으세요.
  예: "데이터센터 공급계약 체결 (2026-07-29, 항목 1.01)"

■ 숫자
  [보고된 숫자]에서 질문과 관련된 항목만 골라 연도별 변화를 한두 문장으로.
  주어진 값만 쓰고 지어내지 마세요.

■ 양면
  뒷받침하는 사실과 걸리는 사실을 **각각 한 문장씩**. 한쪽만 쓰면 안 됩니다 —
  좋은 얘기만 늘어놓는 것은 사실상 추천이 됩니다.

■ 못 확인한 것
  자료에 없어 답할 수 없었던 것을 한 문장으로. 비우지 마세요 —
  모르는 걸 모른다고 말하는 게 이 도구의 핵심입니다.

절대 하지 말 것:
- 사야 하는지/팔아야 하는지 추천. "지금이 기회" 같은 뉘앙스도 금지.
- 주가가 오를지 내릴지 예측. 목표주가 제시.
- 애널리스트 의견을 찾았더라도 본인 판단처럼 쓰지 말고 "누가 이렇게 말했다"로만.
- 자료에 없는 숫자나 사건을 지어내기. 없으면 "확인되지 않았습니다".

예측이나 추천을 요구받으면, 그건 하지 않는다고 한 줄로 말하고 위 형식대로 사실을 정리해 주세요.
마크다운 기호(**, ##)는 쓰지 마세요 — 화면에 그대로 보입니다.`;

const EIGHTK_LOOKBACK_DAYS = 240;
const EIGHTK_MAX = 6;
const EIGHTK_CHARS = 2200;      // 한 건당 본문 상한 (8-K는 원래 짧다)

function clip(s, n) { return String(s || "").slice(0, n || 4000); }

// lib은 화면용으로 배열을 주지만, 프롬프트에는 한 줄 문자열이 낫다
function itemsText(items) {
  return sec.itemsToKorean(items).map((x) => `${x.code} ${x.label}`.trim()).join(" / ");
}

async function fetchSecContext(symbol) {
  const cik = await sec.tickerToCik(symbol);
  if (!cik) return null;
  const sub = await sec.fetchSubmissions(cik);
  const rec = (sub && sub.filings && sub.filings.recent) || {};
  if (!rec.form) return null;

  // SEC는 초당 10건을 권고한다. 8-K와 XBRL을 한 번에 15건 쏘지 않고 두 번에 나눈다.
  const eightK = await sec.listEightK(cik, rec, {
    days: EIGHTK_LOOKBACK_DAYS, max: EIGHTK_MAX, withBody: true, bodyChars: EIGHTK_CHARS,
  });
  const annual = await sec.fetchFinancials(cik);

  const latest = {};
  for (const f of ["10-K", "10-Q"]) {
    const i = rec.form.indexOf(f);
    if (i >= 0) latest[f] = rec.filingDate[i];
  }

  return {
    cik,
    company: sub.name,
    industry: sub.sicDescription,
    eightK: eightK.filter((f) => f.text),
    annual,
    latest,
  };
}

async function fetchAlpacaContext(symbol, headers) {
  const [snapRes, barsRes, newsRes] = await Promise.all([
    fetch(`https://data.alpaca.markets/v2/stocks/snapshots?symbols=${symbol}&feed=iex`, { headers }),
    fetch(`https://data.alpaca.markets/v2/stocks/${symbol}/bars?timeframe=1Day&limit=260&feed=iex`, { headers }),
    fetch(`https://data.alpaca.markets/v1beta1/news?symbols=${symbol}&limit=5`, { headers }),
  ]);
  const snap = await snapRes.json();
  const bars = await barsRes.json();
  const newsJson = await newsRes.json();

  const s = snap[symbol];
  const price = s && s.latestTrade ? s.latestTrade.p : s && s.dailyBar ? s.dailyBar.c : null;
  const prevClose = s && s.prevDailyBar ? s.prevDailyBar.c : null;
  const dayReturn = price != null && prevClose ? price / prevClose - 1 : null;

  const barList = bars.bars || [];
  let volatilityAnnual = null, high = null, low = null, volRatio = null;
  if (barList.length > 5) {
    const closes = barList.map((b) => b.c);
    const rets = [];
    for (let i = 1; i < closes.length; i++) rets.push(closes[i] / closes[i - 1] - 1);
    const mean = rets.reduce((a, b) => a + b, 0) / rets.length;
    const variance = rets.reduce((a, b) => a + (b - mean) ** 2, 0) / rets.length;
    volatilityAnnual = Math.sqrt(variance) * Math.sqrt(252);
    high = Math.max(...closes);
    low = Math.min(...closes);
    const vols = barList.map((b) => b.v);
    const recent = vols.slice(-21, -1);
    const avg20 = recent.length ? recent.reduce((a, b) => a + b, 0) / recent.length : null;
    volRatio = avg20 ? vols[vols.length - 1] / avg20 : null;
  }

  const news = (newsJson.news || []).slice(0, 5).map((n) => ({
    headline: n.headline, summary: n.summary, date: n.created_at, source: n.source,
  }));

  return { price, dayReturn, volatilityAnnual, high, low, volRatio, news };
}

async function fetchFmpContext(symbol, fmpKey) {
  if (!fmpKey) return null;
  try {
    const base = "https://financialmodelingprep.com/stable";
    const [pRes, rRes] = await Promise.all([
      fetch(`${base}/profile?symbol=${symbol}&apikey=${fmpKey}`),
      fetch(`${base}/ratios-ttm?symbol=${symbol}&apikey=${fmpKey}`),
    ]);
    const p = await pRes.json();
    const r = await rRes.json();
    if (!p || !p[0]) return null;
    const p0 = p[0], r0 = (r && r[0]) || {};
    return {
      name: p0.companyName, industry: p0.industry, marketCap: p0.marketCap,
      per: r0.netIncomePerShareTTM ? p0.price / r0.netIncomePerShareTTM : null,
      netMargin: r0.netProfitMarginTTM != null ? r0.netProfitMarginTTM : null,
      debtToEquity: r0.debtToEquityRatioTTM != null ? r0.debtToEquityRatioTTM : null,
    };
  } catch (e) {
    return null;
  }
}

function money(v) {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(2) + "B달러";
  if (a >= 1e6) return (v / 1e6).toFixed(1) + "M달러";
  return Math.round(v).toLocaleString() + "달러";
}

// 주의: 매개변수 이름을 sec으로 두면 모듈 `sec`(lib/sec.js)을 가린다. ctx로 받는다.
function buildSecBlock(ctx) {
  if (!ctx) return "[공시 타임라인] 조회 실패 (SEC에서 이 티커를 찾지 못했습니다)";
  const out = [];
  out.push(`[회사] ${ctx.company || "—"} · 업종(SIC) ${ctx.industry || "—"} · CIK ${ctx.cik}`);
  if (ctx.latest["10-K"] || ctx.latest["10-Q"]) {
    out.push(`[최근 정기보고서] 연차보고서(10-K) ${ctx.latest["10-K"] || "—"} · 분기보고서(10-Q) ${ctx.latest["10-Q"] || "—"}`);
  }

  if (ctx.annual.length) {
    const years = ctx.annual[0].years;
    out.push("", `[보고된 숫자] 회사가 SEC에 제출한 연차보고서(10-K) 수치. 회계연도 ${years.join(", ")}`);
    for (const a of ctx.annual) {
      const row = a.years.map((y) => `${y}년 ${money(a.byYear[y])}`).join(" / ");
      out.push(`  ${a.label}: ${row}`);
    }
  } else {
    out.push("", "[보고된 숫자] 없음 (XBRL 자료를 찾지 못했습니다)");
  }

  if (ctx.eightK.length) {
    out.push("", `[공시 타임라인] 최근 ${EIGHTK_LOOKBACK_DAYS}일간 회사가 '중요사항'으로 SEC에 신고한 건 ${ctx.eightK.length}건.`,
      "항목번호가 사건의 종류입니다. 인용할 때 제출일과 항목번호를 함께 쓰세요.");
    ctx.eightK.forEach((f, i) => {
      out.push(`  ${i + 1}) ${f.date} · 항목 ${itemsText(f.items) || "—"}`);
      out.push(`     원문: ${f.text}`);
    });
  } else {
    out.push("", `[공시 타임라인] 최근 ${EIGHTK_LOOKBACK_DAYS}일간 중요사항 신고(8-K) 없음`);
  }
  return out.join("\n");
}

exports.handler = async function (event) {
  if (event.httpMethod !== "POST") {
    return { statusCode: 405, body: JSON.stringify({ error: "POST만 허용됩니다." }) };
  }

  const API_KEY = process.env.ALPACA_API_KEY;
  const SECRET_KEY = process.env.ALPACA_SECRET_KEY;
  const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY;
  const FMP_API_KEY = process.env.FMP_API_KEY;

  if (!API_KEY || !SECRET_KEY || !ANTHROPIC_API_KEY) {
    return { statusCode: 500, body: JSON.stringify({ error: "서버 환경변수가 설정되지 않았습니다." }) };
  }

  let body;
  try {
    body = JSON.parse(event.body || "{}");
  } catch (e) {
    return { statusCode: 400, body: JSON.stringify({ error: "잘못된 요청입니다." }) };
  }

  const symbol = String(body.symbol || "").trim().toUpperCase();
  const question = String(body.question || "").trim();
  const history = Array.isArray(body.history) ? body.history.slice(-6) : [];

  if (!/^[A-Z.]{1,8}$/.test(symbol)) {
    return { statusCode: 400, body: JSON.stringify({ error: "올바른 티커가 아닙니다." }) };
  }
  if (!question || question.length > 300) {
    return { statusCode: 400, body: JSON.stringify({ error: "질문은 1~300자여야 합니다." }) };
  }

  const headers = { "APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET_KEY };

  try {
    // SEC 조회가 실패해도 나머지로 답할 수 있어야 한다
    const [alpacaCtx, fmpCtx, secCtx] = await Promise.all([
      fetchAlpacaContext(symbol, headers),
      fetchFmpContext(symbol, FMP_API_KEY),
      fetchSecContext(symbol).catch(() => null),
    ]);

    const lines = [];
    lines.push(`종목: ${symbol}${fmpCtx && fmpCtx.name ? " (" + fmpCtx.name + ")" : ""}`);
    if (alpacaCtx.price != null) {
      lines.push(`현재가: $${alpacaCtx.price.toFixed(2)}, 당일 등락률: ${
        alpacaCtx.dayReturn != null ? (alpacaCtx.dayReturn * 100).toFixed(2) + "%" : "확인불가"}`);
    } else {
      lines.push("현재가: 확인불가 (해당 티커의 시세를 찾지 못했습니다)");
    }
    if (alpacaCtx.volatilityAnnual != null) {
      lines.push(`최근 약 1년 기준 연환산 변동성(추정): ${(alpacaCtx.volatilityAnnual * 100).toFixed(1)}%`);
      lines.push(`같은 기간 최고가 $${alpacaCtx.high.toFixed(2)} / 최저가 $${alpacaCtx.low.toFixed(2)}`);
    }
    if (alpacaCtx.volRatio != null) {
      lines.push(`오늘 거래량 / 최근 20일 평균: ${alpacaCtx.volRatio.toFixed(2)}배`);
    }
    if (fmpCtx) {
      lines.push(`시가총액: ${fmpCtx.marketCap != null ? "$" + (fmpCtx.marketCap / 1e9).toFixed(1) + "B" : "확인불가"}, ` +
        `PER: ${fmpCtx.per != null ? fmpCtx.per.toFixed(1) : "확인불가"}, ` +
        `순이익률: ${fmpCtx.netMargin != null ? (fmpCtx.netMargin * 100).toFixed(1) + "%" : "확인불가"}, ` +
        `부채비율: ${fmpCtx.debtToEquity != null ? fmpCtx.debtToEquity.toFixed(2) : "확인불가"}`);
    }
    if (alpacaCtx.news.length) {
      lines.push("", "[최근 뉴스] (기자가 쓴 글입니다 — 아래 공시와 구분하세요)");
      alpacaCtx.news.forEach((n, i) => {
        lines.push(`  ${i + 1}. [${n.date ? n.date.slice(0, 10) : ""}] ${clip(n.headline, 200)}`);
      });
    }
    lines.push("", buildSecBlock(secCtx));

    const factsBlock = clip(lines.join("\n"), 60000);

    const messages = [
      ...history
        .filter((h) => h && (h.role === "user" || h.role === "assistant") && typeof h.content === "string")
        .map((h) => ({ role: h.role, content: clip(h.content, 2000) })),
      { role: "user", content: `[실제 데이터]\n${factsBlock}\n\n[질문]\n${clip(question, 300)}` },
    ];

    const claudeRes = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: MAX_TOKENS,
        system: SYSTEM_PROMPT,
        messages,
        ...(WEB_SEARCH_MAX_USES > 0
          ? { tools: [{ type: "web_search_20250305", name: "web_search", max_uses: WEB_SEARCH_MAX_USES }] }
          : {}),
      }),
    });

    if (!claudeRes.ok) {
      const errText = await claudeRes.text();
      return { statusCode: 502, body: JSON.stringify({ error: "AI 응답 실패: " + errText.slice(0, 200) }) };
    }
    const claudeJson = await claudeRes.json();
    const blocks = claudeJson.content || [];
    let answer = blocks.map((c) => c.text || "").join("").trim();

    const sources = [];
    const seen = new Set();
    blocks.forEach((b) => {
      (b.citations || []).forEach((c) => {
        if (c.url && !seen.has(c.url)) { seen.add(c.url); sources.push({ url: c.url, title: c.title || c.url }); }
      });
    });
    if (!answer && claudeJson.stop_reason === "pause_turn") {
      answer = "조사가 오래 걸려 답변을 완성하지 못했습니다. 질문을 좀 더 구체적으로 다시 해주세요.";
    }
    if (!answer) answer = "답변을 생성하지 못했습니다.";

    // 어떤 1차 자료를 실제로 읽었는지 밝힌다 — 근거의 출처를 감추지 않는다
    const used = [];
    if (secCtx && secCtx.eightK.length) used.push(`SEC 중요사항 공시 ${secCtx.eightK.length}건`);
    if (secCtx && secCtx.annual.length) used.push("SEC 재무보고(XBRL)");
    if (sources.length) used.push(`웹 ${sources.length}곳`);
    if (used.length) answer += `\n\n근거: ${used.join(" · ")}`;
    if (sources.length) answer += `\n출처: ${sources.slice(0, 3).map((s) => s.title).join(" · ")}`;

    return {
      statusCode: 200,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
      body: JSON.stringify({
        answer,
        symbol,
        sources,
        filings: secCtx ? secCtx.eightK.map((f) => ({ date: f.date, items: itemsText(f.items) })) : [],
        usage: claudeJson.usage || null,
      }),
    };
  } catch (err) {
    return { statusCode: 500, body: JSON.stringify({ error: String(err) }) };
  }
};
