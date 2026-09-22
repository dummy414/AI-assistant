// 화면 쪽 날짜 판단. 여기가 틀리면 "정상인데 고장났다"거나 그 반대로 보인다.
import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import fs from "node:fs";
import { loadFunctions, callAsOf, inlineScripts, INDEX } from "./extract.mjs";

const ctx = loadFunctions(["missedWeekdays", "sessionDueAt", "marketDateLabel"],
                          { tradingDays: null }, ["WEEKDAY_KO", "UPDATE_DUE_UTC_HOUR"]);

/** 거래일 목록을 넣거나 빼고 함수를 부른다. */
function withCalendar(days, fn) {
  ctx.tradingDays = days;
  try { return fn(); } finally { ctx.tradingDays = null; }
}

// 2026-09-18은 금요일, 09-21 월, 09-22 화. 미국 장은 20:00 UTC 마감, 자동화는 22:00 UTC.
const WEEK = ["2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"];

test("장중에는 그날을 밀린 것으로 세지 않는다 — 실제로 겪은 거짓 경고", () => {
  // 09-22 15:17 UTC = 한국 09-23 새벽 0시 17분. 한국은 날짜가 넘어갔지만
  // 미국은 화요일 장이 한창 열려 있다. 월요일 데이터가 최신인 게 맞다.
  const got = withCalendar(WEEK,
    () => callAsOf(ctx, "2026-09-22T15:17:00Z", `missedWeekdays("2026-09-21")`));
  assert.equal(got, 0, "한국 날짜만 보고 세면 1이 나온다 — 그게 버그였다");
});

test("장 마감 뒤 자동화가 돌 시간이 지나면 그때부터 센다", () => {
  const before = withCalendar(WEEK,
    () => callAsOf(ctx, "2026-09-22T21:00:00Z", `missedWeekdays("2026-09-21")`));
  assert.equal(before, 0, "22:00 UTC 실행 전이라 아직 아니다");
  const after = withCalendar(WEEK,
    () => callAsOf(ctx, "2026-09-23T02:00:00Z", `missedWeekdays("2026-09-21")`));
  assert.equal(after, 1, "23:00 UTC가 지났는데 화요일 데이터가 없으면 밀린 것");
});

test("주말에는 밀린 거래일이 없다", () => {
  for (const t of ["2026-09-19T00:00:00Z", "2026-09-20T12:00:00Z", "2026-09-21T09:00:00Z"]) {
    assert.equal(withCalendar(WEEK, () => callAsOf(ctx, t, `missedWeekdays("2026-09-18")`)), 0, t);
  }
});

test("이틀 밀리면 2 — 확실히 이상하다", () => {
  const got = withCalendar(WEEK,
    () => callAsOf(ctx, "2026-09-24T02:00:00Z", `missedWeekdays("2026-09-21")`));
  assert.equal(got, 2, "09-22, 09-23 둘 다 놓쳤다");
});

test("정상 갱신 직후는 0", () => {
  const got = withCalendar(WEEK,
    () => callAsOf(ctx, "2026-09-22T23:30:00Z", `missedWeekdays("2026-09-22")`));
  assert.equal(got, 0);
});

test("달력이 없어도 장중 오판은 하지 않는다", () => {
  // 달력 없이 평일 근사치로 돌 때도 같은 마감 기준이 걸려야 한다
  assert.equal(callAsOf(ctx, "2026-09-22T15:17:00Z", `missedWeekdays("2026-09-21")`), 0);
  assert.equal(callAsOf(ctx, "2026-09-23T02:00:00Z", `missedWeekdays("2026-09-21")`), 1);
});

// --- 거래일 달력이 있을 때: 공휴일을 '밀린 날'로 세지 않는다 ---
// 2026-11-26(목)은 추수감사절 휴장, 11-27(금)은 단축장이지만 열린다.
const THANKSGIVING_WEEK = ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27", "2026-11-30"];

test("공휴일은 밀린 거래일로 세지 않는다", () => {
  // 수요일(11-25) 데이터를 추수감사절 다음날 아침에 봄 → 그 사이에 장이 선 날이 없다
  const got = withCalendar(THANKSGIVING_WEEK,
    () => callAsOf(ctx, "2026-11-27T12:00:00Z", `missedWeekdays("2026-11-25")`));
  assert.equal(got, 0, "11-26은 휴장이므로 밀린 것이 아니다");
});

test("달력이 없으면 평일을 세다가 공휴일을 잘못 센다 — 그래서 달력이 필요하다", () => {
  const got = callAsOf(ctx, "2026-11-27T12:00:00Z", `missedWeekdays("2026-11-25")`);
  assert.equal(got, 1, "근사치 동작(11-26을 평일로 셈). 달력이 있으면 0이 된다");
});

test("달력이 있어도 진짜 밀린 것은 잡는다", () => {
  const got = withCalendar(THANKSGIVING_WEEK,
    () => callAsOf(ctx, "2026-11-30T12:00:00Z", `missedWeekdays("2026-11-25")`));
  assert.equal(got, 1, "11-27에 장이 섰는데 갱신이 없었다");
});

test("옛 한국어 날짜 형식은 판단하지 않는다(null)", () => {
  assert.equal(vm.runInContext(`missedWeekdays("2026년 9월 16일")`, ctx), null);
  assert.equal(vm.runInContext(`missedWeekdays("")`, ctx), null);
  assert.equal(vm.runInContext(`missedWeekdays(null)`, ctx), null);
});

test("거래일 표기에 요일이 들어간다", () => {
  const s = callAsOf(ctx, "2026-09-18", `marketDateLabel("2026-09-18")`);
  assert.match(s, /9월 18일\(금\)/);
  assert.doesNotMatch(s, /새 거래일이 없었습니다/, "당일이면 덧붙이지 않는다");
});

test("며칠 지났으면 그 사실을 함께 적는다", () => {
  const s = callAsOf(ctx, "2026-09-21", `marketDateLabel("2026-09-18")`);
  assert.match(s, /3일간 새 거래일이 없었습니다/);
});

test("옛 형식은 그대로 보여준다", () => {
  assert.equal(vm.runInContext(`marketDateLabel("2026년 9월 16일")`, ctx), "2026년 9월 16일");
  assert.equal(vm.runInContext(`marketDateLabel(null)`, ctx), "—");
});

test("인라인 스크립트에 문법 오류가 없다", () => {
  const scripts = inlineScripts();
  assert.ok(scripts.length > 0);
  for (const [i, s] of scripts.entries()) {
    assert.doesNotThrow(() => new vm.Script(s, { filename: `inline-${i}.js` }));
  }
});

test("getElementById로 찾는 id가 HTML에 모두 있다", () => {
  const html = fs.readFileSync(INDEX, "utf8");
  const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
  const main = inlineScripts().at(-1);
  const missing = [...main.matchAll(/getElementById\(['"]([^'"]+)['"]\)/g)]
    .map((m) => m[1]).filter((id) => !ids.has(id));
  assert.deepEqual(missing, [], "HTML에 없는 id를 찾고 있다");
});
