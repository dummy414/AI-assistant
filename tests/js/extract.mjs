// index.html 안의 함수를 꺼내 테스트한다.
// 페이지가 한 파일이라 모듈로 import할 수 없어서, 이름으로 함수 본문만 떼어낸다.
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
export const INDEX = path.join(ROOT, "card_news/dist/index.html");

export function inlineScripts() {
  const html = fs.readFileSync(INDEX, "utf8");
  return [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
}

function cut(src, name) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`index.html에서 ${name}을 찾지 못했습니다`);
  let depth = 0;
  for (let j = src.indexOf("{", start); j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") { depth--; if (depth === 0) return src.slice(start, j + 1); }
  }
  throw new Error(`${name}의 끝을 찾지 못했습니다`);
}

/** `const NAME = ...;` 한 줄을 통째로 꺼낸다 (함수가 참조하는 상수용). */
function cutConst(src, name) {
  const re = new RegExp(`const\\s+${name}\\s*=[^;]*;`, "s");
  const m = src.match(re);
  if (!m) throw new Error(`index.html에서 상수 ${name}을 찾지 못했습니다`);
  return m[0];
}

/** 이름으로 함수들을 꺼내 하나의 샌드박스에 넣고 돌려준다. */
export function loadFunctions(names, extraContext = {}, consts = []) {
  const main = inlineScripts().at(-1);
  const ctx = {
    console,
    ESCAPE_MAP: { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" },
    ...extraContext,
  };
  vm.createContext(ctx);
  vm.runInContext(`
    function escapeHtml(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>ESCAPE_MAP[c]);}
    function safeUrl(u){return typeof u==='string'&&/^https?:\\/\\//i.test(u)?u:'';}
    function pct(x){return (x>=0?'+':'')+(x*100).toFixed(2)+'%';}
    ${consts.map((n) => cutConst(main, n)).join("\n")}
    ${names.map((n) => cut(main, n)).join("\n")}
  `, ctx);
  return ctx;
}

/** 특정 시각을 '지금'으로 고정해 함수를 부른다.
 *
 *  시각은 **UTC**로 준다. 브라우저 로컬 시간으로 고정하면 실행하는 기계의 시간대에 따라
 *  결과가 달라져서, 정작 잡아야 할 시간대 버그를 못 잡는다 (실제로 한국 시간 자정 넘은
 *  새벽에 미국 장이 열려 있는 상황에서 거짓 경고가 떴다).
 *  `isoUtc` 예: "2026-09-22T15:17:00Z"
 */
export function callAsOf(ctx, isoUtc, expr) {
  const fixed = new Date(isoUtc.length <= 10 ? `${isoUtc}T09:00:00Z` : isoUtc).getTime();
  const Real = Date;
  ctx.Date = class extends Real {
    constructor(...a) { if (!a.length) super(fixed); else super(...a); }
    static now() { return fixed; }
    static UTC(...a) { return Real.UTC(...a); }
    static parse(s) { return Real.parse(s); }
  };
  try {
    return vm.runInContext(expr, ctx);
  } finally {
    ctx.Date = Real;
  }
}
