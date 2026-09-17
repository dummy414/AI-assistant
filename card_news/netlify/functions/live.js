// live.js — 서버리스 함수 (Netlify Functions)
// ================================================
// 브라우저는 이 함수만 호출한다. Alpaca API 키는 여기(서버 쪽 환경변수)에만 있고
// 절대 클라이언트로 내려가지 않는다 — 키를 페이지 소스에 넣으면 누구나 훔쳐갈 수 있어서
// 반드시 이렇게 중계해야 한다.
//
// 한 번 호출로: 10개 종목의 최신가·당일등락(snapshot) + 최근 뉴스를 함께 반환한다.
// 클라이언트가 이 함수를 30~60초마다 다시 호출하면 화면이 계속 갱신된다("실시간처럼").
//
// 필요한 환경변수 (Netlify 대시보드 또는 `netlify env:set`으로 설정):
//   ALPACA_API_KEY, ALPACA_SECRET_KEY

const SYMBOLS = ["BA", "GS", "IBM", "AXP", "COP", "XOM", "BAC", "CSCO", "COIN", "HON"];

const NAMES = {
  BA: "Boeing", GS: "Goldman Sachs", IBM: "IBM", AXP: "American Express",
  COP: "ConocoPhillips", XOM: "ExxonMobil", BAC: "Bank of America",
  CSCO: "Cisco", COIN: "Coinbase", HON: "Honeywell",
};

exports.handler = async function () {
  const API_KEY = process.env.ALPACA_API_KEY;
  const SECRET_KEY = process.env.ALPACA_SECRET_KEY;

  if (!API_KEY || !SECRET_KEY) {
    return { statusCode: 500, body: JSON.stringify({ error: "서버에 ALPACA API 키가 설정되지 않았습니다." }) };
  }

  const headers = { "APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET_KEY };
  const BENCHMARK = "SPY";
  const symbolsParam = SYMBOLS.join(",");

  try {
    // 1) 실시간 시세 스냅샷 (최신 체결가 + 당일봉 + 직전봉 — 등락률 계산용)
    //    SPY도 함께 받아서 상대강도(rel_strength) 계산에 쓴다.
    const snapRes = await fetch(
      `https://data.alpaca.markets/v2/stocks/snapshots?symbols=${symbolsParam},${BENCHMARK}&feed=iex`,
      { headers }
    );
    const snapData = await snapRes.json();

    function toQuote(sym, s) {
      if (!s) return { symbol: sym, name: NAMES[sym], ok: false };
      const price = s.latestTrade ? s.latestTrade.p : (s.dailyBar ? s.dailyBar.c : null);
      const prevClose = s.prevDailyBar ? s.prevDailyBar.c : null;
      const dayReturn = price != null && prevClose ? price / prevClose - 1 : null;
      return {
        symbol: sym, name: NAMES[sym], ok: price != null,
        price, prev_close: prevClose, day_return: dayReturn,
        as_of: s.latestTrade ? s.latestTrade.t : null,
      };
    }

    const quotes = SYMBOLS.map((sym) => toQuote(sym, snapData[sym]));
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
    SYMBOLS.forEach((s) => (newsBySymbol[s] = []));
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
