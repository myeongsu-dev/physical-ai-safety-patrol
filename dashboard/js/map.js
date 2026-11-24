// 미션맵 지도 (바탕만). 대시보드 계획 페이지의 관제 지도를 옮긴 것.
// 배치는 미션맵 약도 그대로, 코너는 직각, QR 대신 마커 번호.
// 좌표는 약도 이미지를 옮긴 값(2000 x 1444 기준)이라 server/map_config.py의 xy(원본 사진 기준)와는 다르다.
// 마커 번호 1~15는 같다.
//
// 바탕(baseMap)에는 경보 위치, 로봇 위치, 대피 화살표를 그리지 않는다. 받은 데이터로 위에 얹는 층은 아래 fireLayer · guideLayer · robotLayer.

const C = {
  bg: "#08111f", floor: "#0b1729", grid: "#13233a", wall: "#9fb2c8",
  rack: "#11213a", rackLine: "#34506f", fg: "#dbe7f5", muted: "#8aa0b9",
  green: "#3ee07a", blue: "#2aa6ff", yellow: "#f5c542", one: "#dfe9f5",
  cyan: "#3ee0ff", off: "#8aa0b9",
};

export const VIEWBOX = "40 40 1920 1330";

// 순찰 통로 (x1, y1, x2, y2)
export const SEGMENTS = [
  [340, 160, 1650, 160], [1650, 160, 1650, 890], [1650, 890, 340, 890], [340, 890, 340, 160],
  [340, 553, 1650, 553], [773, 160, 773, 553], [1220, 160, 1220, 553], [1000, 553, 1000, 1268],
];

// 마커 번호 위치
export const MARKER_POS = {
  1: [340, 160], 2: [773, 160], 3: [1220, 160], 4: [1650, 160],
  5: [340, 553], 6: [773, 553], 7: [1220, 553], 8: [1650, 553],
  9: [1000, 690], 10: [340, 890], 11: [870, 890], 12: [1150, 890],
  13: [1650, 890], 14: [1000, 1090], 15: [1000, 1268],
};

// 구역 (랙이 있는 칸)
export const ZONES = {
  A1: { x: 420, y: 237, w: 290, h: 218 },
  A2: { x: 852, y: 237, w: 291, h: 218 },
  A3: { x: 1285, y: 237, w: 291, h: 218 },
  B1: { x: 420, y: 670, w: 506, h: 145 },
  B2: { x: 1068, y: 670, w: 508, h: 145 },
};

const DEFS =
  "<defs>" +
  '<filter id="map-glow" filterUnits="userSpaceOnUse" x="0" y="0" width="2000" height="1444"><feGaussianBlur stdDeviation="5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>' +
  `<pattern id="map-grid" width="50" height="50" patternUnits="userSpaceOnUse"><path d="M50,0 H0 V50" fill="none" stroke="${C.grid}" stroke-width="2"/></pattern>` +
  '<pattern id="map-exit" width="18" height="18" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="18" height="18" fill="#0f3a24"/><line x1="0" y1="0" x2="0" y2="18" stroke="#1f7a45" stroke-width="6"/></pattern>' +
  `<pattern id="map-stripe" width="24" height="24" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="24" height="24" fill="#111"/><rect width="12" height="24" fill="${C.yellow}"/></pattern>` +
  "</defs>";

function line(s, attr) {
  return `<line x1="${s[0]}" y1="${s[1]}" x2="${s[2]}" y2="${s[3]}" ${attr}/>`;
}

function pill(x, y, text, size = 24) {
  const w = Math.round(text.length * size * 0.82 + 30), h = size + 20;
  return `<rect x="${x - w / 2}" y="${y - h / 2}" width="${w}" height="${h}" rx="${h / 2}" fill="#0d1a2c" stroke="${C.wall}" stroke-width="2.5"/>` +
    `<text x="${x}" y="${y + size * 0.36}" font-size="${size}" font-weight="700" text-anchor="middle" fill="${C.fg}">${text}</text>`;
}

function bolt(x, y) {
  return `<path transform="translate(${x},${y})" d="M4,-22 L-12,4 H0 L-4,22 L12,-4 H0Z" fill="${C.yellow}"/>`;
}

// 지도 바탕 SVG 내용 (<svg> 안에 넣는 문자열)
// under: 통로 위, 마커 번호 아래에 넣을 내용 (화재 위치·대피 화살표 층). 마커 번호가 가려지지 않게
export function baseMap(under = "") {
  let s = DEFS;
  s += `<rect x="40" y="40" width="1920" height="1330" fill="${C.bg}"/>`;

  // 바닥과 벽 (비상구 자리는 비워 둠)
  s += `<rect x="258" y="73" width="1480" height="1065" fill="${C.floor}"/><rect x="258" y="73" width="1480" height="1065" fill="url(#map-grid)"/>`;
  s += `<path d="M258,500 V73 H1738 V500 M1738,620 V1138 H1030 M970,1138 H258 V620" fill="none" stroke="${C.wall}" stroke-width="10" stroke-linecap="square"/>`;
  s += '<path d="M268,500 V83 H1728 V500 M1728,620 V1128 H1030 M970,1128 H268 V620" fill="none" stroke="#5b6f88" stroke-width="2"/>';

  // 비상구
  s += `<rect x="96" y="500" width="152" height="120" fill="url(#map-exit)" stroke="${C.green}" stroke-width="3"/>`;
  s += '<text x="172" y="552" font-size="28" font-weight="700" text-anchor="middle" fill="#eafff1">비상구 1</text><path d="M200,585 H140 M140,585 l14,-10 M140,585 l14,10" stroke="#eafff1" stroke-width="4" fill="none"/>';
  s += `<rect x="1748" y="500" width="152" height="120" fill="url(#map-exit)" stroke="${C.green}" stroke-width="3"/>`;
  s += '<text x="1824" y="552" font-size="28" font-weight="700" text-anchor="middle" fill="#eafff1">비상구 2</text><path d="M1796,585 H1856 M1856,585 l-14,-10 M1856,585 l-14,10" stroke="#eafff1" stroke-width="4" fill="none"/>';

  // 충전 스테이션
  s += `<rect x="863" y="1163" width="268" height="160" fill="#0d1a2c" stroke="${C.wall}" stroke-width="3" stroke-dasharray="16 10"/>`;
  s += '<rect x="863" y="1163" width="16" height="160" fill="url(#map-stripe)"/><rect x="1115" y="1163" width="16" height="160" fill="url(#map-stripe)"/>';
  s += pill(1290, 1243, "충전 스테이션");
  s += bolt(1060, 1210);

  // 구역
  for (const [id, r] of Object.entries(ZONES)) {
    s += `<rect x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" rx="4" fill="${C.rack}" stroke="${C.rackLine}" stroke-width="3"/>`;
    s += `<text x="${r.x + r.w / 2}" y="${r.y + r.h / 2 + 18}" font-size="52" font-weight="700" text-anchor="middle" fill="#c9daf0">${id}</text>`;
  }

  // 순찰 통로
  for (const sg of SEGMENTS) {
    s += line(sg, `stroke="${C.one}" stroke-width="5" stroke-dasharray="20 12" filter="url(#map-glow)"`);
  }

  s += under;

  // 마커 번호
  for (const [n, [x, y]] of Object.entries(MARKER_POS)) {
    s += `<rect x="${x - 24}" y="${y - 24}" width="48" height="48" rx="8" fill="#0b1626" stroke="${C.yellow}" stroke-width="4"/>`;
    s += `<text x="${x}" y="${y + 9}" font-size="26" font-weight="700" text-anchor="middle" fill="#fff">${n}</text>`;
  }
  return s;
}

// 로봇 위치 층 (관리자 화면). robots: [{ name(화면 이름), marker, online }]
// 지난 마커 위치에 그린다. 같은 마커에 여러 대면 옆으로 나란히.
// 끊긴 로봇은 회색 점선으로 마지막 위치에 그린다.
export function robotLayer(robots) {
  const byMarker = {};
  for (const r of robots) {
    if (!(r.marker in MARKER_POS)) continue;
    (byMarker[r.marker] ??= []).push(r);
  }
  let s = "";
  for (const [marker, list] of Object.entries(byMarker)) {
    const [mx, my] = MARKER_POS[marker];
    list.forEach((r, i) => {
      const x = mx + (i - (list.length - 1) / 2) * 130;
      const y = my - 62;
      const color = r.online ? C.cyan : C.off;
      const dash = r.online ? "" : ' stroke-dasharray="8 6"';
      s += `<g class="robot">`;
      s += `<line x1="${x}" y1="${y + 27}" x2="${mx}" y2="${my - 24}" stroke="${color}" stroke-width="3"${dash}/>`;
      s += `<rect x="${x - 60}" y="${y - 27}" width="120" height="54" rx="27" fill="#062033" stroke="${color}" stroke-width="5"${dash}/>`;
      s += `<text x="${x}" y="${y + 10}" font-size="28" font-weight="700" text-anchor="middle" fill="#fff">${esc(r.name)}</text>`;
      s += "</g>";
    });
  }
  return s;
}

// ---------- 화재 위치와 대피 화살표 (서버와 정하는 중인 제안 모양, README "서버와 주고받기") ----------

const FIRE = "#ff4d4d";
// 비상구 문 자리 (벽 안쪽 x, 문이 뚫린 y 범위)
const EXIT_DOOR = { 1: { x: 258, y1: 500, y2: 620 }, 2: { x: 1738, y1: 500, y2: 620 } };
const DIR_ANGLE = { E: 0, S: 90, W: 180, N: -90 };

// 두 점이 같은 순찰 통로(SEGMENTS) 위에 있는지
function onCorridor([x1, y1], [x2, y2]) {
  return SEGMENTS.some(([ax, ay, bx, by]) => {
    const within = (v, a, b) => v >= Math.min(a, b) && v <= Math.max(a, b);
    if (ax === bx) return x1 === ax && x2 === ax && within(y1, ay, by) && within(y2, ay, by);
    if (ay === by) return y1 === ay && y2 === ay && within(x1, ax, bx) && within(x2, ax, bx);
    return false;
  });
}

// 선분 p–q 사이(끝 제외)에 다른 마커가 있는지. 있으면 a, b는 이웃이 아니다
function passesMarker(p, q, skip) {
  return Object.entries(MARKER_POS).some(([n, [x, y]]) => {
    if (skip.includes(Number(n))) return false;
    const inX = x >= Math.min(p[0], q[0]) && x <= Math.max(p[0], q[0]);
    const inY = y >= Math.min(p[1], q[1]) && y <= Math.max(p[1], q[1]);
    return inX && inY && (p[0] === q[0] ? x === p[0] : y === p[1]);
  });
}

// 이웃한 마커 a, b 사이 통로의 점 목록. 직선이면 두 점, 꺾이면(예: 9–6, 14–11) 모퉁이를 넣어 세 점.
// 통로로 이어지지 않거나 중간에 다른 마커를 지나면(이웃이 아님) null
export function corridorPoints(a, b) {
  const pa = MARKER_POS[a], pb = MARKER_POS[b];
  if (!pa || !pb || a === b) return null;
  const ok = (list) => list.every((p, i) => i === 0 || (onCorridor(list[i - 1], p) && !passesMarker(list[i - 1], p, [a, b])));
  if (ok([pa, pb])) return [pa, pb];
  for (const c of [[pa[0], pb[1]], [pb[0], pa[1]]]) {
    if (ok([pa, c, pb])) return [pa, c, pb];
  }
  return null;
}

function points(list) {
  return list.map(([x, y]) => `${x},${y}`).join(" ");
}

// 화재 층. fires: [{ segment: [마커, 마커] | null, zones: ["A1", …] }]
// 통로 구간은 빨간 굵은 선, 양옆 구역은 빨간 칸과 "화재 의심"
export function fireLayer(fires) {
  let s = "";
  for (const f of fires) {
    for (const z of f.zones ?? []) {
      const r = ZONES[z];
      if (!r) continue;
      s += `<rect class="fire-zone" x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" rx="4" fill="${FIRE}" fill-opacity="0.22" stroke="${FIRE}" stroke-width="6"/>`;
      // 구역 이름(가운데)과 겹치지 않게 위쪽에
      s += `<text x="${r.x + r.w / 2}" y="${r.y + 38}" font-size="28" font-weight="700" text-anchor="middle" fill="#ffc2c2">화재 의심</text>`;
    }
    const pts = Array.isArray(f.segment) && f.segment.length === 2 ? corridorPoints(f.segment[0], f.segment[1]) : null;
    if (pts) {
      s += `<polyline class="fire-segment" points="${points(pts)}" fill="none" stroke="${FIRE}" stroke-opacity="0.45" stroke-width="34" stroke-linejoin="round" stroke-linecap="round"/>`;
      s += `<polyline points="${points(pts)}" fill="none" stroke="${FIRE}" stroke-width="10" stroke-linejoin="round" stroke-linecap="round"/>`;
    }
  }
  return s;
}

// 대피 층. guides: [{ marker, dir: N·E·S·W, exit: 1·2, path: [마커, …] }]
// 갈림길 마커 옆에 비상구 쪽 화살표. path가 있으면 그 마커들을 지나 비상구 문까지 점선
export function guideLayer(guides) {
  let s = "";
  for (const g of guides) {
    const path = Array.isArray(g.path) ? g.path : [];
    const pts = [];
    for (let i = 0; i + 1 < path.length; i++) {
      const seg = corridorPoints(path[i], path[i + 1]);
      if (!seg) { pts.length = 0; break; } // 이어지지 않는 길은 그리지 않는다
      pts.push(...(i === 0 ? seg : seg.slice(1)));
    }
    const door = EXIT_DOOR[g.exit];
    // 길의 끝 (마커 하나뿐인 path면 그 마커). 비상구 문과 높이가 맞으면 문까지 잇는다
    const last = pts.length ? pts.at(-1) : path.length === 1 ? MARKER_POS[path[0]] : null;
    if (door && last && last[1] >= door.y1 && last[1] <= door.y2) {
      if (!pts.length) pts.push(last);
      pts.push([door.x, last[1]]);
    }
    if (pts.length >= 2) {
      s += `<polyline class="guide-path" points="${points(pts)}" fill="none" stroke="${C.green}" stroke-width="10" stroke-dasharray="26 16" stroke-linejoin="round"/>`;
    }
  }
  // 화살표는 길 위에 그린다
  for (const g of guides) {
    const p = MARKER_POS[g.marker];
    if (!p || !(g.dir in DIR_ANGLE)) continue;
    s += `<g class="guide-arrow" transform="translate(${p[0]},${p[1]}) rotate(${DIR_ANGLE[g.dir]})">` +
      `<polygon points="34,-10 100,-10 100,-26 142,0 100,26 100,10 34,10" fill="${C.green}" stroke="#062b16" stroke-width="4" stroke-linejoin="round"/></g>`;
  }
  return s;
}

function esc(x) {
  return String(x).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function swatch(inner) {
  return `<svg width="30" height="18" viewBox="0 0 30 18" aria-hidden="true">${inner}</svg>`;
}

// 범례 HTML
export function legendHtml({ robots = false } = {}) {
  const items = [
    swatch(`<line x1="1" y1="9" x2="29" y2="9" stroke="${C.one}" stroke-width="3" stroke-dasharray="7 4"/>`) + "순찰 통로",
    swatch(`<rect x="7" y="1" width="16" height="16" rx="3" fill="#0b1626" stroke="${C.yellow}" stroke-width="2"/>`) + "마커 번호 1~15",
    swatch(`<rect x="3" y="2" width="24" height="14" fill="#0f3a24" stroke="${C.green}" stroke-width="2"/>`) + "비상구",
    swatch(`<rect x="3" y="2" width="24" height="14" fill="none" stroke="${C.wall}" stroke-width="2" stroke-dasharray="4 3"/>`) + "충전 스테이션",
  ];
  items.push(swatch(`<line x1="2" y1="9" x2="28" y2="9" stroke="${FIRE}" stroke-width="5" stroke-linecap="round"/>`) + "화재 위치");
  items.push(swatch(`<polygon points="3,6 18,6 18,2 28,9 18,16 18,12 3,12" fill="${C.green}"/>`) + "대피 방향");
  if (robots) {
    items.push(swatch(`<circle cx="15" cy="9" r="7" fill="#062033" stroke="${C.cyan}" stroke-width="2"/>`) + "로봇 위치 (지난 마커)");
    items.push(swatch(`<circle cx="15" cy="9" r="7" fill="#062033" stroke="${C.off}" stroke-width="2" stroke-dasharray="3 2"/>`) + "끊긴 로봇 (마지막 위치)");
  }
  return items.map((t) => `<span>${t}</span>`).join("");
}
