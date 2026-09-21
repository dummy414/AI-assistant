// 서버리스 함수의 순수 로직. 네트워크를 타지 않는 부분만 검증한다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const live = require("../../card_news/netlify/functions/live.js");
const sec = require("../../card_news/netlify/functions/lib/sec.js");

// ---------------------------------------------------------------- live.js
test("등락률은 정규장 종가로 계산한다 — 시간외 체결을 쓰면 안 된다", () => {
  // 실제로 겪은 값: 주말의 SPY. 시간외 체결이 전일 종가와 거의 같아 -0.00%가 나왔고,
  // 그 탓에 카드의 'SPY 대비'가 당일 등락과 똑같아졌다.
  const q = live.toQuote("SPY", {
    latestTrade: { p: 762.63, t: "2026-09-18T20:08:57Z" },
    dailyBar: { c: 761.62, t: "2026-09-18T04:00:00Z" },
    prevDailyBar: { c: 762.64 },
  });
  assert.equal(q.price, 761.62, "정규장 종가를 써야 한다");
  assert.ok(Math.abs(q.day_return - (761.62 / 762.64 - 1)) < 1e-12);
  assert.ok(q.day_return < -0.001, `-0.13%여야 하는데 ${q.day_return}`);
  assert.equal(q.last_trade, 762.63, "시간외 체결은 참고용으로 따로 내려보낸다");
});

test("스냅샷이 없으면 ok:false", () => {
  const q = live.toQuote("ZZZZ", undefined);
  assert.equal(q.ok, false);
  assert.equal(q.price, undefined === q.price ? q.price : q.price); // 값 자체는 자유
});

test("전일 종가가 없으면 등락률은 null", () => {
  const q = live.toQuote("X", { dailyBar: { c: 10, t: "2026-09-18T04:00:00Z" } });
  assert.equal(q.ok, true);
  assert.equal(q.day_return, null);
});

test("as_of는 봉 날짜다 — 페이지가 '장 마감'을 판단하는 근거", () => {
  const q = live.toQuote("X", {
    dailyBar: { c: 10, t: "2026-09-18T04:00:00Z" }, prevDailyBar: { c: 9 },
  });
  assert.equal(String(q.as_of).slice(0, 10), "2026-09-18");
});

test("티커 파라미터는 형식 검사를 통과한 것만 받는다", () => {
  const ev = { queryStringParameters: { symbols: "riot, hut ,<script>,TOOLONGTICKER,AA" } };
  assert.deepEqual(live.parseSymbols(ev, "symbols"), ["RIOT", "HUT", "AA"]);
});

test("파라미터가 없으면 빈 배열", () => {
  assert.deepEqual(live.parseSymbols({}, "symbols"), []);
  assert.deepEqual(live.parseSymbols({ queryStringParameters: {} }, "symbols"), []);
});

// ---------------------------------------------------------------- lib/sec.js
test("8-K 항목번호를 한국어로 옮기고 눈여겨볼 것을 표시한다", () => {
  const items = sec.itemsToKorean("1.01,9.01");
  assert.deepEqual(items.map((x) => x.code), ["1.01", "9.01"]);
  assert.equal(items[0].label, "중요 계약 체결");
  assert.equal(items[0].notable, true, "계약 체결은 눈여겨볼 항목");
  assert.equal(items[1].notable, false, "첨부자료는 아니다");
});

test("모르는 항목번호도 버리지 않는다", () => {
  const [it] = sec.itemsToKorean("9.99");
  assert.equal(it.code, "9.99");
  assert.equal(it.label, "");
});

test("재무제표 신뢰 불가(4.02)는 반드시 눈에 띄어야 한다", () => {
  assert.equal(sec.itemsToKorean("4.02")[0].notable, true);
  assert.equal(sec.ITEM_LABEL["4.02"], "과거 재무제표 신뢰 불가");
});

test("첨부자료만 있는 공시는 목록에서 뺀다", async () => {
  const rec = {
    form: ["8-K", "8-K", "10-K"],
    filingDate: ["2026-09-16", "2026-09-10", "2026-02-18"],
    items: ["1.01,9.01", "9.01", ""],
    accessionNumber: ["0001-26-1", "0001-26-2", "0001-26-3"],
    primaryDocument: ["a.htm", "b.htm", "c.htm"],
  };
  const out = await sec.listEightK("0001474735", rec, { days: 3650, max: 10 });
  assert.equal(out.length, 1, "9.01만 있는 건은 알맹이가 없다");
  assert.equal(out[0].date, "2026-09-16");
  assert.match(out[0].url, /^https:\/\/www\.sec\.gov\/Archives\/edgar\/data\/1474735\//);
});

test("기간이 지난 공시는 제외한다", async () => {
  const old = "2000-01-01";
  const rec = {
    form: ["8-K"], filingDate: [old], items: ["1.01"],
    accessionNumber: ["0001-00-1"], primaryDocument: ["a.htm"],
  };
  assert.equal((await sec.listEightK("1", rec, { days: 30 })).length, 0);
});

test("XBRL에서 연간 수치만 고른다 — 분기가 섞이면 연매출이 틀린다", () => {
  const units = [
    { form: "10-K", fy: 2025, val: 4.21e9, start: "2024-10-01", end: "2025-09-30" }, // 연간
    { form: "10-K", fy: 2025, val: 1.0e9, start: "2025-07-01", end: "2025-09-30" },  // 분기
    { form: "10-K", fy: 2024, val: 4.30e9, start: "2023-10-01", end: "2024-09-30" },
    { form: "10-Q", fy: 2025, val: 9.9e9, start: "2024-10-01", end: "2025-09-30" },  // 분기보고서
  ];
  const got = sec.pickAnnual(units);
  assert.deepEqual(got.years, [2025, 2024]);
  assert.equal(got.byYear[2025], 4.21e9, "분기 값이 연간 자리를 차지하면 안 된다");
});

test("연도가 최근 4개까지만 남는다", () => {
  const units = [2021, 2022, 2023, 2024, 2025].map((fy) => ({
    form: "10-K", fy, val: fy, start: `${fy - 1}-10-01`, end: `${fy}-09-30`,
  }));
  assert.deepEqual(sec.pickAnnual(units).years, [2025, 2024, 2023, 2022]);
});

test("쓸 수 있는 값이 없으면 null", () => {
  assert.equal(sec.pickAnnual([]), null);
  assert.equal(sec.pickAnnual([{ form: "10-Q", fy: 2025, val: 1 }]), null);
});

test("EDGAR 링크는 https여야 한다", () => {
  assert.match(sec.edgarUrl("0001474735"), /^https:\/\/www\.sec\.gov\//);
});
