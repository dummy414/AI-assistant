// live.js — 서버리스 함수 (Netlify Functions)
// ================================================
// 브라우저는 이 함수만 호출한다. Alpaca API 키는 여기(서버 쪽 환경변수)에만 있고
// 절대 클라이언트로 내려가지 않는다 — 키를 페이지 소스에 넣으면 누구나 훔쳐갈 수 있어서
// 반드시 이렇게 중계해야 한다.
//
// 한 번 호출로: 오늘의 10개 종목 + 사용자가 직접 추가한 종목(있으면)의
// 최신가·당일등락(snapshot) + 최근 뉴스를 함께 반환한다.
// 클라이언트가 이 함수를 30~60초마다 다시 호출하면 화면이 계속 갱신된다("실시간처럼").
//
// 쿼리 파라미터:
//   ?symbols=RIOT,HUT,...  — 페이지가 지금 화면에 띄우고 있는 종목 (이게 정답이다)
//   ?extra=AAPL,NVDA       — 사용자가 즐겨찾기로 직접 추가한 티커 (예전 방식, 계속 지원)
// 유효하지 않은 티커는 quotes에서 ok:false로 내려간다.
//
// **예전에는 여기에 종목 10개가 하드코딩돼 있었다(BA·GS·IBM·AXP·COP·XOM·BAC·CSCO·COIN·HON).**
// 그런데 매일 선정되는 종목은 따로 정해지므로, 카드 16개 중 우연히 겹치는 2개만 시세가
// 갱신되고 나머지 14개는 전날 종가가 그대로 남았다. 화면 위에는 '실시간'이라고 떠 있는
// 채로. 그래서 페이지가 자기가 보여주는 종목을 직접 알려주는 방식으로 바꿨다.
// 하드코딩 목록은 파라미터가 아예 없을 때의 폴백으로만 남긴다.
//
// 필요한 환경변수 (Netlify 대시보드 또는 `netlify env:set`으로 설정):
//   ALPACA_API_KEY, ALPACA_SECRET_KEY

const FALLBACK_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"];
const MAX_SYMBOLS = 40;   // Alpaca 스냅샷 한 번에 요청할 상한

function parseSymbols(event, key) {
  const raw = (event.queryStringParameters && event.queryStringParameters[key]) || "";
  return raw
    .split(",")
    .map((s) => s.trim().toUpperCase())
    .filter((s) => /^[A-Z.]{1,8}$/.test(s)); // 티커 형식만 허용 (임의 문자열 주입 방지)
}

exports.handler = async function (event) {
  const API_KEY = process.env.ALPACA_API_KEY;
  const SECRET_KEY = process.env.ALPACA_SECRET_KEY;

  if (!API_KEY || !SECRET_KEY) {
    return { statusCode: 500, body: JSON.stringify({ error: "서버에 ALPACA API 키가 설정되지 않았습니다." }) };
  }

  const requested = [...parseSymbols(event, "symbols"), ...parseSymbols(event, "extra")];
  const ALL_SYMBOLS = [...new Set(requested.length ? requested : FALLBACK_SYMBOLS)].slice(0, MAX_SYMBOLS);

  const headers = { "APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET_KEY };
  const BENCHMARK = "SPY";
  const symbolsParam = ALL_SYMBOLS.join(",");

  try {
    // 1) 실시간 시세 스냅샷 (최신 체결가 + 당일봉 + 직전봉 — 등락률 계산용)
    //    SPY도 함께 받아서 상대강도(rel_strength) 계산에 쓴다.
    const snapRes = await fetch(
      `https://data.alpaca.markets/v2/stocks/snapshots?symbols=${symbolsParam},${BENCHMARK}&feed=iex`,
      { headers }
    );
    const snapData = await snapRes.json();

    function toQuote(sym, s) {
      // 종목명은 페이지가 이미 갖고 있다(카드 데이터). 여기서 이름표를 따로 들고
      // 있으면 낡기만 하므로 심볼을 그대로 돌려준다.
      if (!s) return { symbol: sym, name: sym, ok: false };
      const price = s.latestTrade ? s.latestTrade.p : (s.dailyBar ? s.dailyBar.c : null);
      const prevClose = s.prevDailyBar ? s.prevDailyBar.c : null;
      const dayReturn = price != null && prevClose ? price / prevClose - 1 : null;
      return {
        symbol: sym, name: sym, ok: price != null,
        price, prev_close: prevClose, day_return: dayReturn,
        as_of: s.latestTrade ? s.latestTrade.t : null,
      };
    }

    const quotes = ALL_SYMBOLS.map((sym) => toQuote(sym, snapData[sym]));
    const spyQuote = toQuote(BENCHMARK, snapData[BENCHMARK]);
    const spyDayReturn = spyQuote.ok ? spyQuote.day_return : null;

    // 2) 최근 뉴스 (지난 3일, 종목당 최대 3건)
    const since = new Date(Date.now() - 3 * 24 * 3600 * 1000).toISOString();
    const newsRes = await fetch(
      `https://data.alpaca.markets/v1beta1/news?symbols=${symbolsParam}&start=${since}&limit=50`,
      { headers }
    );
    const newsJson = await newsRes.json();
    const items = (newsJson.news || []);

    const newsBySymbol = {};
    ALL_SYMBOLS.forEach((s) => (newsBySymbol[s] = []));
    for (const n of items) {
      for (const sym of n.symbols || []) {
        if (newsBySymbol[sym] && newsBySymbol[sym].length < 3) {
          newsBySymbol[sym].push({
            text: n.headline, source: n.source, url: n.url,
            published_at: n.created_at,
          });
        }
      }
    }

    return {
      statusCode: 200,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
      body: JSON.stringify({
        fetched_at: new Date().toISOString(),
        quotes,
        news: newsBySymbol,
        spy_day_return: spyDayReturn,
      }),
    };
  } catch (err) {
    return { statusCode: 500, body: JSON.stringify({ error: String(err) }) };
  }
};
