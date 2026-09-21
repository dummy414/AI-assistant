// 화면 쪽 날짜 판단. 여기가 틀리면 "정상인데 고장났다"거나 그 반대로 보인다.
import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import fs from "node:fs";
import { loadFunctions, callAsOf, inlineScripts, INDEX } from "./extract.mjs";

const ctx = loadFunctions(["missedWeekdays", "marketDateLabel"], { tradingDays: null },
                          ["WEEKDAY_KO"]);

/** 거래일 목록을 넣거나 빼고 함수를 부른다. */
function withCalendar(days, fn) {
  ctx.tradingDays = days;
  try { return fn(); } finally { ctx.tradingDays = null; }
}

// 2026-09-18은 금요일, 09-21은 월요일
test("주말에는 밀린 거래일이 없다 — 거짓 경고를 띄우면 안 된다", () => {
  for (const today of ["2026-09-19", "2026-09-20", "2026-09-21"]) {
    assert.equal(callAsOf(ctx, today, `missedWeekdays("2026-09-18")`), 0, today);
  }
});

test("화요일 아침에도 금요일 데이터면 1일 밀린 것", () => {
  assert.equal(callAsOf(ctx, "2026-09-22", `missedWeekdays("2026-09-18")`), 1);
});

test("수요일까지 금요일 데이터면 2일 — 확실히 이상하다", () => {
  assert.equal(callAsOf(ctx, "2026-09-23", `missedWeekdays("2026-09-18")`), 2);
});

test("평일 정상 갱신은 0", () => {
  assert.equal(callAsOf(ctx, "2026-09-22", `missedWeekdays("2026-09-21")`), 0);
  assert.equal(callAsOf(ctx, "2026-09-23", `missedWeekdays("2026-09-22")`), 0);
});

// --- 거래일 달력이 있을 때: 공휴일을 '밀린 날'로 세지 않는다 ---
// 2026-11-26(목)은 추수감사절 휴장, 11-27(금)은 단축장이지만 열린다.
const THANKSGIVING_WEEK = ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27", "2026-11-30"];

test("공휴일은 밀린 거래일로 세지 않는다", () => {
  // 수요일(11-25) 데이터를 추수감사절 다음날 아침에 봄 → 그 사이에 장이 선 날이 없다
  const got = withCalendar(THANKSGIVING_WEEK,
    () => callAsOf(ctx, "2026-11-27", `missedWeekdays("2026-11-25")`));
  assert.equal(got, 0, "11-26은 휴장이므로 밀린 것이 아니다");
});

test("달력이 없으면 평일을 세다가 공휴일을 잘못 센다 — 그래서 달력이 필요하다", () => {
  const got = callAsOf(ctx, "2026-11-27", `missedWeekdays("2026-11-25")`);
  assert.equal(got, 1, "근사치 동작(11-26을 평일로 셈). 달력이 있으면 0이 된다");
});

test("달력이 있어도 진짜 밀린 것은 잡는다", () => {
  const got = withCalendar(THANKSGIVING_WEEK,
    () => callAsOf(ctx, "2026-11-30", `missedWeekdays("2026-11-25")`));
  assert.equal(got, 1, "11-27에 장이 섰는데 갱신이 없었다");
});

test("오늘은 아직 장이 안 끝났을 수 있으니 세지 않는다", () => {
  const got = withCalendar(THANKSGIVING_WEEK,
    () => callAsOf(ctx, "2026-11-27", `missedWeekdays("2026-11-24")`));
  assert.equal(got, 1, "11-25만 셈 (오늘인 11-27은 제외)");
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
