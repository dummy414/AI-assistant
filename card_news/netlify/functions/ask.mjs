// ask.mjs — 종목을 '1차 자료'로 조사하고, 답을 흘려보내는 함수 (스트리밍)
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
// **왜 스트리밍인가.** 무료 플랜의 동기 함수는 10초에서 끊긴다. 그래서 한동안
// Haiku에 답변 400자로 묶어두고 웹검색도 껐다. 그런데 실제로 재보니
// **스트리밍 응답은 그 제한에 걸리지 않았다** — 25초짜리가 26.7초 만에 완주했다.
// 그래서 Sonnet + 웹검색 + 긴 답변으로 되돌렸다. 덤으로 답이 타이핑되듯 나와서
// 기다리는 느낌도 사라진다.
//
// 응답 형식은 JSON이 아니라 **평문 스트림**이다. 클라이언트가 받는 대로 화면에 붙인다.
// 자료 수집 단계에서 실패하면 스트림을 열기 전에 JSON 오류로 돌려준다 (상태코드를
// 바꿀 수 있는 마지막 시점이기 때문이다).
//
// **이 기능만 부를 때마다 실제로 돈이 나간다** (Claude API + 웹검색, 질문 한 번에
// 약 $0.07). 사이트는 공개돼 있으므로 인증이 없으면 누구나 무한정 쓸 수 있고 청구서는
// 주인에게 간다. 그래서 소유자 키를 요구하고, 키가 새더라도 폭주하지 못하게
// 호출 제한을 한 겹 더 둔다.
//
// 필요한 환경변수: ALPACA_API_KEY, ALPACA_SECRET_KEY, ANTHROPIC_API_KEY, OWNER_KEY
// FMP_API_KEY는 선택 (없으면 시가총액·PER 없이 답변)

import sec from "./lib/sec.js";
import guard from "./lib/guard.js";

// 소유자용이라 넉넉하지만, 실수로 반복 호출되는 상황은 막을 만큼 좁게.
const RATE = { max: 12, windowMs: 5 * 60 * 1000 };   // 5분에 12번

const MODEL = "claude-sonnet-5";
// **thinking 블록이 max_tokens를 함께 쓴다.** 1200으로 두었더니 사고와 웹검색
// 결과만으로 예산을 다 써서 본문이 시작도 못 하고 stop_reason=max_tokens로 끝난
// 적이 있다(화면에는 "답변을 생성하지 못했습니다"만 떴다).
// 스트리밍이라 실제 소요시간은 '실제로 쓴 양'에 비례하지 '상한'에 비례하지 않는다.
// 답 길이는 프롬프트에서 450~700자로 잡으므로, 상한은 넉넉히 두는 쪽이 안전하다.
const MAX_TOKENS = 4000;
const WEB_SEARCH_MAX_USES = 2;

const SYSTEM_PROMPT = `당신은 '오늘의 관심종목' 사이트의 종목 리서치 도우미입니다.
읽는 사람은 주식을 잘 모르는 초보입니다. 전문용어는 풀어 쓰세요.

**당신의 특징은 1차 자료를 읽는다는 것입니다.**
[공시 타임라인]은 회사가 "이건 중요하다"며 SEC에 직접 신고한 사건들이고,
[보고된 숫자]는 회사가 SEC에 제출한 재무 수치입니다. 기자의 해석이 아니라 원본입니다.
이 둘을 먼저 쓰고, 자료에 없는 것(경쟁사 비교, 업계 동향, 최신 반응)이 필요할 때만
web_search를 쓰세요(최대 2회).

답변은 아래 네 항목으로, 전체 450~700자.

■ 공시
  질문과 가장 관련 있는 신고 1~3건. 반드시 (제출일, 항목번호)를 같이 적으세요.
  예: "데이터센터 공급계약 체결 (2026-07-29, 항목 1.01)"
  공시 원문에 구체적 조건(금액·주식수·단계)이 있으면 그걸 쓰세요 — 그게 기사에 없는 정보입니다.

■ 숫자
  [보고된 숫자]에서 질문과 관련된 항목을 골라 연도별 변화를 설명하세요.
  주어진 값만 쓰고 지어내지 마세요.

■ 양면
  뒷받침하는 사실과 걸리는 사실을 **둘 다**. 한쪽만 쓰면 안 됩니다 —
  좋은 얘기만 늘어놓는 것은 사실상 추천이 됩니다.

■ 못 확인한 것
  자료에도 검색에도 없어 답할 수 없었던 것. 비우지 마세요 —
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

function jsonError(status, message) {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

export default async function handler(request) {
  if (request.method !== "POST") return jsonError(405, "POST만 허용됩니다.");

  // --- 여기서 돈이 나간다. 통과 조건을 먼저 본다. ---
  if (!guard.isOwner(request)) {
    return jsonError(401, "이 기능은 사이트 관리자만 사용할 수 있습니다. "
      + "질문 한 번에 실제 비용이 들어서 공개하지 않았습니다.");
  }
  const rl = guard.rateLimit("ask:" + guard.clientId(request), RATE);
  if (rl.limited) {
    return jsonError(429, `너무 자주 호출했습니다. ${rl.retryAfter}초 뒤에 다시 시도해 주세요.`);
  }

  const API_KEY = process.env.ALPACA_API_KEY;
  const SECRET_KEY = process.env.ALPACA_SECRET_KEY;
  const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY;
  const FMP_API_KEY = process.env.FMP_API_KEY;
  if (!API_KEY || !SECRET_KEY || !ANTHROPIC_API_KEY) {
    return jsonError(500, "서버 환경변수가 설정되지 않았습니다.");
  }

  let body;
  try {
    body = await request.json();
  } catch (e) {
    return jsonError(400, "잘못된 요청입니다.");
  }

  const symbol = String(body.symbol || "").trim().toUpperCase();
  const question = String(body.question || "").trim();
  const history = Array.isArray(body.history) ? body.history.slice(-6) : [];
  if (!/^[A-Z.]{1,8}$/.test(symbol)) return jsonError(400, "올바른 티커가 아닙니다.");
  if (!question || question.length > 300) return jsonError(400, "질문은 1~300자여야 합니다.");

  const headers = { "APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET_KEY };

  // --- 자료 수집: 여기서 실패하면 아직 상태코드를 바꿀 수 있다 ---
  let factsBlock;
  try {
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
    factsBlock = clip(lines.join("\n"), 60000);
  } catch (err) {
    return jsonError(500, "자료를 모으지 못했습니다: " + String(err).slice(0, 120));
  }

  const messages = [
    ...history
      .filter((h) => h && (h.role === "user" || h.role === "assistant") && typeof h.content === "string")
      .map((h) => ({ role: h.role, content: clip(h.content, 2000) })),
    { role: "user", content: `[실제 데이터]\n${factsBlock}\n\n[질문]\n${clip(question, 300)}` },
  ];

  const upstream = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: MODEL,
      max_tokens: MAX_TOKENS,
      system: SYSTEM_PROMPT,
      messages,
      stream: true,
      // Sonnet 5의 사고(thinking)는 적응형이라 프롬프트가 길면 알아서 길어진다.
      // 그게 max_tokens를 먹고 첫 글자까지 22초가 걸리게 만들었다. 이 작업은
      // '주어진 공시를 정해진 형식으로 정리하기'라 깊은 사고가 필요 없어서 끈다.
      thinking: { type: "disabled" },
      ...(WEB_SEARCH_MAX_USES > 0
        ? { tools: [{ type: "web_search_20250305", name: "web_search", max_uses: WEB_SEARCH_MAX_USES }] }
        : {}),
    }),
  });

  if (!upstream.ok || !upstream.body) {
    const t = await upstream.text().catch(() => "");
    return jsonError(502, "AI 응답 실패: " + t.slice(0, 200));
  }

  // --- 여기서부터는 스트림. 상태코드는 더 못 바꾸므로 오류도 본문에 적는다 ---
  const enc = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      const reader = upstream.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      let wrote = false;
      let stopReason = null;
      const sources = [];
      const seen = new Set();

      const push = (t) => { if (t) { controller.enqueue(enc.encode(t)); wrote = true; } };

      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          const lines = buf.split("\n");
          buf = lines.pop();
          for (const line of lines) {
            if (!line.startsWith("data:")) continue;
            const payload = line.slice(5).trim();
            if (!payload || payload === "[DONE]") continue;
            let ev;
            try { ev = JSON.parse(payload); } catch (e) { continue; }
            if (ev.type === "message_delta" && ev.delta && ev.delta.stop_reason) {
              stopReason = ev.delta.stop_reason;
            }
            if (ev.type === "content_block_delta" && ev.delta) {
              // thinking_delta는 모델의 사고 과정이라 화면에 내보내지 않는다
              if (ev.delta.type === "text_delta") push(ev.delta.text);
              // 웹검색을 썼다면 어느 페이지를 봤는지 모아둔다
              if (ev.delta.type === "citations_delta" && ev.delta.citation) {
                const c = ev.delta.citation;
                if (c.url && !seen.has(c.url)) {
                  seen.add(c.url);
                  sources.push(c.title || c.url);
                }
              }
            }
          }
        }
        if (!wrote) {
          push(stopReason === "max_tokens"
            ? "자료를 읽는 데 분량을 다 써서 답을 쓰지 못했습니다. 질문을 좁혀서 다시 물어봐 주세요."
            : "답변을 생성하지 못했습니다. 질문을 조금 더 구체적으로 다시 해주세요.");
        } else if (stopReason === "max_tokens") {
          push("\n\n(분량 제한으로 여기서 끊겼습니다)");
        }
        // 근거를 감추지 않는다 — 무엇을 읽고 답했는지 끝에 밝힌다
        const used = ["SEC 공시·재무보고"];
        if (sources.length) used.push(`웹 ${sources.length}곳`);
        push(`\n\n근거: ${used.join(" · ")}`);
        if (sources.length) push(`\n출처: ${sources.slice(0, 3).join(" · ")}`);
      } catch (err) {
        push(`\n\n(답변이 도중에 끊겼습니다: ${String(err).slice(0, 100)})`);
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "no-store",
      "x-accel-buffering": "no",
    },
  });
}
