// 화면의 겉모습은 눈으로 봐야 알 수 있지만, '토큰을 안 쓰고 숫자를 박았다'거나
// '밝은 테마에서 색 하나를 덮어쓰는 걸 깜빡했다' 같은 건 글자만 봐도 알 수 있다.
// 그런 건 사람이 매번 확인하지 말고 여기서 걸러낸다.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { INDEX } from "./extract.mjs";

const html = fs.readFileSync(INDEX, "utf8");
const style = html.slice(html.indexOf("<style>"), html.indexOf("</style>"));
const body = html.slice(html.indexOf("<body>"));

/** `:root { ... }` 같은 블록 안의 `--이름: 값;`을 모은다. */
function tokensIn(selector) {
  const i = style.indexOf(selector + " {");
  assert.ok(i >= 0, `${selector} 블록을 찾지 못했습니다`);
  const block = style.slice(i, style.indexOf("\n  }", i));
  const out = {};
  for (const m of block.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

const dark = tokensIn(":root");
const light = tokensIn(':root[data-theme="light"]');

test("밝은 테마가 모든 색 토큰을 덮어쓴다", () => {
  // 하나라도 빠지면 밝은 배경 위에 어두운 테마의 색이 그대로 남는다.
  // 눈으로 보기 전에는 모르고, 보더라도 '왜 이것만 이상하지'로 끝나기 쉽다.
  const isColor = (v) => /^#[0-9a-f]{3,8}$/i.test(v) || /^rgb|^hsl|color-mix/i.test(v);
  const missing = Object.keys(dark).filter((k) => isColor(dark[k]) && !(k in light));
  assert.deepEqual(missing, [], `밝은 테마에 없는 색 토큰: ${missing.join(", ")}`);
});

test("쓰이는 토큰은 전부 정의돼 있다", () => {
  // var(--surfce) 같은 오타는 조용히 무시되고 그 자리만 스타일이 사라진다.
  const defined = new Set([...Object.keys(dark), ...Object.keys(light)]);
  const used = new Set([...style.matchAll(/var\((--[\w-]+)/g)].map((m) => m[1]));
  const undef = [...used].filter((n) => !defined.has(n));
  assert.deepEqual(undef, [], `정의되지 않은 토큰: ${undef.join(", ")}`);
});

test("정의한 토큰은 전부 쓰인다", () => {
  // 안 쓰는 토큰이 남아 있으면 다음 사람이 '이건 뭐지'부터 시작한다.
  const used = new Set([...style.matchAll(/var\((--[\w-]+)/g)].map((m) => m[1]));
  const dead = Object.keys(dark).filter((n) => !used.has(n));
  assert.deepEqual(dead, [], `아무도 안 쓰는 토큰: ${dead.join(", ")}`);
});

// ---- 명암비 ----
// 색을 고를 때 가장 하기 쉬운 실수는 '보기 좋은 회색'을 골랐는데 정작 안 읽히는 것이다.
// 어두운 화면에서 괜찮아 보이던 회색이 밝은 테마로 가면 거의 사라진다.
// 눈으로는 "좀 연하네" 정도로만 느껴져서 그냥 넘어가기 쉬우니 숫자로 못 박는다.
function luminance(hex) {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? [...h].map((c) => c + c).join("") : h;
  const [r, g, b] = [0, 2, 4].map((i) => {
    const x = parseInt(full.slice(i, i + 2), 16) / 255;
    return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// 글자색 → 그 글자가 얹힐 수 있는 배경들
const TEXT_ON = {
  "--ink": ["--paper", "--surface", "--surface-2"],
  "--ink-2": ["--paper", "--surface", "--surface-2"],
  "--ink-3": ["--paper", "--surface", "--surface-2"],   // 11px 각주에 쓴다. 제일 위험하다
  "--accent": ["--accent-soft", "--surface", "--paper"],
  "--accent-ink": ["--accent"],
  "--up": ["--up-soft", "--surface"],
  "--down": ["--down-soft", "--surface"],
  "--gold": ["--gold-soft"],
};

for (const [themeName, base] of [["어두운", dark], ["밝은", { ...dark, ...light }]]) {
  test(`${themeName} 테마의 글자가 배경 위에서 읽힌다 (4.5:1)`, () => {
    const fails = [];
    for (const [fg, bgs] of Object.entries(TEXT_ON)) {
      if (!base[fg] || !base[fg].startsWith("#")) continue;
      for (const bg of bgs) {
        if (!base[bg] || !base[bg].startsWith("#")) continue;
        const r = contrast(base[fg], base[bg]);
        if (r < 4.5) fails.push(`${fg}(${base[fg]}) on ${bg}(${base[bg]}) = ${r.toFixed(2)}:1`);
      }
    }
    assert.deepEqual(fails, [], "기준(4.5:1)에 못 미치는 조합:\n  " + fails.join("\n  "));
  });
}

test("모서리 값은 토큰으로만 쓴다", () => {
  // 8·10·12가 제각각 박혀 있으면 한 화면 안에서 카드마다 둥글기가 달라진다.
  const hard = [...style.matchAll(/border-radius:\s*([\d.]+px)/g)].map((m) => m[1]);
  // 1~2px은 얇은 막대(햄버거 선 등), 7~8px은 크기에 맞춘 브랜드 마크다.
  const bad = hard.filter((v) => parseFloat(v) > 8);
  assert.deepEqual(bad, [], `토큰을 안 쓴 모서리 값: ${bad.join(", ")}`);
});

test("이모지를 쓰지 않는다", () => {
  // 이모지는 기기마다 다르게 그려진다. 같은 페이지가 사람마다 달라 보인다.
  const found = [...new Set(body.match(/[\u{1F300}-\u{1FAFF}⚠⚙⭐]/gu) || [])];
  assert.deepEqual(found, [], `남은 이모지: ${found.join(" ")}`);
  assert.ok(!/rel="icon"[^>]*%F0%9F/.test(html), "탭 아이콘이 아직 이모지입니다");
});

test("랜드마크가 하나씩 제대로 있다", () => {
  const count = (t) => (body.match(new RegExp(`<${t}[\\s>]`, "g")) || []).length;
  for (const tag of ["header", "main", "footer", "h1"]) {
    assert.equal(count(tag), 1, `<${tag}>는 페이지에 하나여야 합니다`);
  }
  for (const tag of ["div", "section", "article", "aside", "main", "header", "footer", "nav"]) {
    const open = count(tag);
    const close = (body.match(new RegExp(`</${tag}>`, "g")) || []).length;
    assert.equal(open, close, `<${tag}> 여닫이 개수가 다릅니다`);
  }
});

test("방문자에게 개발자용 문구를 보여주지 않는다", () => {
  // 예전 실패 화면은 "data.json 파일을 확인해주세요"였다. 방문자는 그게 뭔지 모른다.
  // (script 안의 undefined·null 같은 건 코드라서 여기서 빼고 본다 — 눈에 보이는 글자만 본다.)
  const markup = body.replace(/<script[\s\S]*?<\/script>/g, "");
  for (const word of ["data.json", "undefined", "NaN", "TODO", "FIXME", "localhost"]) {
    assert.ok(!markup.includes(word), `화면 문구에 "${word}"가 들어 있습니다`);
  }
  // 스크립트가 만드는 문구 중에도 이건 다시 들어오면 안 된다
  assert.ok(!html.includes("data.json 파일을"), "실패 화면이 다시 개발자용 문구로 돌아갔습니다");
});

test("자료가 없는 날에도 그릴 화면이 있다", () => {
  // renderDetail()은 data.cards[active]가 있다고 가정한다. 목록이 비면 거기서 터지고
  // 화면이 뼈대만 남은 채 멈춘다 — 방문자 눈에는 고장이다.
  assert.ok(html.includes("function renderEmptyDay()"), "종목 없는 날 화면이 없습니다");
  assert.match(html, /if \(!d\.cards \|\| !d\.cards\.length\) \{ renderEmptyDay\(\); return; \}/,
    "render()가 빈 목록을 거르지 않습니다");
  assert.match(html, /const c = data\.cards\[active\];\s*\n\s*if \(!c\) return;/,
    "renderDetail()에 빈 목록 방어가 없습니다");
});

test("자료가 오기 전에도 자리를 잡아둔다", () => {
  // 첫 화면이 완전히 비어 있으면 느린 회선에서 '안 열리는 사이트'로 보인다.
  for (const id of ["moversActive", "tickerNav", "detail", "related"]) {
    const i = body.indexOf(`id="${id}"`);
    assert.ok(i >= 0, `#${id}를 찾지 못했습니다`);
    assert.ok(body.slice(i, i + 400).includes("skel"),
      `#${id}에 불러오는 동안 보여줄 뼈대가 없습니다`);
  }
});
