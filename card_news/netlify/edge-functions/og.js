// og.js — 종목별 공유 미리보기 (Netlify Edge Function, Deno)
// ============================================================================
// 이 사이트는 index.html 한 장짜리라 메타태그가 고정이다. 그래서 ?s=GNRC 링크를
// 카톡·슬랙에 붙여도 "오늘의 관심종목"이라는 같은 카드만 떴다.
//
// 이 함수는 '/' 요청을 가로채서, ?s=<티커>가 붙어 있으면 원본 HTML을 받아
// og:title / og:description 만 그 종목에 맞게 바꿔서 돌려준다.
// 브라우저에서 보이는 화면은 그대로다 — 링크 미리보기만 달라진다.
//
// 종목명·오늘의 한 줄은 사이트가 이미 배포해둔 data.json에서 읽는다.
// (data.json은 '/' 가 아니라서 이 함수를 다시 타지 않는다 — 무한루프 없음)

const SITE = "오늘의 관심종목";
const FALLBACK_DESC = "미국 주식을 회사가 SEC에 직접 낸 공시로 확인하는 곳.";

// 메타태그 content="" 안에 들어가므로 따옴표와 꺾쇠를 반드시 막아야 한다
function attr(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function setMeta(html, selector, value) {
  // <meta property="og:title" content="..."> 의 content만 갈아끼운다
  const re = new RegExp(`(<meta\\s+${selector}\\s+content=")[^"]*(")`, "i");
  return html.replace(re, `$1${attr(value)}$2`);
}

export default async (request, context) => {
  const url = new URL(request.url);
  const sym = (url.searchParams.get("s") || "").trim().toUpperCase();
  if (!/^[A-Z.]{1,8}$/.test(sym)) return;    // 종목 링크가 아니면 손대지 않는다

  const res = await context.next();
  const type = res.headers.get("content-type") || "";
  if (!type.includes("text/html")) return res;

  let name = "";
  let hook = "";
  try {
    const r = await fetch(new URL("/data.json", url.origin), {
      headers: { accept: "application/json" },
    });
    if (r.ok) {
      const d = await r.json();
      const card = (d.cards || []).find((c) => c.symbol === sym);
      if (card) { name = card.name || ""; hook = card.hook || ""; }
    }
  } catch (e) {
    // 미리보기 문구가 덜 예뻐질 뿐이므로 조용히 넘어간다
  }

  const title = name ? `${sym} · ${name} — ${SITE}` : `${sym} — ${SITE}`;
  const desc = hook
    ? `${hook} · 회사가 SEC에 낸 공시와 재무보고를 함께 봅니다.`
    : `${sym}가 SEC에 낸 공시와 보고한 숫자를 한 화면에서 봅니다. ${FALLBACK_DESC}`;

  let html = await res.text();
  html = setMeta(html, 'property="og:title"', title);
  html = setMeta(html, 'property="og:description"', desc);
  html = setMeta(html, 'property="og:url"', `${url.origin}/?s=${sym}`);
  html = setMeta(html, 'name="twitter:title"', title);
  html = setMeta(html, 'name="twitter:description"', desc);
  html = html.replace(/<title>[^<]*<\/title>/i, `<title>${attr(title)}</title>`);

  return new Response(html, {
    status: res.status,
    headers: { ...Object.fromEntries(res.headers), "content-type": "text/html; charset=utf-8" },
  });
};

export const config = { path: "/" };
