// 돈이 나가는 함수가 열린 채로 나가지 않게.
// ============================================================================
// 2026-09-23 새벽, 잠금 없는 옛 ask 함수가 8시간 넘게 배포돼 있었다.
// 코드에는 잠금이 있었고 guard.test.mjs도 전부 통과했다 — 문제는 그 코드가
// 배포되지 않았다는 것이었다(Netlify가 캐시된 예전 번들을 도로 올렸다).
//
// 여기서 막을 수 있는 건 '코드가 잘못되는 것'까지다. '배포가 잘못되는 것'은
// 파일만 봐서는 알 수 없으므로 tools/deploy.py가 배포 직후 실제로 찔러본다.
// 두 개가 한 짝이다.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const FN = path.join(ROOT, "card_news/netlify/functions");
const ask = fs.readFileSync(path.join(FN, "ask.mjs"), "utf8");

test("ask는 돈 쓰기 전에 주인인지 먼저 본다", () => {
  // 파일 전체가 아니라 **요청을 처리하는 함수 안**만 본다.
  // 위쪽의 함수 정의(async function fetchSecContext…)는 실행 순서와 무관하다.
  const handlerAt = ask.search(/export default async function handler\s*\(/);
  assert.ok(handlerAt > 0, "ask.mjs에서 요청 처리 함수를 찾지 못했습니다");
  const handler = ask.slice(handlerAt);

  const guardAt = handler.indexOf("guard.isOwner");
  assert.ok(guardAt > 0, "요청 처리 함수 안에 guard.isOwner 호출이 없습니다");

  // 비용이 발생하는 지점들 — 전부 잠금 뒤에서 불려야 한다
  for (const [what, needle] of [
    ["Claude API 호출", "api.anthropic.com"],
    ["SEC 자료 수집", "fetchSecContext("],
    ["시세·뉴스 조회", "fetchAlpacaContext("],
    ["환경변수 읽기", "process.env.ANTHROPIC_API_KEY"],
  ]) {
    const at = handler.indexOf(needle);
    if (at < 0) continue;
    assert.ok(at > guardAt, `${what}가 잠금(guard.isOwner)보다 먼저 나옵니다`);
  }
});

test("ask는 키가 틀리면 401로 돌려보낸다", () => {
  const block = ask.slice(ask.indexOf("guard.isOwner"), ask.indexOf("guard.isOwner") + 400);
  assert.match(block, /jsonError\(401/, "키가 없을 때 401을 주지 않습니다");
});

test("ask에는 호출 제한도 걸려 있다", () => {
  // 키가 새더라도 폭주하지는 못하게 하는 두 번째 겹
  assert.match(ask, /guard\.rateLimit\(/, "호출 제한이 없습니다");
  assert.match(ask, /jsonError\(429/, "제한을 넘었을 때 429를 주지 않습니다");
});

test("공개 함수에도 호출 제한이 걸려 있다", () => {
  // 공짜 데이터라도 초당 수백 번 불리면 Netlify 사용량이 깎인다
  for (const f of ["live.js", "filings.js"]) {
    const src = fs.readFileSync(path.join(FN, f), "utf8");
    assert.match(src, /guard\.rateLimit\(/, `${f}에 호출 제한이 없습니다`);
  }
});

test("잠금은 설정을 깜빡했을 때 열리는 쪽이 아니라 닫히는 쪽으로 넘어진다", () => {
  const guard = fs.readFileSync(path.join(FN, "lib/guard.js"), "utf8");
  const body = guard.slice(guard.indexOf("function isOwner"));
  // OWNER_KEY가 비어 있으면 곧바로 false — 'if (!expected) return false'
  assert.match(body.slice(0, 260), /if\s*\(!expected\)\s*return false/,
    "OWNER_KEY가 없을 때 아무나 통과하게 될 수 있습니다");
});

test("배포 스크립트가 함수 캐시를 무시한다", () => {
  // 이게 빠지면 Netlify가 예전 번들을 도로 올린다 — 실제로 그렇게 뚫렸다
  const deploy = fs.readFileSync(path.join(ROOT, "tools/deploy.py"), "utf8");
  assert.match(deploy, /--skip-functions-cache/,
    "배포 스크립트에 --skip-functions-cache가 없습니다");
  assert.match(deploy, /401/, "배포 뒤 잠금을 확인하지 않습니다");
});
