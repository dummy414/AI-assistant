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

const guard = require("./lib/guard");

const FALLBACK_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"];
const MAX_SYMBOLS = 40;   // Alpaca 스냅샷 한 번에 요청할 상한

// **이 함수가 부하의 급소다.** 페이지가 45초마다 부르므로 방문자가 100명이면
// 분당 133회가 된다. Alpaca 무료 플랜 한도를 금방 먹는다.
//
// 그래서 CDN에 짧게 캐시한다. 같은 종목 목록을 보는 방문자는 전부 같은 주소를
// 부르므로, 몇 명이 보든 Alpaca로 나가는 호출은 캐시 주기마다 한 번뿐이다.
// 시세가 20초 늦는 건 이 사이트 용도에서 문제가 되지 않는다.
const CACHE_SECONDS = 20;
// 캐시를 우회하는 요청(매번 다른 종목 조합)이 쏟아지는 경우를 대비한 최후 방어선
const RATE = { max: 60, windowMs: 60 * 1000 };

// **latestTrade를 쓰면 안 된다.** 그건 시간외 체결까지 포함한 '마지막 체결'이라,
// 정규장 종가(dailyBar.c)와 다를 수 있다. 실제로 주말에 SPY의 latestTrade가
// 전일 종가와 거의 같은 값이라 당일 등락이 -0.00%로 찍혔고, 그 결과 카드의
// 'SPY 대비'가 등락률과 똑같이 나왔다(SPY가 0%니까).
// dailyBar는 장중에도 실시간으로 갱신되므로 언제 쓰든 앞뒤가 맞는다.
function toQuote(sym, s) {
  // 종목명은 페이지가 이미 갖고 있다(카드 데이터). 여기서 이름표를 따로 들고
  // 있으면 낡기만 하므로 심볼을 그대로 돌려준다.
  if (!s) return { symbol: sym, name: sym, ok: false };
  const bar = s.dailyBar || null;
  const prev = s.prevDailyBar || null;
  const price = bar ? bar.c : null;
  const prevClose = prev ? prev.c : null;
  const dayReturn = price != null && prevClose ? price / prevClose - 1 : null;
  return {
    symbol: sym, name: sym, ok: price != null,
    price, prev_close: prevClose, day_return: dayReturn,
    as_of: bar ? bar.t : null,
    // 시간외까지 포함한 마지막 체결 — 참고용으로만 내려보낸다
    last_trade: s.latestTrade ? s.latestTrade.p : null,

    // --- 장중 사실 ---
    // 화면의 '지금 장중' 탭이 쓴다. 전부 계산하지 않은 원본 값이고,
    // 해석은 화면에서 한다.
    //
    // **호가(latestQuote)는 일부러 내려보내지 않는다.** 무료 IEX 피드는 전체
    // 거래의 일부만 보므로 스프레드가 실제보다 훨씬 넓게 찍힌다 — NVDA가
    // 1센트짜리 스프레드인데 $1.30으로 나온다. 그걸 '매매 비용'처럼 보여주면
    // 사실이 아니라 틀린 정보가 된다.
    //
    // 거래량도 같은 피드라 절대값은 전체 거래량이 아니다. 다만 어제와 오늘을
    // **같은 피드끼리** 비교하는 비율은 의미가 있어서 둘 다 그대로 넘긴다.
    open: bar ? bar.o : null,
    high: bar ? bar.h : null,
    low: bar ? bar.l : null,
    volume: bar ? bar.v : null,
    prev_open: prev ? prev.o : null,
    prev_high: prev ? prev.h : null,
    prev_low: prev ? prev.l : null,
    prev_volume: prev ? prev.v : null,
  };
}

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

  const rl = guard.rateLimit("live:" + guard.clientId(event), RATE);
  if (rl.limited) {
    return {
      statusCode: 429,
      headers: { "Content-Type": "application/json", "Retry-After": String(rl.retryAfter) },
      body: JSON.stringify({ error: "요청이 너무 잦습니다." }),
    };
  }

  const requested = [...parseSymbols(event, "symbols"), ...parseSymbols(event, "extra")];
  const ALL_SYMBOLS = [...new Set(requested.length ? requested : FALLBACK_SYMBOLS)].slice(0, MAX_SYMBOLS);

  const headers = { "APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET_KEY };
  const BENCHMARK = "SPY";
  const symbolsParam = ALL_SYMBOLS.join(",");

  try {
    // 1) 실시간 시세 스냅샷 + 장이 열려 있는지 (Alpaca 공식 시계)
    //    SPY도 함께 받아서 상대강도(rel_strength) 계산에 쓴다.
    //    장이 닫혀 있으면 페이지가 폴링을 크게 늦춘다 — 한국에서 보는 시간대는
    //    대부분 미국 장이 닫혀 있어서, 이것만으로 호출이 몇십 분의 일로 준다.
    const [snapRes, clockRes] = await Promise.all([
      fetch(`https://data.alpaca.markets/v2/stocks/snapshots?symbols=${symbolsParam},${BENCHMARK}&feed=iex`,
            { headers }),
      fetch("https://api.alpaca.markets/v2/clock", { headers }).catch(() => null),
    ]);
    const snapData = await snapRes.json();
    let marketOpen = null, nextOpen = null;
    if (clockRes && clockRes.ok) {
      try {
        const c = await clockRes.json();
        marketOpen = !!c.is_open;
        nextOpen = c.next_open || null;
      } catch (e) { /* 시계를 못 읽어도 시세는 내려보낸다 */ }
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
      headers: {
        "Content-Type": "application/json",
        // 같은 종목 목록을 보는 방문자는 전부 같은 주소를 부르므로, 몇 명이 보든
        // Alpaca로 나가는 호출은 이 주기마다 한 번뿐이다.
        "Cache-Control": `public, max-age=${CACHE_SECONDS}, s-maxage=${CACHE_SECONDS}`,
      },
      body: JSON.stringify({
        fetched_at: new Date().toISOString(),
        quotes,
        news: newsBySymbol,
        spy_day_return: spyDayReturn,
        // 이 시세가 '어느 거래일' 것인지. 주말·공휴일에는 직전 거래일이 내려온다.
        // 페이지는 이 값으로 '실시간'과 '장 마감'을 구분한다.
        session_date: spyQuote.as_of ? String(spyQuote.as_of).slice(0, 10) : null,
        market_open: marketOpen,
        next_open: nextOpen,
      }),
    };
  } catch (err) {
    return { statusCode: 500, body: JSON.stringify({ error: String(err) }) };
  }
};

// 테스트에서 쓰기 위해 순수 함수만 내보낸다 (핸들러 동작에는 영향 없음)
module.exports.toQuote = toQuote;
module.exports.parseSymbols = parseSymbols;
