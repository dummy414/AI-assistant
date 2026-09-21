// calendar.js — 미국 증시가 실제로 열리는 날 목록
// ============================================================================
// 화면의 '갱신이 밀렸나' 판단에 쓴다. 예전에는 평일을 세었는데, 그러면
// 추수감사절·크리스마스처럼 평일인데 장이 안 서는 날을 '밀린 날'로 잘못 센다.
//
// 추측하지 않고 Alpaca의 공식 거래일 달력을 그대로 쓴다. 반휴장(블랙프라이데이
// 13:00 마감)까지 들어 있지만, 여기서는 '열렸나 아닌가'만 있으면 된다.
//
// 달력은 거의 바뀌지 않으므로 CDN에 6시간 캐시한다.

const ALPACA_CALENDAR = "https://api.alpaca.markets/v2/calendar";

function ymd(d) {
  return d.toISOString().slice(0, 10);
}

exports.handler = async function () {
  const API_KEY = process.env.ALPACA_API_KEY;
  const SECRET_KEY = process.env.ALPACA_SECRET_KEY;
  if (!API_KEY || !SECRET_KEY) {
    return { statusCode: 500, body: JSON.stringify({ error: "서버에 ALPACA API 키가 설정되지 않았습니다." }) };
  }

  // 지난 30일 ~ 앞으로 30일. 배너는 '최근에 장이 몇 번 섰나'만 알면 된다.
  const now = Date.now();
  const start = ymd(new Date(now - 30 * 864e5));
  const end = ymd(new Date(now + 30 * 864e5));

  try {
    const r = await fetch(`${ALPACA_CALENDAR}?start=${start}&end=${end}`, {
      headers: { "APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET_KEY },
    });
    if (!r.ok) {
      return { statusCode: 502, body: JSON.stringify({ error: "달력을 불러오지 못했습니다." }) };
    }
    const days = await r.json();
    return {
      statusCode: 200,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "public, max-age=21600, s-maxage=21600",
      },
      body: JSON.stringify({
        from: start,
        to: end,
        // 날짜만 — 화면에서 필요한 건 '이 날 장이 섰나'뿐이다
        trading_days: (days || []).map((d) => d.date),
        generated_at: new Date().toISOString(),
      }),
    };
  } catch (err) {
    return { statusCode: 500, body: JSON.stringify({ error: String(err) }) };
  }
};
