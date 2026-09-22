// lib/guard.js — 누가 부를 수 있고, 얼마나 자주 부를 수 있나
// ============================================================================
// 이 사이트는 공개돼 있다. 대부분의 기능은 공짜 데이터라 누가 봐도 상관없지만,
// **종목 질문 기능만은 부를 때마다 실제로 돈이 나간다** (Claude API + 웹검색,
// 질문 한 번에 약 $0.07). 인증이 없으면 누구나 무한정 쓸 수 있고 그 청구서는
// 사이트 주인에게 간다.
//
// 그래서 두 겹으로 막는다:
//   1) 소유자 키 — 돈 나가는 기능은 키를 가진 사람만
//   2) 호출 제한 — 키가 새더라도 폭주하지 못하게, 그리고 공개 기능도 남용 방지
//
// 호출 제한은 **인스턴스 메모리**에 둔다. 서버리스는 인스턴스가 여러 개 뜨고
// 수시로 사라지므로 완벽한 전역 제한은 아니다. 다만 한 사람이 스크립트로
// 연타하는 경우는 대개 같은 인스턴스로 몰리기 때문에 실질적인 방어가 된다.
// 완벽한 제한이 필요해지면 그때 공유 저장소를 붙이면 된다.

function headerOf(reqOrEvent, name) {
  if (!reqOrEvent) return "";
  // v2 함수(Request 객체)
  if (typeof reqOrEvent.headers?.get === "function") {
    return reqOrEvent.headers.get(name) || "";
  }
  // v1 함수(event 객체) — 헤더 이름은 소문자로 온다
  const h = reqOrEvent.headers || {};
  return h[name] || h[name.toLowerCase()] || "";
}

/** 길이가 달라도 같은 시간이 걸리게 비교한다 (타이밍으로 키를 추측하지 못하도록). */
function safeEqual(a, b) {
  const x = String(a || "");
  const y = String(b || "");
  if (x.length !== y.length) return false;
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x.charCodeAt(i) ^ y.charCodeAt(i);
  return diff === 0;
}

/** 소유자인가. OWNER_KEY가 설정돼 있지 않으면 **아무도 통과시키지 않는다**
 *  (설정을 깜빡했을 때 기능이 열린 채로 남는 게 제일 위험하다). */
function isOwner(reqOrEvent) {
  const expected = process.env.OWNER_KEY;
  if (!expected) return false;
  return safeEqual(headerOf(reqOrEvent, "x-owner-key"), expected);
}

/** 호출자 식별. Netlify가 넣어주는 실제 접속 IP를 쓴다. */
function clientId(reqOrEvent) {
  return (
    headerOf(reqOrEvent, "x-nf-client-connection-ip") ||
    String(headerOf(reqOrEvent, "x-forwarded-for")).split(",")[0].trim() ||
    "unknown"
  );
}

// 버킷: key → { count, resetAt }
const buckets = new Map();
const MAX_BUCKETS = 5000;   // 메모리가 무한정 커지지 않게

/** 제한을 넘었으면 { limited: true, retryAfter }를 준다. */
function rateLimit(key, { max, windowMs }) {
  const now = Date.now();

  // 만료된 것 청소 (맵이 커졌을 때만 — 매 호출마다 전체를 훑지 않는다)
  if (buckets.size > MAX_BUCKETS) {
    for (const [k, v] of buckets) if (v.resetAt <= now) buckets.delete(k);
    if (buckets.size > MAX_BUCKETS) buckets.clear();
  }

  const b = buckets.get(key);
  if (!b || b.resetAt <= now) {
    buckets.set(key, { count: 1, resetAt: now + windowMs });
    return { limited: false, remaining: max - 1 };
  }
  b.count += 1;
  if (b.count > max) {
    return { limited: true, retryAfter: Math.ceil((b.resetAt - now) / 1000) };
  }
  return { limited: false, remaining: max - b.count };
}

module.exports = { isOwner, clientId, rateLimit, headerOf, safeEqual };
