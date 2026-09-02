// Generates the design artboards (*.dc.html) and canvas.json for the chess tutor mockup.
// Run: node build.mjs   (from the design/ directory)
import { writeFileSync } from 'node:fs';
import { boardBlock } from './board.mjs';
import { pieceSVG } from './pieces.mjs';

// ---------- tokens ----------
const T = {
  paper: '#f4efe6', surface: '#fcf9f3', ink: '#2b2622', ink2: '#6b6259', ink3: '#9b9187',
  line: '#e4dbcb', lineStrong: '#cdc1ab',
  good: '#2478a6', goodBg: 'rgba(36, 120, 166, 0.10)',
  bad: '#c25a3c', badBg: 'rgba(194, 90, 60, 0.12)',
  boardLight: '#ecdfc5', boardDark: '#a98b68',
};
const FONT_UI = "'IBM Plex Sans KR', 'Apple SD Gothic Neo', 'Noto Sans KR', system-ui, sans-serif";
const FONT_DISPLAY = "'Gowun Batang', 'Apple Myungjo', 'Noto Serif KR', Georgia, serif";
const FONT_MONO = "'IBM Plex Mono', 'SF Mono', Menlo, Consolas, monospace";

const HEAD = `<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Gowun+Batang:wght@400;700&amp;family=IBM+Plex+Sans+KR:wght@400;500;600;700&amp;family=IBM+Plex+Mono:wght@400;500;600&amp;display=swap">
  <style>
    body { margin: 0; background: ${T.paper}; color: ${T.ink}; font-family: ${FONT_UI}; font-size: 14px; line-height: 1.5; -webkit-font-smoothing: antialiased; }
    a { color: ${T.good}; text-decoration: none; } a:hover { color: #1b5f85; text-decoration: underline; }
    * { box-sizing: border-box; }
    .mono { font-family: ${FONT_MONO}; font-variant-numeric: tabular-nums; }
    .display { font-family: ${FONT_DISPLAY}; }
    .muted { color: ${T.ink2}; }
    .faint { color: ${T.ink3}; }
    .small { font-size: 12px; }
    .card { background: ${T.surface}; border: 1px solid ${T.line}; border-radius: 10px; }
    .h3 { font-family: ${FONT_DISPLAY}; font-size: 16px; font-weight: 700; color: ${T.ink}; margin: 0; letter-spacing: -0.01em; }
    .eyebrow { font-size: 11px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: ${T.ink3}; }
    .chip { display: inline-flex; align-items: center; gap: 6px; height: 24px; padding: 0 10px; border-radius: 999px; border: 1px solid ${T.lineStrong}; font-size: 12px; font-weight: 500; color: ${T.ink2}; background: ${T.surface}; }
    .btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; height: 36px; padding: 0 14px; border-radius: 8px; font-size: 13px; font-weight: 600; font-family: ${FONT_UI}; white-space: nowrap; flex-shrink: 0; }
    .btn-primary { background: ${T.ink}; color: ${T.paper}; }
    .btn-ghost { border: 1px solid ${T.lineStrong}; color: ${T.ink}; background: transparent; }
    .badge { display: inline-flex; align-items: center; gap: 5px; height: 22px; padding: 0 8px; border-radius: 6px; font-size: 12px; font-weight: 700; }
    .badge-bad { background: ${T.badBg}; color: ${T.bad}; }
    .badge-good { background: ${T.goodBg}; color: ${T.good}; }
    .badge-neutral { background: rgba(43, 38, 34, 0.07); color: ${T.ink2}; }
    .mv { font-family: ${FONT_MONO}; font-weight: 600; }
    table { border-collapse: collapse; }
  </style>
</helmet>
`;
const FOOT = `</x-dc>
</body>
</html>
`;

// ---------- icons (stroke, 20px grid) ----------
const ico = {
  review: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"></rect><path d="M3 9h18M3 15h18M9 3v18M15 3v18"></path></svg>',
  openings: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="5" cy="12" r="2.5"></circle><circle cx="19" cy="6" r="2.5"></circle><circle cx="19" cy="18" r="2.5"></circle><path d="M7.4 11L16.6 7M7.4 13l9.2 4"></path></svg>',
  profile: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"></circle><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"></path></svg>',
  train: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><circle cx="12" cy="12" r="4.5"></circle><circle cx="12" cy="12" r="1"></circle></svg>',
  check: (c) => `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="${c}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><path d="M8 12.5l2.8 2.8L16.5 9.5"></path></svg>`,
  play: (c) => `<svg width="14" height="14" viewBox="0 0 24 24" fill="${c}"><path d="M6 4l14 8-14 8z"></path></svg>`,
  save: (c) => `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="${c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3h11l3 3v15H5z"></path><path d="M8 3v6h8V3M8 21v-7h8v7"></path></svg>`,
  chev: (dir, c) => `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="${c}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">${dir === 'l' ? '<path d="M15 5l-7 7 7 7"></path>' : dir === 'r' ? '<path d="M9 5l7 7-7 7"></path>' : dir === 'll' ? '<path d="M18 5l-7 7 7 7M11 5l-7 7 7 7"></path>' : '<path d="M6 5l7 7-7 7M13 5l7 7-7 7"></path>'}</svg>`,
  flip: (c) => `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="${c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9a8 8 0 0 1 14-3l2 2M20 4v4h-4M20 15a8 8 0 0 1-14 3l-2-2M4 20v-4h4"></path></svg>`,
  arrow: (c) => `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="${c}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"></path></svg>`,
  dot: (c) => `<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${c}"></span>`,
  pin: (c) => `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="${c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s-6-5.3-6-11a6 6 0 0 1 12 0c0 5.7-6 11-6 11z"></path><circle cx="12" cy="10" r="2"></circle></svg>`,
};

// ---------- shared chrome ----------
function chrome({ active, meta, body }) {
  const rail = [
    ['review', '리뷰', ico.review], ['openings', '오프닝', ico.openings], ['profile', '프로필', ico.profile], ['train', '훈련', ico.train],
  ].map(([key, label, svg]) => {
    const on = key === active;
    return `<div style="display: flex; flex-direction: column; align-items: center; gap: 4px; width: 52px; height: 56px; justify-content: center; border-radius: 10px; color: ${on ? T.ink : T.ink3}; background: ${on ? 'rgba(43, 38, 34, 0.08)' : 'transparent'}">${svg}<span style="font-size: 11px; font-weight: ${on ? 600 : 500}">${label}</span></div>`;
  }).join('');
  return `<div style="width: 1440px; height: 900px; background: ${T.paper}; display: flex; flex-direction: column; overflow: hidden">
  <div style="height: 52px; flex-shrink: 0; display: flex; align-items: center; gap: 16px; padding: 0 20px; border-bottom: 1px solid ${T.line}">
    <div class="display" style="font-size: 20px; font-weight: 700; letter-spacing: -0.02em">체스 튜터</div>
    <div style="width: 1px; height: 22px; background: ${T.lineStrong}"></div>
    ${meta}
    <div style="flex: 1"></div>
    <div class="btn btn-ghost" style="height: 32px">기보 가져오기</div>
    <div class="chip">래피드 1560</div>
    <div style="width: 32px; height: 32px; border-radius: 50%; background: ${T.ink}; color: ${T.paper}; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 600">나</div>
  </div>
  <div style="flex: 1; display: flex; min-height: 0">
    <div style="width: 64px; flex-shrink: 0; border-right: 1px solid ${T.line}; display: flex; flex-direction: column; align-items: center; gap: 6px; padding-top: 12px">${rail}</div>
    <div style="flex: 1; min-width: 0; display: flex; gap: 24px; padding: 20px 24px">${body}</div>
  </div>
</div>`;
}

// ---------- review screen pieces ----------
function evalBar(whiteShare, label, height) {
  return `<div style="width: 16px; height: ${height}px; border-radius: 4px; overflow: hidden; background: ${T.ink}; position: relative; flex-shrink: 0; box-shadow: 0 0 0 1px rgba(43, 38, 34, 0.25)">
    <div style="position: absolute; left: 0; right: 0; top: 0; height: ${Math.round(whiteShare * 100)}%; background: ${T.paper}"></div>
    <div class="mono" style="position: absolute; left: 0; right: 0; top: 6px; text-align: center; font-size: 9px; font-weight: 600; color: ${T.ink}; writing-mode: vertical-rl">${label}</div>
  </div>`;
}

function controls(current) {
  const b = (svg) => `<div style="width: 36px; height: 36px; border-radius: 8px; border: 1px solid ${T.lineStrong}; display: flex; align-items: center; justify-content: center; background: ${T.surface}">${svg}</div>`;
  return `<div style="display: flex; align-items: center; gap: 8px; height: 36px">
    ${b(ico.chev('ll', T.ink))}${b(ico.chev('l', T.ink))}${b(ico.chev('r', T.ink))}${b(ico.chev('rr', T.ink))}
    <div class="mv" style="margin-left: 8px; font-size: 15px">${current}</div>
    <div style="flex: 1"></div>
    <div class="btn btn-ghost" style="height: 32px; padding: 0 10px; font-weight: 500">${ico.flip(T.ink2)} 보드 뒤집기</div>
  </div>`;
}

const CLS = {
  best: `<span class="badge badge-good" style="height: 18px; padding: 0 6px; font-size: 11px">최선</span>`,
  good: `<span class="badge badge-neutral" style="height: 18px; padding: 0 6px; font-size: 11px">좋음</span>`,
  book: `<span class="badge badge-neutral" style="height: 18px; padding: 0 6px; font-size: 11px">책</span>`,
  inacc: `<span class="badge badge-neutral" style="height: 18px; padding: 0 6px; font-size: 11px; border: 1px dashed ${T.lineStrong}; background: transparent">부정확</span>`,
  blunder: `<span class="badge badge-bad" style="height: 18px; padding: 0 6px; font-size: 11px">블런더</span>`,
};

function moveList(rows, selectedIndex, note) {
  const r = rows.map((row, i) => {
    const cell = (m, sel) => m ? `<div style="display: flex; align-items: center; gap: 8px; height: 30px; padding: 0 8px; border-radius: 6px; background: ${sel ? 'rgba(43, 38, 34, 0.09)' : 'transparent'}; outline: ${sel ? `1.5px solid ${T.ink}` : 'none'}"><span class="mv" style="font-size: 13px">${m.san}</span>${m.cls ? CLS[m.cls] : ''}</div>` : '<div></div>';
    return `<div style="display: grid; grid-template-columns: 36px minmax(0, 1fr) minmax(0, 1fr); gap: 6px; align-items: center">
      <div class="mono faint" style="font-size: 12px; text-align: right; padding-right: 4px">${row.n}.</div>${cell(row.w, selectedIndex === i * 2)}${cell(row.b, selectedIndex === i * 2 + 1)}</div>`;
  }).join('');
  return `<div class="card" style="padding: 10px 12px; display: flex; flex-direction: column; gap: 2px">
    <div style="display: flex; align-items: center; gap: 8px; padding: 0 0 6px 0; border-bottom: 1px solid ${T.line}; margin-bottom: 4px"><span class="eyebrow">기보</span><span class="small muted">${note}</span></div>
    ${r}
  </div>`;
}

function sparkline(values, blunderIdx, width, height, caption) {
  const n = values.length; const mid = height / 2; const scale = (v) => mid - Math.max(-1, Math.min(1, v / 8)) * (mid - 6);
  const pts = values.map((v, i) => `${(i / (n - 1) * (width - 8) + 4).toFixed(1)},${scale(v).toFixed(1)}`);
  const area = `M4,${mid} L${pts.join(' L')} L${width - 4},${mid} Z`;
  let dot = '';
  if (blunderIdx != null) {
    const bx = blunderIdx / (n - 1) * (width - 8) + 4, by = scale(values[blunderIdx]);
    dot = `<circle cx="${bx.toFixed(1)}" cy="${by.toFixed(1)}" r="5" fill="${T.bad}" stroke="${T.surface}" stroke-width="2"></circle>`;
  }
  return `<div class="card" style="padding: 8px 12px 6px; display: flex; flex-direction: column; gap: 2px">
    <div style="display: flex; justify-content: space-between; align-items: center"><span class="eyebrow">평가 추이</span><span class="small muted">${caption}</span></div>
    <svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" style="display: block">
      <line x1="4" y1="${mid}" x2="${width - 4}" y2="${mid}" stroke="${T.lineStrong}" stroke-width="1"></line>
      <path d="${area}" fill="rgba(43, 38, 34, 0.10)"></path>
      <polyline points="${pts.join(' ')}" fill="none" stroke="${T.ink}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></polyline>
      ${dot}
    </svg>
  </div>`;
}

function panelTabs(active) {
  const tab = (label, on) => `<div style="height: 36px; display: flex; align-items: center; padding: 0 4px; margin-right: 22px; font-size: 14px; font-weight: ${on ? 700 : 500}; color: ${on ? T.ink : T.ink3}; border-bottom: 2px solid ${on ? T.ink : 'transparent'}">${label}</div>`;
  return `<div style="display: flex; border-bottom: 1px solid ${T.line}; margin: -4px 0 16px">${tab('이 수의 설명', active === 'move')}${tab('전략과 계획', active === 'plan')}${tab('국면 특징', false)}</div>`;
}

function verifyRow(claims, buttons) {
  return `<div style="display: flex; align-items: center; gap: 12px; padding-top: 12px; border-top: 1px solid ${T.line}; margin-top: 2px">
    <div style="flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; font-size: 12px; line-height: 1.4; color: ${T.ink2}">${ico.check(T.good)}<span><b style="color: ${T.ink}">검증됨</b> · 문장 속 ${claims}개 주장(칸·기물·공격 관계)이 보드와 일치</span></div>
    <div style="display: flex; gap: 8px; flex-shrink: 0">${buttons}</div>
  </div>`;
}

const MOVE_ROWS = [
  { n: 17, w: { san: 'Bg5', cls: 'good' }, b: { san: 'Rxd1+', cls: 'good' } },
  { n: 18, w: { san: 'Rxd1', cls: 'good' }, b: { san: 'Qc7', cls: 'good' } },
  { n: 19, w: { san: 'Be3', cls: 'inacc' }, b: { san: 'Qc6', cls: 'good' } },
  { n: 20, w: { san: 'Nd5', cls: 'best' }, b: { san: 'Qd7', cls: 'blunder' } },
  { n: 21, w: { san: 'Nxf6+', cls: 'best' }, b: { san: 'Bxf6', cls: 'good' } },
  { n: 22, w: { san: 'Rxd7', cls: 'best' }, b: { san: 'Rd8', cls: 'good' } },
];

// ---------- Artboard 1: review, tactical tab ----------
function mainArtboard() {
  const BOARD = 520;
  const fen = '5rk1/p2qbppp/1p2pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 w - - 1 21';
  const board = boardBlock(fen, {
    size: BOARD, flip: true, light: T.boardLight, dark: T.boardDark, ink: T.ink, paper: '#f8f3ea',
    lastMove: ['c6', 'd7'],
    arrows: [{ from: 'd5', to: 'f6', color: T.bad }, { from: 'd1', to: 'd7', color: T.ink, dashed: true }],
  });
  const evalVals = [0.2, 0.1, 0.3, 0.2, 0.4, 0.3, 0.5, 0.2, 0.3, 0.6, 0.4, 0.5, 0.7, 0.4, 0.3, 0.5, 0.6, 0.3, 0.7, 0.4, 5.6, 5.8, 6.4, 7.1, 7.6, 8.4, 9.0, 10.2, 11.5, 12.8, 14];
  const center = `<div style="width: 544px; flex-shrink: 0; display: flex; flex-direction: column; gap: 12px">
    <div style="display: flex; gap: 8px; align-items: stretch">${evalBar(0.86, '+5.6', BOARD)}${board}</div>
    ${controls('20… Qd7')}
    ${moveList(MOVE_ROWS, 7, '시실리안 헤지호그 · 책 이탈 12…Qc7 · 1–0 (31수)')}
    ${sparkline(evalVals, 20, 520, 44, '20…Qd7에서 +0.4 → +5.6')}
  </div>`;

  const line = (m) => `<span class="mv" style="font-size: 13px; padding: 2px 6px; border-radius: 5px; background: rgba(43, 38, 34, 0.06)">${m}</span>`;
  const branch = (bm, wm, res) => `<tr>
    <td style="padding: 5px 0; width: 90px">${line(bm)}</td>
    <td style="padding: 5px 0; width: 90px">${line(wm)}</td>
    <td style="padding: 5px 0; color: ${T.ink2}; font-size: 13px">${res}</td></tr>`;

  const panel = `<div class="card" style="flex: 1; min-width: 0; padding: 16px 22px 16px; overflow: auto; display: flex; flex-direction: column; gap: 13px">
    ${panelTabs('move')}
    <div style="display: flex; align-items: center; gap: 12px; margin-top: -6px">
      <div class="mv" style="font-size: 26px; letter-spacing: -0.01em">20… Qd7</div>
      <span class="badge badge-bad" style="height: 26px; padding: 0 10px; font-size: 13px">블런더</span>
      <div class="mono" style="font-size: 14px; color: ${T.ink2}">+0.4 → <b style="color: ${T.bad}">+5.6</b></div>
      <div style="flex: 1"></div>
      <span class="chip" style="border-style: dashed">1400–1600 구간의 34%가 두는 수</span>
    </div>
    <p style="margin: 0; font-size: 14.5px; line-height: 1.65; max-width: 640px">Nd5가 e7 비숍을 공격하자 퀸으로 지켰습니다. 자연스러운 반응이지만, 퀸이 d파일에 서면서 <b>Rd1과 같은 선</b>에 놓입니다. 지금은 Nd5가 사이를 가리고 있을 뿐입니다.</p>

    <div style="display: flex; flex-direction: column; gap: 10px">
      <div style="display: flex; align-items: baseline; gap: 10px"><span class="h3">무슨 일이 일어나는가</span><span class="small muted">반박 수순 · 클릭하면 보드에 재생</span></div>
      <div style="display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 8px; background: ${T.badBg}">
        <span class="mv" style="font-size: 15px; color: ${T.bad}">21. Nxf6+</span>
        <span style="font-size: 13.5px">체크를 주면서 비켜서고, 그 순간 Rd1이 Qd7을 겨냥합니다. 흑은 체크부터 처리해야 합니다.</span>
      </div>
      <table style="width: 100%; margin-left: 6px">
        ${branch('21… Bxf6', '22. Rxd7', '퀸 상실 · 퀸과 나이트 교환, 흑 −6')}
        ${branch('21… gxf6', '22. Rxd7', '같은 결과 · 킹 앞 폰까지 무너짐')}
        ${branch('21… Kh8', '22. Nxd7', '같은 결과 · 나이트가 퀸을 직접 잡음')}
      </table>
      <div style="display: flex; gap: 6px; flex-wrap: wrap; align-items: center"><span class="small faint" style="margin-right: 4px">모티프</span><span class="chip">디스커버드 어택</span><span class="chip">체크 템포</span><span class="chip">같은 선 위의 퀸과 룩</span></div>
      <div style="display: flex; gap: 10px; padding: 10px 12px; border-radius: 8px; border: 1px solid ${T.line}; background: ${T.paper}">
        <div style="font-size: 13px; line-height: 1.55"><b>왜 21.Nxe7+가 아니라 Nxf6+인가.</b> 21.Nxe7+에는 <span class="mv">Qxe7</span>이 있습니다. 퀸이 체크를 잡으면서 d파일도 벗어납니다. f6는 퀸이 닿지 않는 칸이라 같은 수법이 통합니다.</div>
      </div>
    </div>

    <div style="display: flex; flex-direction: column; gap: 8px">
      <span class="h3">대신 두었어야 할 수</span>
      <div style="display: grid; grid-template-columns: 120px 56px minmax(0, 1fr); gap: 6px 12px; align-items: center; font-size: 13px">
        <div style="display: flex; align-items: center; gap: 8px"><span class="mv" style="font-size: 14px">20… Nxd5</span>${CLS.best}</div><div class="mono" style="color: ${T.good}; font-weight: 600">+0.3</div><div class="muted">${line('21.exd5 exd5 22.Qd3 Rd8')} d5 나이트를 없애면 e7과 f6 위협이 함께 사라집니다.</div>
        <div style="display: flex; align-items: center; gap: 8px"><span class="mv" style="font-size: 14px">20… exd5</span></div><div class="mono" style="color: ${T.ink2}; font-weight: 600">+0.6</div><div class="muted">${line('21.exd5 Qd6 22.Bf4')} 나이트는 없앴지만 백 d5 통과폰이 생기고 퀸이 쫓깁니다.</div>
      </div>
    </div>

    <div style="display: flex; flex-direction: column; gap: 8px">
      <div style="display: flex; align-items: baseline; gap: 10px"><span class="h3">왜 Nxd5가 exd5보다 나은가</span><span class="small muted">분기점 · 21.exd5 이후 국면 비교</span></div>
      <table style="width: 100%; font-size: 13px">
        <tr style="color: ${T.ink3}; font-size: 11.5px; font-weight: 600"><td style="padding: 4px 0; width: 90px">항목</td><td style="padding: 4px 8px; border-left: 2px solid ${T.good}">…Nxd5 후</td><td style="padding: 4px 8px; border-left: 2px solid ${T.lineStrong}">…exd5 후</td></tr>
        <tr><td style="padding: 6px 0; font-weight: 600">폰 구조</td><td style="padding: 6px 8px; border-left: 2px solid ${T.good}">e6 폰이 남아 d5 폰을 곧 교환. 열린 d파일을 흑도 씀</td><td style="padding: 6px 8px; border-left: 2px solid ${T.lineStrong}">백 d5 통과폰(고립)이 고정. Rd1이 뒤에서 지원</td></tr>
        <tr><td style="padding: 6px 0; font-weight: 600">기물 활동</td><td style="padding: 6px 8px; border-left: 2px solid ${T.good}">Be7·Rf8이 d파일로 나옴</td><td style="padding: 6px 8px; border-left: 2px solid ${T.lineStrong}">Qc6가 폰에 쫓겨 템포 손실, Bf4가 또 쫓음</td></tr>
        <tr><td style="padding: 6px 0; font-weight: 600">킹 안전</td><td style="padding: 6px 8px; border-left: 2px solid ${T.good}">변화 없음</td><td style="padding: 6px 8px; border-left: 2px solid ${T.lineStrong}">변화 없음</td></tr>
      </table>
    </div>

    ${verifyRow(7, `<div class="btn btn-primary">${ico.play(T.paper)} 이 국면에서 이어 두기 · Maia 1500</div><div class="btn btn-ghost">${ico.save(T.ink)} 퍼즐로 저장</div>`)}
  </div>`;

  const meta = `<div style="display: flex; align-items: center; gap: 10px; font-size: 13px; color: ${T.ink2}"><span style="color: ${T.ink}; font-weight: 600">게임 리뷰</span><span>래피드 10+0 · 2026-08-29 · 흑 · 1–0</span><span class="chip" style="height: 22px">상대 1548</span></div>`;
  return HEAD + chrome({ active: 'review', meta, body: center + panel }) + FOOT;
}

// ---------- Artboard 2: review, strategy tab (move 14) ----------
function strategyArtboard() {
  const BOARD = 520;
  const fen = '2r2rk1/pbqnbppp/1p1ppn2/8/2PNP3/2N1BB2/PP1Q1PPP/2RR2K1 b - - 4 14';
  const board = boardBlock(fen, {
    size: BOARD, flip: true, light: T.boardLight, dark: T.boardDark, ink: T.ink, paper: '#f8f3ea',
    marks: [{ sq: 'a7', color: T.ink }, { sq: 'b6', color: T.ink }, { sq: 'd6', color: T.ink }, { sq: 'e6', color: T.ink }],
    arrows: [{ from: 'd6', to: 'd5', color: T.good }, { from: 'b6', to: 'b5', color: T.good }, { from: 'e4', to: 'e5', color: T.bad, dashed: true }, { from: 'c3', to: 'd5', color: T.bad, dashed: true }],
  });
  const rows = [
    { n: 12, w: { san: 'Qd2', cls: 'book' }, b: { san: 'Qc7', cls: 'good' } },
    { n: 13, w: { san: 'Rac1', cls: 'good' }, b: { san: 'Rac8', cls: 'good' } },
    { n: 14, w: { san: 'Rfd1', cls: 'good' }, b: { san: 'Qb8', cls: 'best' } },
    { n: 15, w: { san: 'Qe1', cls: 'inacc' }, b: { san: 'Rfd8', cls: 'good' } },
    { n: 16, w: { san: 'Bg5', cls: 'good' }, b: { san: 'Ne5', cls: 'best' } },
    { n: 17, w: { san: 'Be2', cls: 'good' }, b: { san: 'Ng6', cls: 'good' } },
  ];
  const evalVals = [0.2, 0.1, 0.3, 0.2, 0.4, 0.3, 0.5, 0.2, 0.3, 0.4, 0.3, 0.2, 0.3, 0.2, 0.1, 0.3, 0.1, 0.2, 0.1, -0.2, -0.1, 0.1, 0.0, -0.3, -0.2, 0.0, 0.1, -0.1, 0.0, 0.1, 0.0, 0.0, 0.1, 0.0, 0.0, 0.0, 0.0];
  const center = `<div style="width: 544px; flex-shrink: 0; display: flex; flex-direction: column; gap: 12px">
    <div style="display: flex; gap: 8px; align-items: stretch">${evalBar(0.52, '+0.2', BOARD)}${board}</div>
    ${controls('14. Rfd1 · 흑 차례')}
    ${moveList(rows, 4, '시실리안 헤지호그 · 책 이탈 12…Qc7 · ½–½ (37수)')}
    ${sparkline(evalVals, null, 520, 44, '큰 실수 없이 균형 유지 · 18…d5 브레이크 이후 +0.1')}
  </div>`;

  const legend = (color, label, dashed) => `<span style="display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: ${T.ink2}"><span style="width: 18px; height: 0; border-top: 3px ${dashed ? 'dashed' : 'solid'} ${color}"></span>${label}</span>`;
  const planRow = (title, cond, status) => `<div style="display: flex; flex-direction: column; gap: 2px; padding: 8px 0; border-bottom: 1px solid ${T.line}">
      <div style="display: flex; align-items: center; gap: 8px; font-size: 13.5px; font-weight: 600">${title}<div style="flex: 1"></div>${status}</div>
      <div style="font-size: 12.5px; color: ${T.ink2}; line-height: 1.5">${cond}</div>
    </div>`;
  const pvMatch = `<span class="badge badge-good" style="height: 18px; padding: 0 6px; font-size: 11px; font-weight: 600">${ico.check(T.good)} 엔진 PV 일치</span>`;
  const later = `<span class="badge badge-neutral" style="height: 18px; padding: 0 6px; font-size: 11px; font-weight: 600">아직 이름</span>`;

  const panel = `<div class="card" style="flex: 1; min-width: 0; padding: 16px 22px 16px; overflow: auto; display: flex; flex-direction: column; gap: 12px">
    ${panelTabs('plan')}
    <div style="display: flex; align-items: center; gap: 12px; margin-top: -6px">
      <div class="display" style="font-size: 24px; font-weight: 700; letter-spacing: -0.02em">헤지호그 구조</div>
      <span class="chip">흑: a7 b6 d6 e6</span><span class="chip">백: c4 + e4 바인드</span>
      <div style="flex: 1"></div>
      <span class="small faint">분류 확신 0.92</span>
    </div>
    <div style="display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: ${T.ink2}">
      <span class="eyebrow" style="margin-right: 4px">구조 흐름</span>
      <span class="chip" style="height: 22px">오픈 시실리안 1–9수</span>${ico.arrow(T.ink3)}<span class="chip" style="height: 22px; border-color: ${T.ink}; color: ${T.ink}; font-weight: 600">헤지호그 10–18수</span>${ico.arrow(T.ink3)}<span class="chip" style="height: 22px">오픈 센터 19수~</span>
    </div>
    <p style="margin: 0; font-size: 14.5px; line-height: 1.65; max-width: 660px">흑은 3열로 웅크리고 있지만 수동적인 구조가 아닙니다. 폰 브레이크 <b>…d5</b>와 <b>…b5</b>가 언제든 터질 수 있다는 압박이 백의 기물을 묶습니다. 이 구조에서 승부는 브레이크의 <b>타이밍</b>에서 갈립니다.</p>
    <div style="display: flex; gap: 16px">${legend(T.good, '흑의 브레이크')}${legend(T.bad, '백의 계획', true)}<span style="display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: ${T.ink2}"><span style="width: 14px; height: 14px; border: 2.5px solid ${T.ink}; border-radius: 3px"></span>구조를 정의하는 폰</span></div>

    <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px">
      <div>
        <div class="h3" style="margin-bottom: 4px">흑의 계획</div>
        ${planRow('…d5 브레이크', 'd5 칸 통제력이 앞설 때. 지금은 공격 2(Nf6, Bb7) 대 방어 2(Nc3, Bf3)로 시기상조', pvMatch)}
        ${planRow('…b5 브레이크', 'c4 폰을 흔들어 퀸사이드를 엽니다. …a6 없이는 cxb5 뒤 되잡을 수 없으니 준비가 먼저', later)}
        ${planRow('…Ne5로 Bf3 교환 유도', 'd5를 지키는 비숍을 빼면 d5 브레이크의 계산이 흑에게 기웁니다', pvMatch)}
      </div>
      <div>
        <div class="h3" style="margin-bottom: 4px">백의 계획</div>
        ${planRow('e4–e5 밀어내기', '…d5를 영구히 막고 공간을 넓힘. f4로 지원할 때 강력', later)}
        ${planRow('Nd5 희생', 'e7·f6를 동시에 건드림. 8/29 게임 20수에서 당신이 실제로 당한 수입니다', pvMatch)}
        ${planRow('킹사이드 공간 확장', 'g4–g5로 Nf6를 밀어내면 d5의 흑 공격수가 하나 줄어듭니다', later)}
      </div>
    </div>

    <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px">
      <div style="padding: 12px 14px; border-radius: 8px; background: ${T.goodBg}; display: flex; flex-direction: column; gap: 4px">
        <div style="display: flex; align-items: center; gap: 8px"><span class="eyebrow" style="color: ${T.good}">당신이 한 것</span><span class="mv" style="font-size: 14px">14… Qb8</span>${CLS.best}</div>
        <div style="font-size: 13px; line-height: 1.55">Bb7과 함께 e4를 겨냥하는 정석 배치입니다. 계획 <b>…Ne5 → …d5</b>의 첫 수로 엔진 수순과 일치합니다. 실제 브레이크 18…d5는 준비가 끝난 뒤였고 타이밍이 적절했습니다(+0.1).</div>
      </div>
      <div style="padding: 12px 14px; border-radius: 8px; border: 1px solid ${T.line}; background: ${T.paper}; display: flex; flex-direction: column; gap: 4px">
        <div style="display: flex; align-items: center; gap: 8px"><span class="eyebrow">반사실 · 지금 14…d5를 두면?</span></div>
        <div class="mv" style="font-size: 12.5px; color: ${T.ink2}">15.cxd5 exd5 16.exd5 Nxd5 17.Nxd5 Bxd5 18.Bxd5</div>
        <div style="font-size: 13px; line-height: 1.55">d5에서 공격 2 대 방어 2. 마지막 교환에서 흑이 <b>기물을 폰과 바꾸게 됩니다</b>(−2.1). 브레이크는 준비가 먼저입니다.</div>
      </div>
    </div>

    <div style="display: flex; align-items: center; gap: 14px; padding: 10px 14px; border-radius: 8px; border: 1px dashed ${T.lineStrong}; font-size: 13px">
      <span class="eyebrow">이 구조에서 당신의 기록</span>
      <span><b>12판</b> · 승률 <b style="color: ${T.bad}">33%</b></span>
      <span class="muted">평균 …d5 브레이크 시점 <b style="color: ${T.ink}">21수</b>, 1400–1600 구간 평균 16수</span>
      <div style="flex: 1"></div><a href="#">구조별 리포트 보기</a>
    </div>
    ${verifyRow(9, `<div class="btn btn-primary">${ico.play(T.paper)} 이 국면에서 이어 두기 · Maia 1500</div><div class="btn btn-ghost">헤지호그 스터디 열기</div>`)}
  </div>`;
  const meta = `<div style="display: flex; align-items: center; gap: 10px; font-size: 13px; color: ${T.ink2}"><span style="color: ${T.ink}; font-weight: 600">게임 리뷰</span><span>래피드 10+0 · 2026-08-22 · 흑 · ½–½</span><span class="chip" style="height: 22px">상대 1571</span></div>`;
  return HEAD + chrome({ active: 'review', meta, body: center + panel }) + FOOT;
}

// ---------- Artboard 3: profile / weakness report ----------
function profileArtboard() {
  const bar = (label, value, max, right, color = T.ink) => `<div style="display: grid; grid-template-columns: 130px minmax(0, 1fr) 56px; gap: 10px; align-items: center; font-size: 13px">
    <div style="font-weight: 500">${label}</div>
    <div style="height: 10px; border-radius: 4px; background: rgba(43, 38, 34, 0.06); overflow: hidden"><div style="width: ${Math.round(value / max * 100)}%; height: 100%; background: ${color}; border-radius: 0 4px 4px 0"></div></div>
    <div class="mono muted" style="text-align: right">${right}</div>
  </div>`;
  const tile = (label, value, delta, deltaColor) => `<div style="flex: 1; padding: 12px 14px; border-radius: 8px; background: ${T.paper}; border: 1px solid ${T.line}">
    <div class="small muted">${label}</div>
    <div class="mono" style="font-size: 30px; font-weight: 600; line-height: 1.1; margin-top: 2px">${value}</div>
    <div class="mono small" style="color: ${deltaColor}; margin-top: 4px">${delta} <span class="faint">vs 같은 구간</span></div>
  </div>`;
  const card = (title, sub, body, extra = '') => `<div class="card" style="padding: 16px 18px; display: flex; flex-direction: column; gap: 12px; ${extra}">
    <div style="display: flex; align-items: baseline; gap: 10px"><span class="h3">${title}</span><span class="small muted">${sub}</span></div>${body}</div>`;

  const structRows = [
    ['헤지호그', 12, 33, 142], ['오픈 센터', 15, 47, 88], ['IQP · 상대 보유', 11, 45, 95], ['프렌치 구조', 8, 50, 70], ['카로칸 어드밴스', 9, 61, 40],
  ].map(([s, n, w, loss]) => `<tr style="font-size: 13px">
      <td style="padding: 6px 0; font-weight: 500">${s}</td>
      <td class="mono muted" style="padding: 6px 8px; text-align: right">${n}판</td>
      <td class="mono" style="padding: 6px 8px; text-align: right; color: ${w < 45 ? T.bad : T.ink}; font-weight: 600">${w}%</td>
      <td style="padding: 6px 0 6px 8px"><div style="display: flex; align-items: center; gap: 8px"><div style="width: ${Math.round(loss / 150 * 120)}px; height: 8px; border-radius: 4px; background: ${loss > 120 ? T.bad : T.ink3}"></div><span class="mono small muted">−${loss}</span></div></td>
    </tr>`).join('');

  const body = `<div style="flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 16px">
    <div style="display: flex; align-items: flex-end; gap: 14px">
      <div class="display" style="font-size: 26px; font-weight: 700; letter-spacing: -0.02em">약점 리포트</div>
      <div class="muted" style="font-size: 13px; padding-bottom: 4px">최근 60판 · 2026-07-01 ~ 08-30 · chess.com 래피드</div>
      <div style="flex: 1"></div>
      <div class="chip">래피드 1560 <span class="mono" style="color: ${T.good}">▲34</span></div>
      <div class="chip">블리츠 1482 <span class="mono" style="color: ${T.bad}">▼12</span></div>
    </div>
    <div style="display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr); gap: 16px">
      ${card('튜터의 요약', '통계를 설명과 연결합니다', `<div style="font-size: 14.5px; line-height: 1.7; max-width: 720px">지난 두 달 동안 가장 큰 손실은 <b>헤지호그 구조에서 …d5 브레이크의 타이밍</b>이었습니다. 12판 중 8판에서 브레이크가 평균 5수 늦었고, 그 사이 백이 e5나 g4로 먼저 움직였습니다. 전술에서는 <b>나이트가 체크와 함께 비켜서는 디스커버드 어택</b>을 17번 놓쳤습니다. 이 둘은 같은 게임에서 자주 겹칩니다. 미들게임 정확도가 같은 구간보다 6점 낮은 이유의 대부분이 여기 있습니다.</div>
        <div style="display: flex; gap: 8px"><a href="#" style="font-size: 13px">관련 게임 9판 보기</a><span class="faint">·</span><a href="#" style="font-size: 13px">헤지호그 계획 다시 읽기</a></div>`)}
      ${card('단계별 정확도', '100점 만점', `<div style="display: flex; gap: 10px">${tile('오프닝', '84', '+2', T.good)}${tile('미들게임', '71', '−6', T.bad)}${tile('엔드게임', '63', '−3', T.bad)}</div>`)}
    </div>
    <div style="display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr) minmax(0, 0.9fr); gap: 16px">
      ${card('구조별 성적', '판수 · 승률 · 판당 평균 손실(센티폰)', `<table style="width: 100%">${structRows}</table>`)}
      ${card('놓친 전술 모티프', '리뷰에서 태그된 횟수', `<div style="display: flex; flex-direction: column; gap: 9px">${bar('디스커버드 어택', 17, 17, '17회', T.bad)}${bar('나이트 포크', 14, 17, '14회')}${bar('핀', 9, 17, '9회')}${bar('백랭크', 6, 17, '6회')}${bar('수비수 제거', 4, 17, '4회')}</div>`)}
      ${card('시간 관리', '남은 시간과 실수의 관계', `<div style="display: flex; flex-direction: column; gap: 10px">
          <div style="font-size: 13px; line-height: 1.55">30초 미만에 둔 수의 블런더율이 <b style="color: ${T.bad}">18%</b>입니다. 같은 구간 기준 9%. 헤지호그에서 20수 전후에 시간이 몰립니다.</div>
          ${bar('30초 미만', 18, 20, '18%', T.bad)}${bar('30초 이상', 6, 20, '6%')}${bar('구간 기준', 9, 20, '9%', T.ink3)}
        </div>`)}
    </div>
    <div style="display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 16px">
      ${card('오늘의 훈련', '내 기보에서 만든 문제 · 간격 반복', `<div style="display: flex; gap: 10px">
          <div style="flex: 1; padding: 12px 14px; border-radius: 8px; border: 1px solid ${T.line}; background: ${T.paper}; display: flex; flex-direction: column; gap: 6px"><div class="small muted">복습 예정</div><div style="font-weight: 600">내 기보 퍼즐 <span class="mono">7</span>개</div><div class="small muted">8/29 리뷰의 20…Qd7 포함</div><div class="btn btn-primary" style="height: 32px; margin-top: 4px">시작</div></div>
          <div style="flex: 1; padding: 12px 14px; border-radius: 8px; border: 1px solid ${T.line}; background: ${T.paper}; display: flex; flex-direction: column; gap: 6px"><div class="small muted">모티프 세트</div><div style="font-weight: 600">디스커버드 어택 <span class="mono">12</span>문제</div><div class="small muted">Lichess 퍼즐 DB에서 선별</div><div class="btn btn-ghost" style="height: 32px; margin-top: 4px">시작</div></div>
          <div style="flex: 1; padding: 12px 14px; border-radius: 8px; border: 1px solid ${T.line}; background: ${T.paper}; display: flex; flex-direction: column; gap: 6px"><div class="small muted">구조 스터디</div><div style="font-weight: 600">헤지호그 …d5 타이밍</div><div class="small muted">내 게임 3국면 + 마스터 3국면</div><div class="btn btn-ghost" style="height: 32px; margin-top: 4px">열기</div></div>
        </div>`)}
      ${card('레퍼토리 구멍', '책에서 벗어난 지점과 그 뒤의 손실', `<div style="display: flex; flex-direction: column; gap: 8px; font-size: 13px">
          <div style="display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 6px; background: ${T.paper}; border: 1px solid ${T.line}"><span class="mv">1.d4 상대</span><span class="muted">12수 이전 책 이탈</span><span class="mono" style="color: ${T.bad}; font-weight: 600">61%</span><div style="flex: 1"></div><span class="mono muted">평균 −0.8</span></div>
          <div style="display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 6px; background: ${T.paper}; border: 1px solid ${T.line}"><span class="mv">1.e4 c5 2.c3</span><span class="muted">알라핀 · 4판</span><span class="mono" style="color: ${T.bad}; font-weight: 600">0승</span><div style="flex: 1"></div><a href="#">오프닝 지도에서 보기</a></div>
        </div>`)}
    </div>
  </div>`;
  const meta = `<div style="display: flex; align-items: center; gap: 10px; font-size: 13px; color: ${T.ink2}"><span style="color: ${T.ink}; font-weight: 600">프로필</span><span>chess.com 연동 · 2,341판 가져옴 · 마지막 동기화 오늘 09:12</span></div>`;
  return HEAD + chrome({ active: 'profile', meta, body }) + FOOT;
}

// ---------- Artboard 4: openings map ----------
function lerp(a, b, t) { return Math.round(a + (b - a) * t); }
function hexToRgb(h) { return [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)); }
function scoreColor(score) { // 0..1 win rate -> diverging bad / gray / good around 0.5
  const g = hexToRgb('#9b9187'), b = hexToRgb(T.bad), gd = hexToRgb(T.good);
  const t = Math.max(-1, Math.min(1, (score - 0.5) / 0.2));
  const from = g, to = t < 0 ? b : gd, k = Math.abs(t);
  return `rgb(${lerp(from[0], to[0], k)}, ${lerp(from[1], to[1], k)}, ${lerp(from[2], to[2], k)})`;
}
function textW(s) { let w = 0; for (const ch of s) w += /[ᄀ-ᇿ㄰-㆏가-힯一-鿿]/.test(ch) ? 12.5 : 7.2; return w; }
// Opening DAG: every node carries a board snapshot of the position it stands for.
function dag() {
  const W = 856, H = 610, MINI = 84;
  const nodes = {
    root: { x: 0, y: 248, san: '1.e4 c5', n: 214, fen: 'rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2' },
    nf3: { x: 175, y: 100, san: '2.Nf3', n: 171, fen: 'rnbqkbnr/pp1ppppp/8/2p5/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2' },
    c3: { x: 175, y: 330, san: '2.c3 알라핀', n: 18, fen: 'rnbqkbnr/pp1ppppp/8/2p5/4P3/2P5/PP1P1PPP/RNBQKBNR b KQkq - 0 2' },
    nc3: { x: 175, y: 470, san: '2.Nc3 클로즈드', n: 25, fen: 'rnbqkbnr/pp1ppppp/8/2p5/4P3/2N5/PPPP1PPP/R1BQKBNR b KQkq - 1 2' },
    e6: { x: 350, y: 0, san: '2…e6', n: 96, fen: 'rnbqkbnr/pp1p1ppp/4p3/2p5/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 3' },
    d6: { x: 350, y: 150, san: '2…d6', n: 52, fen: 'rnbqkbnr/pp2pppp/3p4/2p5/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 3' },
    nc6: { x: 350, y: 300, san: '2…Nc6', n: 23, fen: 'r1bqkbnr/pp1ppppp/2n5/2p5/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3' },
    ala: { x: 350, y: 450, san: '2…d5 3.exd5 Qxd5', n: 12, fen: 'rnb1kbnr/pp2pppp/8/2pq4/8/2P5/PP1P1PPP/RNBQKBNR w KQkq - 0 4' },
    d4e6: { x: 525, y: 0, san: '3.d4 cxd4 4.Nxd4', n: 88, fen: 'rnbqkbnr/pp1p1ppp/4p3/8/3NP3/8/PPP2PPP/RNBQKB1R b KQkq - 0 4' },
    bb5: { x: 525, y: 150, san: '3.Bb5+ 모스크바', n: 14, fen: 'rnbqkbnr/pp2pppp/3p4/1Bp5/4P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 1 3', master: true },
    d4d6: { x: 525, y: 300, san: '3.d4 cxd4 4.Nxd4 Nf6', n: 49, fen: 'rnbqkb1r/pp2pppp/3p1n2/8/3NP3/8/PPP2PPP/RNBQKB1R w KQkq - 1 5' },
    sche: { x: 700, y: 0, san: '셰베닝겐 타비야', n: 91, fen: 'rnbq1rk1/pp2bppp/3ppn2/8/3NP3/2N5/PPP1BPPP/R1BQ1RK1 w - - 4 8', tabiya: true },
    hedge: { x: 700, y: 190, san: '헤지호그 타비야', n: 31, fen: 'r2q1rk1/pb1nbppp/1p1ppn2/8/2PNP3/2N1BB2/PP3PPP/R2Q1RK1 w - - 0 12', tabiya: true },
    dev: { x: 700, y: 380, san: '12…Qc7 책 이탈', n: 31, fen: 'r4rk1/pbqnbppp/1p1ppn2/8/2PNP3/2N1BB2/PP1Q1PPP/R4RK1 w - - 2 13', deviation: true },
  };
  const edges = [
    ['root', 'nf3', 171, 0.49], ['root', 'c3', 18, 0.36], ['root', 'nc3', 25, 0.52],
    ['nf3', 'e6', 96, 0.50], ['nf3', 'd6', 52, 0.47], ['nf3', 'nc6', 23, 0.55],
    ['e6', 'd4e6', 88, 0.50], ['d6', 'd4d6', 49, 0.47], ['d6', 'bb5', 0, 0.5, true],
    ['d4e6', 'sche', 60, 0.53], ['d4d6', 'sche', 31, 0.48], ['d4e6', 'hedge', 10, 0.40],
    ['sche', 'hedge', 21, 0.33], ['hedge', 'dev', 31, 0.35],
    ['c3', 'ala', 12, 0.25],
  ];
  const nodeW = (k) => Math.min(156, Math.max(124, textW(nodes[k].san) + 26));
  const nodeH = 122;
  const paths = edges.map(([a, b, n, s, master]) => {
    const A = nodes[a], B = nodes[b];
    const w = master ? 1.5 : Math.max(2, Math.min(11, n / 16));
    const stroke = master ? T.ink3 : scoreColor(s);
    let d;
    if (B.x - (A.x + nodeW(a)) < 30) { // same column: bottom-centre of A to top-centre of B
      const x1 = A.x + nodeW(a) / 2, y1 = A.y + nodeH, x2 = B.x + nodeW(b) / 2, y2 = B.y;
      const my = (y1 + y2) / 2;
      d = `M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`;
    } else {
      const x1 = A.x + nodeW(a), y1 = A.y + MINI / 2 + 8, x2 = B.x, y2 = B.y + MINI / 2 + 8;
      const mx = (x1 + x2) / 2;
      d = `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
    }
    return `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="${w}" stroke-linecap="round" opacity="${master ? 0.7 : 0.85}"${master ? ' stroke-dasharray="4 5"' : ''}></path>`;
  }).join('');
  const cards = Object.entries(nodes).map(([k, v]) => {
    const w = nodeW(k);
    const border = v.deviation ? `1.5px solid ${T.bad}` : v.tabiya ? `1.5px solid ${T.ink}` : `1px solid ${T.lineStrong}`;
    const bg = v.deviation ? T.badBg : v.tabiya ? 'rgba(43, 38, 34, 0.06)' : T.surface;
    const mini = boardBlock(v.fen, { size: MINI, flip: true, coords: false, light: T.boardLight, dark: T.boardDark, ink: T.ink, paper: '#f8f3ea' });
    return `<div style="position: absolute; left: ${v.x}px; top: ${v.y}px; width: ${w}px; height: ${nodeH}px; padding: 8px 8px 6px; border-radius: 8px; background: ${bg}; border: ${border}; display: flex; flex-direction: column; align-items: center; gap: 5px">
      ${mini}
      <div style="display: flex; align-items: baseline; gap: 6px; width: 100%; justify-content: center"><span class="mv" style="font-size: 11.5px; line-height: 1.2; text-align: center; color: ${v.deviation ? T.bad : T.ink}">${v.san}</span><span class="mono" style="font-size: 10.5px; color: ${T.ink3}">${v.master ? '마스터' : v.n}</span></div>
    </div>`;
  }).join('');
  const devNote = `<div style="position: absolute; left: ${nodes.dev.x}px; top: ${nodes.dev.y + nodeH + 8}px; width: 156px; font-size: 11.5px; line-height: 1.4; color: ${T.ink2}"><b style="color: ${T.ink}">31판 중 20판 패배.</b> 마스터 DB의 주류는 12…Qb8 (67%)</div>`;
  return `<div style="position: relative; width: ${W}px; height: ${H}px">
    <svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" style="position: absolute; inset: 0; display: block">${paths}</svg>
    ${cards}${devNote}
  </div>`;
}

function openingsArtboard() {
  const legend = `<div style="display: flex; align-items: center; gap: 18px; font-size: 12px; color: ${T.ink2}; padding-top: 10px; border-top: 1px solid ${T.line}">
    <span style="display: inline-flex; align-items: center; gap: 6px"><span style="width: 22px; height: 0; border-top: 6px solid ${T.ink3}; border-radius: 3px"></span>굵기 = 내 게임 수</span>
    <span style="display: inline-flex; align-items: center; gap: 6px"><span style="width: 12px; height: 12px; border-radius: 3px; background: ${T.bad}"></span>내 승률 낮음</span>
    <span style="display: inline-flex; align-items: center; gap: 6px"><span style="width: 12px; height: 12px; border-radius: 3px; background: #9b9187"></span>50%</span>
    <span style="display: inline-flex; align-items: center; gap: 6px"><span style="width: 12px; height: 12px; border-radius: 3px; background: ${T.good}"></span>내 승률 높음</span>
    <span style="display: inline-flex; align-items: center; gap: 6px"><span style="width: 22px; height: 0; border-top: 2px dashed ${T.ink3}"></span>마스터 DB에만 있는 가지</span>
    <span style="display: inline-flex; align-items: center; gap: 6px"><span style="width: 14px; height: 14px; border-radius: 3px; background: ${T.ink}"></span>타비야 (전이 병합)</span>
  </div>`;
  const heatBoard = boardBlock('8/8/8/8/8/8/8/8 w - - 0 1', {
    size: 232, flip: true, coords: true, pieces: false, light: T.boardLight, dark: T.boardDark,
    heat: { e7: 0.74, g7: 0.12, d6: 0.05, c5: 0.04, f8: 0.03, b4: 0.02 },
  });
  const heatLabels = [['e7', '74%'], ['g7', '12%'], ['d6', '5%'], ['c5', '4%']].map(([s, p]) => `<div style="display: flex; justify-content: space-between; font-size: 12.5px"><span class="mv">${s}</span><span class="mono muted">${p}</span></div>`).join('');
  const hist = (label, who, bins, mine, master, color) => {
    const max = Math.max(...bins);
    const bars = bins.map((b, i) => `<rect x="${i * 11}" y="${(28 - b / max * 26).toFixed(1)}" width="9" height="${(b / max * 26).toFixed(1)}" rx="2" fill="${color}" opacity="${0.45 + b / max * 0.5}"></rect>`).join('');
    const mx = (mine - 10) * 11 + 4.5, sx = (master - 10) * 11 + 4.5;
    return `<div style="display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 10px; align-items: center">
      <div><div class="mv" style="font-size: 13px">${label}</div><div class="small faint">${who}</div></div>
      <svg viewBox="0 0 232 36" width="232" height="36" style="display: block">${bars}
        <line x1="${sx}" y1="0" x2="${sx}" y2="30" stroke="${T.ink3}" stroke-width="1.5" stroke-dasharray="2 2"></line>
        <line x1="${mx}" y1="0" x2="${mx}" y2="30" stroke="${T.ink}" stroke-width="2"></line>
        <text x="0" y="35.5" font-family="${FONT_MONO}" font-size="8.5" fill="${T.ink3}">10수</text><text x="232" y="35.5" text-anchor="end" font-family="${FONT_MONO}" font-size="8.5" fill="${T.ink3}">30수</text>
      </svg></div>`;
  };
  const body = `<div style="flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 14px">
    <div style="display: flex; align-items: center; gap: 12px">
      <div class="display" style="font-size: 26px; font-weight: 700; letter-spacing: -0.02em">오프닝 지도</div>
      <span class="muted" style="font-size: 13px">흑 · 시실리안 · 내 214판 위에 마스터 DB를 겹침</span>
      <div style="flex: 1"></div>
      <div class="chip">마스터 DB · 2,400+ ▾</div><div class="chip">1400–1600 구간 ▾</div>
      <div class="chip" style="border-color: ${T.ink}; color: ${T.ink}; font-weight: 600"><span style="width: 8px; height: 8px; border-radius: 50%; background: ${T.good}"></span>내 기보 오버레이</div>
      <div class="chip">깊이 12플라이 ▾</div>
    </div>
    <div style="flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 420px; gap: 16px">
      <div class="card" style="padding: 16px 18px; display: flex; flex-direction: column; gap: 6px; min-height: 0">
        <div style="display: flex; align-items: baseline; gap: 10px"><span class="h3">가지와 합류</span><span class="small muted">같은 구조로 합쳐지는 수순은 하나의 타비야 노드로 병합</span></div>
        <div style="flex: 1; min-height: 0; overflow: hidden">${dag()}</div>
        ${legend}
      </div>
      <div style="display: flex; flex-direction: column; gap: 16px; min-height: 0">
        <div class="card" style="padding: 16px 18px; display: flex; flex-direction: column; gap: 10px">
          <div style="display: flex; align-items: center; gap: 10px"><span class="h3">기물 목적지</span><span class="small muted">15수까지 이 기물이 놓인 칸</span><div style="flex: 1"></div><span class="chip" style="height: 24px">흑 f8 비숍 ▾</span></div>
          <div style="display: flex; gap: 14px; align-items: flex-start">
            ${heatBoard}
            <div style="flex: 1; display: flex; flex-direction: column; gap: 6px; padding-top: 4px">
              <div class="small muted">셰베닝겐 · 마스터 3,412판</div>${heatLabels}
              <div style="font-size: 12.5px; line-height: 1.5; margin-top: 6px; color: ${T.ink2}">e7에 두고 …Rfd8과 함께 d파일을 준비하는 것이 주류. g7로 가는 12%는 다른 구조(드래곤형)로 전이.</div>
            </div>
          </div>
        </div>
        <div class="card" style="padding: 16px 18px; display: flex; flex-direction: column; gap: 10px">
          <div style="display: flex; align-items: center; gap: 10px"><span class="h3">폰 브레이크 시점</span><span class="small muted">헤지호그 · 마스터 분포</span><div style="flex: 1"></div><span style="display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; color: ${T.ink2}"><span style="width: 2px; height: 12px; background: ${T.ink}"></span>내 평균</span><span style="display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; color: ${T.ink2}"><span style="width: 0; height: 12px; border-left: 2px dashed ${T.ink3}"></span>마스터 중앙값</span></div>
          ${hist('…d5', '흑', [0, 1, 2, 4, 7, 9, 12, 10, 8, 6, 5, 3, 3, 2, 2, 1, 1, 1, 1, 0, 0], 21, 16, T.good)}
          ${hist('…b5', '흑', [0, 0, 1, 3, 5, 8, 9, 9, 7, 6, 4, 3, 2, 2, 1, 1, 1, 0, 0, 0, 0], 19, 17, T.good)}
          ${hist('e4–e5', '백', [0, 0, 0, 1, 2, 3, 5, 7, 8, 8, 7, 5, 4, 3, 2, 2, 1, 1, 0, 0, 0], 18, 19, T.bad)}
          ${hist('g2–g4', '백', [0, 0, 0, 0, 1, 2, 4, 6, 8, 9, 8, 6, 4, 3, 2, 1, 1, 0, 0, 0, 0], 17, 20, T.bad)}
        </div>
      </div>
    </div>
  </div>`;
  const meta = `<div style="display: flex; align-items: center; gap: 10px; font-size: 13px; color: ${T.ink2}"><span style="color: ${T.ink}; font-weight: 600">오프닝</span><span>레퍼토리 · 흑 1.e4 c5</span></div>`;
  return HEAD + chrome({ active: 'openings', meta, body }) + FOOT;
}

// ---------- Low-fi direction alternates ----------
function wire(title, tagline, inner) {
  return HEAD + `<div style="width: 720px; height: 480px; background: #f7f5f0; padding: 20px 24px; display: flex; flex-direction: column; gap: 12px; color: #4a4540; font-family: ${FONT_UI}">
    <div style="display: flex; align-items: baseline; gap: 12px"><span class="display" style="font-size: 18px; font-weight: 700; color: #2b2622">${title}</span><span style="font-size: 12.5px; color: #7b736a">${tagline}</span></div>
    ${inner}
  </div>` + FOOT;
}
const wbox = (label, extra = '') => `<div style="border: 1.5px dashed #b8b0a4; border-radius: 6px; background: #ecE8e1; display: flex; align-items: center; justify-content: center; font-size: 12px; color: #7b736a; text-align: center; padding: 6px; ${extra}">${label}</div>`;

function directionB() {
  const msg = (who, text, mine) => `<div style="display: flex; flex-direction: column; gap: 3px; align-self: ${mine ? 'flex-end' : 'flex-start'}; max-width: 82%">
    <div style="font-size: 10.5px; color: #9b9187">${who}</div>
    <div style="padding: 8px 10px; border-radius: 10px; background: ${mine ? '#dcd6cb' : '#ffffff'}; border: 1px solid #d5cec2; font-size: 12px; line-height: 1.5">${text}</div></div>`;
  return wire('대안 B · 대화형 코치', '설명이 아니라 대화. 질문할 수 있고, 코치가 보드를 가리키며 답함',
    `<div style="flex: 1; display: grid; grid-template-columns: 250px minmax(0, 1fr); gap: 14px; min-height: 0">
      <div style="display: flex; flex-direction: column; gap: 8px">${wbox('보드 (작게)', 'height: 250px')}${wbox('수 목록', 'flex: 1')}</div>
      <div style="display: flex; flex-direction: column; gap: 8px; border: 1.5px dashed #b8b0a4; border-radius: 6px; padding: 12px; background: #f1ede6">
        ${msg('코치', '20…Qd7은 비숍을 지키려는 자연스러운 수였어요. 하지만 퀸이 룩과 같은 줄에 서게 됐죠. 보드에서 d파일을 강조해 둘게요.')}
        ${msg('나', '왜 Nxe7+는 안 되고 Nxf6+만 되는 거야?', true)}
        ${msg('코치', 'Nxe7+는 퀸이 잡으면서 체크를 해결하고 d파일에서도 벗어나요. f6는 퀸이 못 가는 칸이라 다릅니다. 두 수순을 나란히 재생해 볼까요?')}
        <div style="flex: 1"></div>
        ${wbox('질문 입력 · "이 구조에서 내 계획은?"', 'height: 38px')}
      </div>
    </div>`);
}
function directionC() {
  return wire('대안 C · 독서형 주석', '책처럼 읽는 한 판. 다이어그램이 본문 사이에 들어가고 여백에 짧은 메모',
    `<div style="flex: 1; display: grid; grid-template-columns: minmax(0, 1fr) 170px; gap: 16px; min-height: 0">
      <div style="display: flex; flex-direction: column; gap: 8px; font-size: 12px; line-height: 1.6; color: #4a4540">
        <div style="font-family: ${FONT_DISPLAY}; font-size: 15px; font-weight: 700; color: #2b2622">14. Rfd1 Qb8 · 헤지호그의 기다림</div>
        <div>흑은 세 번째 줄에 폰을 모아 두고 브레이크의 순간을 기다린다. ${'<b>…d5</b>'}는 지금 시기상조다. d5의 공격수가 둘, 수비수가 둘, 마지막 교환에서 흑이 기물을 잃는다.</div>
        <div style="display: flex; gap: 12px">${wbox('다이어그램 · 14수', 'width: 150px; height: 150px')}<div style="flex: 1">${wbox('다이어그램 · 20수 Qd7 이후', 'height: 150px')}</div></div>
        <div>20…Qd7?? 은 비숍을 지키는 수처럼 보이지만 룩과 한 줄에 서는 수다. <b>21.Nxf6+</b>가 체크와 함께 줄을 연다.</div>
        ${wbox('본문 계속 …', 'flex: 1')}
      </div>
      <div style="display: flex; flex-direction: column; gap: 8px">${wbox('여백 메모 · 모티프', 'height: 60px')}${wbox('여백 메모 · Maia 통계', 'height: 60px')}${wbox('여백 메모 · 검증 배지', 'height: 44px')}${wbox('목차 · 국면별 이동', 'flex: 1')}</div>
    </div>`);
}

// ---------- write ----------
writeFileSync('Main.dc.html', mainArtboard());
writeFileSync('Strategy.dc.html', strategyArtboard());
writeFileSync('Profile.dc.html', profileArtboard());
writeFileSync('Openings.dc.html', openingsArtboard());
writeFileSync('DirectionB.dc.html', directionB());
writeFileSync('DirectionC.dc.html', directionC());

const canvas = {
  artboards: [
    { file: 'Main.dc.html', title: '게임 리뷰 · 이 수의 설명', x: 0, y: 0, w: 1440, h: 900 },
    { file: 'Strategy.dc.html', title: '게임 리뷰 · 전략과 계획', x: 1560, y: 0, w: 1440, h: 900 },
    { file: 'Profile.dc.html', title: '프로필 · 약점 리포트', x: 0, y: 1060, w: 1440, h: 900 },
    { file: 'Openings.dc.html', title: '오프닝 지도', x: 1560, y: 1060, w: 1440, h: 900 },
    { file: 'DirectionB.dc.html', title: '대안 B · 대화형 코치 (저해상도)', x: 0, y: 2160, w: 720, h: 480 },
    { file: 'DirectionC.dc.html', title: '대안 C · 독서형 주석 (저해상도)', x: 840, y: 2160, w: 720, h: 480 },
  ],
  annotations: [
    { id: 'direction', x: 0, y: -190, w: 600, text: '방향: 서재의 주석 달린 기보. 종이와 잉크 팔레트, 명조 제목, 고정폭 수 표기.\n좌측 보드는 실제 국면(합법성 검증 완료), 우측은 설명 패널. 평가치와 통계는 샘플값.\n원칙: 모든 전술 주장은 수순과 함께, 문장은 보드와 대조해 검증 배지를 붙임.' },
    { id: 'alternates', x: 1660, y: 2160, w: 360, text: '아래 두 장은 리뷰 화면의 다른 방향을 저해상도로 그린 것.\n주 방향(위)이 아니라 이쪽이 끌리면 그 방향으로 다시 그림.' },
  ],
  launch: { view: 'canvas' },
};
writeFileSync('canvas.json', JSON.stringify(canvas, null, 2));
console.log('artboards written');
