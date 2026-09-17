// ask.js — 특정 종목에 대한 정량적 리서치 챗봇 (서버리스 함수)
// ============================================================
// "이 종목 사도 될까?" 같은 질문에는 답하지 않는다. 대신 실제 가격·변동성·
// 거래량·재무지표·최근 뉴스를 모아서 Claude에게 넘기고, 그 데이터로만
// 답하도록 시스템 프롬프트에서 강하게 제한한다 — 예측/매수·매도 추천 금지.
//
// 준비된 데이터로 부족하면 Claude가 web_search 도구로 직접 검색할 수 있다.
// 검색 1회당 $0.01 고정 요금 + 검색 결과가 통째로 컨텍스트에 들어가면서 붙는 토큰 비용
// (Haiku는 결과 필터링 없이 원문을 그대로 받기 때문에, 실측 기준 검색 1회당 약 $0.02 수준)
// — 그래서 답변 1건당 최대 2회(max_uses)로 캡을 걸어둔다 (대화 하나 기준 대략 $0.10 예산).
//
// 필요한 환경변수: ALPACA_API_KEY, ALPACA_SECRET_KEY, ANTHROPIC_API_KEY
// FMP_API_KEY는 선택 (없으면 재무데이터 없이 답변)

const SYSTEM_PROMPT = `당신은 '오늘의 관심종목' 사이트의 종목 리서치 도우미입니다.
[실제 데이터]로 제공되는 가격·변동성·거래량·재무지표·최근 뉴스가 기본 근거입니다.
그것만으로 부족하면 주저하지 말고 web_search 도구를 사용해 직접 찾아보세요 — 애널리스트 의견,
최신 뉴스, 업계 비교처럼 [실제 데이터]에 없는 정보를 물어보면 검색해서 답하세요
(검색은 비용이 들므로 한 번의 답변에 최대 2회까지만 사용).

절대 하지 말아야 할 것:
- 이 종목을 지금 사야 하는지/팔아야 하는지 추천하지 마세요.
- 주가가 오를지 내릴지 예측하지 마세요.
- "지금이 매수 타이밍" 같은 뉘앙스도 피하세요.
- 웹 검색으로 애널리스트 목표주가나 제3자 의견을 찾았더라도, 그걸 본인의 추천처럼 포장하지 말고
  "누가 이렇게 말했다"는 사실로만 전달하세요.

이런 질문을 받으면: 예측이나 투자 추천은 제공하지 않는다고 짧게 설명하고,
대신 제공된 데이터(필요하면 검색 결과 포함) 중 질문과 관련된 사실을 요약해서 알려주세요.
데이터에도 검색에도 없는 내용은 절대 지어내지 말고 "확인되지 않았습니다"라고 답하세요.
답변은 3~6문장 정도로 간결하게 하세요.`;

function clip(s) { return String(s || "").slice(0, 4000); }

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

  return { price, dayReturn, volatilityAnnual, high, low, volRatio, news, barCount: barList.length };
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
    const [alpacaCtx, fmpCtx] = await Promise.all([
      fetchAlpacaContext(symbol, headers),
      fetchFmpContext(symbol, FMP_API_KEY),
    ]);

    const lines = [];
    lines.push(`종목: ${symbol}${fmpCtx && fmpCtx.name ? " (" + fmpCtx.name + ")" : ""}`);
    if (alpacaCtx.price != null) {
      lines.push(
        `현재가: $${alpacaCtx.price.toFixed(2)}, 당일 등락률: ${
          alpacaCtx.dayReturn != null ? (alpacaCtx.dayReturn * 100).toFixed(2) + "%" : "확인불가"
        }`
      );
    } else {
      lines.push("현재가: 확인불가 (해당 티커의 시세를 찾지 못했습니다)");
    }
    if (alpacaCtx.volatilityAnnual != null) {
      lines.push(`최근 약 1년 데이터 기준 연환산 변동성(추정): ${(alpacaCtx.volatilityAnnual * 100).toFixed(1)}%`);
      lines.push(`같은 기간 내 최고가: $${alpacaCtx.high.toFixed(2)}, 최저가: $${alpacaCtx.low.toFixed(2)}`);
    }
    if (alpacaCtx.volRatio != null) {
      lines.push(`오늘 거래량 / 최근 20일 평균 거래량 배수: ${alpacaCtx.volRatio.toFixed(2)}배`);
    }
    if (fmpCtx) {
      lines.push(
        `업종: ${fmpCtx.industry || "확인불가"}, 시가총액: ${
          fmpCtx.marketCap != null ? "$" + (fmpCtx.marketCap / 1e9).toFixed(1) + "B" : "확인불가"
        }`
      );
      lines.push(
        `PER: ${fmpCtx.per != null ? fmpCtx.per.toFixed(1) : "확인불가"}, 순이익률: ${
          fmpCtx.netMargin != null ? (fmpCtx.netMargin * 100).toFixed(1) + "%" : "확인불가"
        }, 부채비율: ${fmpCtx.debtToEquity != null ? fmpCtx.debtToEquity.toFixed(2) : "확인불가"}`
      );
    } else {
      lines.push("재무데이터(PER·순이익률·부채비율): 확인불가 (FMP 키 미설정 또는 조회 실패)");
    }
    if (alpacaCtx.news.length) {
      lines.push("최근 뉴스:");
      alpacaCtx.news.forEach((n, i) => {
        lines.push(`${i + 1}. [${n.date ? n.date.slice(0, 10) : ""}] ${n.headline} — ${n.summary || ""}`);
      });
    } else {
      lines.push("최근 뉴스: 확인되지 않음");
    }

    const factsBlock = clip(lines.join("\n"));

    const messages = [
      ...history
        .filter((h) => h && (h.role === "user" || h.role === "assistant") && typeof h.content === "string")
        .map((h) => ({ role: h.role, content: clip(h.content) })),
      { role: "user", content: `[실제 데이터]\n${factsBlock}\n\n[질문]\n${clip(question)}` },
    ];

    const claudeRes = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: "claude-haiku-4-5-20251001",
        max_tokens: 700,
        system: SYSTEM_PROMPT,
        messages,
        tools: [{ type: "web_search_20250305", name: "web_search", max_uses: 2 }],
      }),
    });

    if (!claudeRes.ok) {
      const errText = await claudeRes.text();
      return { statusCode: 502, body: JSON.stringify({ error: "AI 응답 실패: " + errText.slice(0, 200) }) };
    }
    const claudeJson = await claudeRes.json();
    const blocks = claudeJson.content || [];
    let answer = blocks.map((c) => c.text || "").join("").trim();

    // 검색을 실제로 했다면 어느 페이지를 참고했는지 짧게 붙여준다 (출처 명시)
    const sources = [];
    const seen = new Set();
    blocks.forEach((b) => {
      (b.citations || []).forEach((c) => {
        if (c.url && !seen.has(c.url)) { seen.add(c.url); sources.push({ url: c.url, title: c.title || c.url }); }
      });
    });
    if (!answer && claudeJson.stop_reason === "pause_turn") {
      answer = "검색이 오래 걸려 답변을 완성하지 못했습니다. 질문을 좀 더 구체적으로 다시 해주세요.";
    }
    if (!answer) answer = "답변을 생성하지 못했습니다.";
    if (sources.length) {
      answer += "\n\n출처: " + sources.slice(0, 3).map((s) => s.title).join(" · ");
    }

    return {
      statusCode: 200,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
      body: JSON.stringify({ answer, symbol, sources }),
    };
  } catch (err) {
    return { statusCode: 500, body: JSON.stringify({ error: String(err) }) };
  }
};
