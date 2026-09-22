// 인증과 호출 제한. 여기가 뚫리면 사이트 주인 돈이 나간다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const guard = require("../../card_news/netlify/functions/lib/guard.js");

function req(headers = {}) {
  return { headers: { get: (k) => headers[k.toLowerCase()] ?? null } };
}
function evt(headers = {}) {
  return { headers };                       // v1 함수는 평범한 객체로 온다
}

test("OWNER_KEY가 없으면 아무도 통과하지 못한다", () => {
  const saved = process.env.OWNER_KEY;
  delete process.env.OWNER_KEY;
  try {
    assert.equal(guard.isOwner(req({ "x-owner-key": "무엇이든" })), false,
      "설정을 깜빡했을 때 기능이 열린 채로 남는 게 제일 위험하다");
  } finally {
    if (saved !== undefined) process.env.OWNER_KEY = saved;
  }
});

test("키가 맞아야 통과한다", () => {
  const saved = process.env.OWNER_KEY;
  process.env.OWNER_KEY = "secret-key-1234";
  try {
    assert.equal(guard.isOwner(req({ "x-owner-key": "secret-key-1234" })), true);
    assert.equal(guard.isOwner(req({ "x-owner-key": "secret-key-1235" })), false);
    assert.equal(guard.isOwner(req({ "x-owner-key": "secret-key-123" })), false, "길이가 달라도 거부");
    assert.equal(guard.isOwner(req({})), false, "헤더가 없으면 거부");
    assert.equal(guard.isOwner(evt({ "x-owner-key": "secret-key-1234" })), true, "v1 함수 형태도 지원");
  } finally {
    if (saved === undefined) delete process.env.OWNER_KEY; else process.env.OWNER_KEY = saved;
  }
});

test("접속 IP로 호출자를 구분한다", () => {
  assert.equal(guard.clientId(req({ "x-nf-client-connection-ip": "1.2.3.4" })), "1.2.3.4");
  assert.equal(guard.clientId(req({ "x-forwarded-for": "5.6.7.8, 9.9.9.9" })), "5.6.7.8",
    "프록시를 거치면 맨 앞이 실제 호출자다");
  assert.equal(guard.clientId(req({})), "unknown");
});

test("한도까지는 통과하고 넘으면 막는다", () => {
  const key = "테스트:" + Math.random();
  const opt = { max: 3, windowMs: 60000 };
  for (let i = 0; i < 3; i++) {
    assert.equal(guard.rateLimit(key, opt).limited, false, `${i + 1}번째는 통과해야 한다`);
  }
  const blocked = guard.rateLimit(key, opt);
  assert.equal(blocked.limited, true);
  assert.ok(blocked.retryAfter > 0, "언제 다시 시도할지 알려줘야 한다");
});

test("호출자가 다르면 서로 영향을 주지 않는다", () => {
  const opt = { max: 1, windowMs: 60000 };
  const a = "사람A:" + Math.random();
  const b = "사람B:" + Math.random();
  guard.rateLimit(a, opt);
  assert.equal(guard.rateLimit(a, opt).limited, true, "A는 한도 초과");
  assert.equal(guard.rateLimit(b, opt).limited, false, "B는 아직 멀쩡해야 한다");
});

test("시간이 지나면 다시 열린다", async () => {
  const key = "만료:" + Math.random();
  const opt = { max: 1, windowMs: 40 };
  guard.rateLimit(key, opt);
  assert.equal(guard.rateLimit(key, opt).limited, true);
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(guard.rateLimit(key, opt).limited, false, "창이 지나면 초기화");
});
