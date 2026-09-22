// 화면을 눈으로 확인하는 도구.
// ============================================================================
// CSS를 고칠 때마다 "이렇게 보일 것이다"라고 짐작하는 대신 실제로 찍어본다.
// 짐작으로는 못 잡는 것들이 있다 — 마스트헤드가 파란 밑줄로 그려지고 있었고,
// 회사 이름이 칸을 넘어 옆 칸 위로 흐르고 있었고, 밝은 테마에서는 각주 글자가
// 배경과 2.88:1이라 사실상 안 보였다. 전부 코드만 읽어서는 몰랐던 것들이다.
//
// 쓰는 법
//   npm i puppeteer-core            (한 번만. 크롬은 이미 깔린 걸 쓴다)
//   node tools/screenshot.mjs                    → 배포본
//   node tools/screenshot.mjs http://localhost:8888
//
// 결과는 tools/shots/ 에 png로 떨어지고, 화면마다 아래를 함께 출력한다:
//   · 가로 스크롤이 생겼는지 (휴대폰에서 가장 흔하고 가장 티 나는 결함)
//   · 화면 밖으로 나간 요소가 있는지
//   · 손가락으로 누르기엔 작은 버튼(24px 미만)이 있는지
//   · 본문 글자와 배경의 명암비 (4.5:1이 작은 글자의 기준선)

import fs from "node:fs";
import path from "node:path";

const SITE = process.argv[2] || "https://today-watchlist-kr.netlify.app/";
const OUT = path.join(path.dirname(new URL(import.meta.url).pathname.slice(1)), "shots");

// 크롬은 대부분 이미 깔려 있다. 브라우저를 또 내려받지 않으려고 puppeteer-core를 쓴다.
const CHROME_CANDIDATES = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
];

let puppeteer;
try {
  puppeteer = (await import("puppeteer-core")).default;
} catch {
  console.error("puppeteer-core가 없습니다. 먼저:  npm i puppeteer-core");
  process.exit(1);
}
const chrome = CHROME_CANDIDATES.find((p) => fs.existsSync(p));
if (!chrome) {
  console.error("크롬이나 엣지를 찾지 못했습니다. CHROME_CANDIDATES에 경로를 추가하세요.");
  process.exit(1);
}

// 실제로 쓰이는 폭들. 320은 아직 쓰는 사람이 있는 가장 좁은 화면이다.
const VIEWS = [
  { name: "desktop", w: 1440, h: 1000 },
  { name: "desktop-light", w: 1440, h: 1000, light: true },
  { name: "tablet", w: 820, h: 1100 },
  { name: "phone", w: 390, h: 844, dsf: 2, mobile: true },
  { name: "phone-light", w: 390, h: 844, dsf: 2, mobile: true, light: true },
  { name: "phone-small", w: 320, h: 700, dsf: 2, mobile: true },
  { name: "tab-quality", w: 1440, h: 1000, click: "tabQuality" },
  { name: "tab-radar", w: 1440, h: 1000, click: "tabRadar" },
  { name: "tab-ask", w: 1440, h: 900, click: "tabAsk" },
  { name: "tab-history", w: 1440, h: 1100, click: "tabHistory" },
  { name: "stock", w: 1440, h: 1200, path: "?s=ARM&" },
  { name: "stock-phone", w: 390, h: 844, dsf: 2, mobile: true, path: "?s=ARM&" },
];

// 페이지 안에서 도는 검사. 브라우저 안이라 여기서 쓰는 건 전부 DOM API다.
function audit() {
  const vw = document.documentElement.clientWidth;

  const escaped = [];
  for (const el of document.querySelectorAll("body *")) {
    // 닫힌 서랍·모달과 가로로 넘기는 탭바는 일부러 밖에 둔 것이다
    if (el.closest(".drawer, .modal, .modal-backdrop, .drawer-backdrop, .tabbar")) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    if (r.right > vw + 1 || r.left < -1) {
      const cls = el.className?.baseVal ?? el.className;
      escaped.push(`${el.tagName.toLowerCase()}.${cls || el.id} (${Math.round(r.left)}~${Math.round(r.right)})`);
    }
  }

  // 24px은 손가락으로 누를 수 있는 최소 크기. 글 안에 섞인 링크는 예외다.
  const smallTargets = new Set();
  for (const el of document.querySelectorAll("button, [role=button]")) {
    const r = el.getBoundingClientRect();
    if (r.width && r.height && (r.height < 24 || r.width < 24)) {
      smallTargets.add(`${el.id || el.className} ${Math.round(r.width)}x${Math.round(r.height)}`);
    }
  }

  const luminance = (css) => {
    const [r, g, b] = css.match(/\d+/g).map(Number).map((v) => {
      const x = v / 255;
      return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const contrast = (a, b) => {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const behind = (el) => {
    let p = el;
    while (p) {
      const bg = getComputedStyle(p).backgroundColor;
      if (bg && bg !== "rgba(0, 0, 0, 0)" && bg !== "transparent") return bg;
      p = p.parentElement;
    }
    return getComputedStyle(document.body).backgroundColor;
  };
  const contrasts = {};
  for (const sel of [".body-text", ".mrow-name", ".footer-note", ".sec-title", ".stat-caption", ".primer"]) {
    const el = document.querySelector(sel);
    if (el) contrasts[sel] = contrast(getComputedStyle(el).color, behind(el)).toFixed(2);
  }

  return {
    vw,
    scrollW: document.documentElement.scrollWidth,
    scrollH: document.documentElement.scrollHeight,
    theme: document.documentElement.getAttribute("data-theme") || "(기본)",
    escaped: escaped.slice(0, 10),
    escapedCount: escaped.length,
    smallTargets: [...smallTargets].slice(0, 6),
    contrasts,
    failed: !!document.querySelector(".load-fail"),
  };
}

fs.mkdirSync(OUT, { recursive: true });
const browser = await puppeteer.launch({
  executablePath: chrome,
  headless: "new",
  args: ["--no-sandbox", "--disable-gpu", "--font-render-hinting=none"],
});

let problems = 0;
for (const v of VIEWS) {
  const page = await browser.newPage();
  await page.setViewport({
    width: v.w, height: v.h, deviceScaleFactor: v.dsf ?? 1,
    isMobile: !!v.mobile, hasTouch: !!v.mobile,
  });
  // 밝은 테마는 기기 설정을 따라가므로 크롬에게 "이 기기는 밝은 모드"라고 알려주면 된다
  if (v.light) await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "light" }]);

  const url = SITE + (v.path || "?") + "cb=" + Date.now();
  await page.goto(url, { waitUntil: "networkidle2", timeout: 60000 }).catch(() => {});
  await new Promise((r) => setTimeout(r, 2000));      // 실시간 시세가 한 번 들어올 시간
  if (v.click) {
    await page.click("#" + v.click).catch(() => {});
    await new Promise((r) => setTimeout(r, 1500));
  }

  const a = await page.evaluate(audit);
  await page.screenshot({ path: path.join(OUT, v.name + ".png") });
  await page.close();

  const scrolls = a.scrollW > a.vw + 1;
  const lowContrast = Object.entries(a.contrasts).filter(([, r]) => Number(r) < 4.5);
  if (scrolls || a.escapedCount || a.smallTargets.length || lowContrast.length || a.failed) problems++;

  console.log(`\n── ${v.name}  ${a.vw}px · 테마 ${a.theme} ──`);
  console.log(`   ${a.scrollW}x${a.scrollH}  ${scrolls ? "가로 스크롤 발생!" : "가로 스크롤 없음"}`);
  if (a.failed) console.log("   실패 화면이 떠 있습니다 (자료를 못 불러왔습니다)");
  if (a.escapedCount) console.log(`   화면 밖 ${a.escapedCount}개: ${a.escaped.join(" | ")}`);
  if (a.smallTargets.length) console.log(`   작은 누름 영역: ${a.smallTargets.join(" | ")}`);
  console.log(`   명암비 ${Object.entries(a.contrasts).map(([k, r]) => `${k} ${r}`).join(" · ")}`
    + (lowContrast.length ? "   ← 4.5:1 미만 있음" : ""));
}

await browser.close();
console.log(`\n${OUT} 에 저장했습니다.`);
console.log(problems ? `살펴볼 화면 ${problems}개` : "이상 없음");
