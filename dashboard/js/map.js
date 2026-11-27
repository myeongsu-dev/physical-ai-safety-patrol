// 미션맵 지도 (바탕만). 대시보드 계획 페이지의 관제 지도를 옮긴 것.
// 배치는 미션맵 약도 그대로, 코너는 직각, QR 대신 마커 번호.
// 좌표는 약도 이미지를 옮긴 값(2000 x 1444 기준)이라 server/map_config.py의 xy(원본 사진 기준)와는 다르다.
// 마커 번호는 같다 (1~16).
//
// 바탕(baseMap)에는 경보 위치, 로봇 위치, 대피 화살표를 그리지 않는다. 받은 데이터로 위에 얹는 층은 아래 fireLayer · guideLayer · robotLayer.

const C = {
  bg: "#08111f", floor: "#0b1729", grid: "#13233a", wall: "#9fb2c8",
  rack: "#11213a", rackLine: "#34506f", fg: "#dbe7f5", muted: "#8aa0b9",
  green: "#3ee07a", blue: "#2aa6ff", yellow: "#f5c542", one: "#dfe9f5",
  cyan: "#3ee0ff", off: "#8aa0b9", orange: "#ff9f1a",
};

export const VIEWBOX = "40 40 1920 1330";

// 순찰 통로 (x1, y1, x2, y2)
export const SEGMENTS = [
  [340, 160, 1650, 160], [1650, 160, 1650, 890], [1650, 890, 340, 890], [340, 890, 340, 160],
  [340, 553, 1650, 553], [773, 160, 773, 553], [1220, 160, 1220, 553], [1000, 553, 1000, 1268],
];

// 마커 번호 위치
// 마커 1~16 배치 (11/27 회의 시안). 위 줄 1~4, 가운데 줄 5~9 (7은 가운데 갈림길),
// 아래 줄 10~14 (12는 가운데 갈림길), 15는 아래 줄기, 16은 충전 스테이션
export const MARKER_POS = {
  1: [340, 160], 2: [773, 160], 3: [1220, 160], 4: [1650, 160],
  5: [340, 553], 6: [773, 553], 7: [1000, 553], 8: [1220, 553], 9: [1650, 553],
  10: [340, 890], 11: [670, 890], 12: [1000, 890], 13: [1325, 890], 14: [1650, 890],
  15: [1000, 1090], 16: [1000, 1268],
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

// 로봇 위치 층 (관리자 화면). robots: [{ name(화면 이름), marker, online, stopped }]
// 길 중간에 멈춘 로봇(stopped)은 주황 테두리
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
      const color = !r.online ? C.off : r.stopped ? C.orange : C.cyan;
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

// ---------- 화재 위치와 대피 안내 (서버 evac 메시지, server/dispatch.py) ----------

const FIRE = "#ff4d4d";
// 지도 위 글자(화재, 출구)가 통로 선 위에 놓여도 읽히게 바탕색 테두리를 두른다
const HALO = `stroke="${C.bg}" stroke-width="10" stroke-linejoin="round" paint-order="stroke"`;
// 비상구 문 자리 (벽 안쪽 x, 문이 뚫린 y 범위)
const EXIT_DOORS = [{ x: 258, y1: 500, y2: 620 }, { x: 1738, y1: 500, y2: 620 }];
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

// 화재 층. fire: 서버가 확정한 불난 마커 (모르면 null). 마커 둘레를 빨갛게, 위에 "화재"
export function fireLayer(fire) {
  const p = MARKER_POS[fire];
  if (!p) return "";
  const [x, y] = p;
  return `<circle class="fire-marker" cx="${x}" cy="${y}" r="64" fill="${FIRE}" fill-opacity="0.3" stroke="${FIRE}" stroke-width="8"/>` +
    `<text x="${x}" y="${y - 78}" font-size="32" font-weight="700" text-anchor="middle" fill="#ffc2c2" ${HALO}>화재</text>`;
}

// 출구 마커 옆에 비상구 문이 있으면 그 문 (마커 높이가 문 범위 안이고 가까운 쪽)
function doorFor(marker) {
  const p = MARKER_POS[marker];
  if (!p) return null;
  const near = EXIT_DOORS.filter((d) => p[1] >= d.y1 && p[1] <= d.y2);
  near.sort((a, b) => Math.abs(a.x - p[0]) - Math.abs(b.x - p[0]));
  return near[0] ?? null;
}

// 대피 층. evac: { fire, exits: [마커], arrows: [{ marker, dir: N·E·S·W·here·x, exit, path }] }
//   - 대피 길: 모든 arrows의 path를 합친 통로를 초록 점선으로 (같은 통로는 한 번만)
//   - 출구: 초록 테두리와 "출구". 옆에 비상구 문이 있으면 문까지 잇는다
//   - 화살표: 마커마다 출구 쪽 첫 걸음 방향. here(출구)는 화살표 없음
//   - x: 갈 수 없는 마커 (불난 마커는 화재 층이 따로 그린다)
export function guideLayer(evac) {
  if (!evac) return "";
  const arrows = Array.isArray(evac.arrows) ? evac.arrows : [];
  const exits = Array.isArray(evac.exits) ? evac.exits : [];
  let s = "";
  const drawn = new Set();
  for (const g of arrows) {
    const path = Array.isArray(g.path) ? g.path : [];
    for (let i = 0; i + 1 < path.length; i++) {
      const key = [path[i], path[i + 1]].sort((a, b) => a - b).join("-");
      if (drawn.has(key)) continue;
      drawn.add(key);
      const seg = corridorPoints(path[i], path[i + 1]);
      if (!seg) continue; // 지도 통로로 이어지지 않으면 그리지 않는다
      s += `<polyline class="guide-path" points="${points(seg)}" fill="none" stroke="${C.green}" stroke-width="10" stroke-dasharray="26 16" stroke-linejoin="round"/>`;
    }
  }
  for (const e of exits) {
    const p = MARKER_POS[e];
    if (!p) continue;
    const door = doorFor(e);
    if (door) {
      s += `<polyline class="guide-path" points="${points([p, [door.x, p[1]]])}" fill="none" stroke="${C.green}" stroke-width="10" stroke-dasharray="26 16"/>`;
    }
    s += `<rect class="guide-exit" x="${p[0] - 40}" y="${p[1] - 40}" width="80" height="80" rx="14" fill="none" stroke="${C.green}" stroke-width="8"/>`;
    s += `<text x="${p[0]}" y="${p[1] + 74}" font-size="30" font-weight="700" text-anchor="middle" fill="${C.green}" ${HALO}>출구</text>`;
  }
  for (const g of arrows) {
    const p = MARKER_POS[g.marker];
    if (!p) continue;
    if (g.dir in DIR_ANGLE) {
      s += `<g class="guide-arrow" transform="translate(${p[0]},${p[1]}) rotate(${DIR_ANGLE[g.dir]})">` +
        `<polygon points="30,-9 76,-9 76,-24 112,0 76,24 76,9 30,9" fill="${C.green}" stroke="#062b16" stroke-width="4" stroke-linejoin="round"/></g>`;
    } else if (g.dir === "x" && g.marker !== evac.fire) {
      const [x, y] = [p[0] + 44, p[1] - 44];
      s += `<g class="guide-blocked" stroke="${C.off}" stroke-width="7" stroke-linecap="round">` +
        `<line x1="${x - 14}" y1="${y - 14}" x2="${x + 14}" y2="${y + 14}"/><line x1="${x + 14}" y1="${y - 14}" x2="${x - 14}" y2="${y + 14}"/></g>`;
    }
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
    swatch(`<rect x="7" y="1" width="16" height="16" rx="3" fill="#0b1626" stroke="${C.yellow}" stroke-width="2"/>`) + `마커 번호 1~${Object.keys(MARKER_POS).length}`,
    swatch(`<rect x="3" y="2" width="24" height="14" fill="#0f3a24" stroke="${C.green}" stroke-width="2"/>`) + "비상구",
    swatch(`<rect x="3" y="2" width="24" height="14" fill="none" stroke="${C.wall}" stroke-width="2" stroke-dasharray="4 3"/>`) + "충전 스테이션",
  ];
  items.push(swatch(`<circle cx="15" cy="9" r="7" fill="${FIRE}" fill-opacity="0.3" stroke="${FIRE}" stroke-width="2"/>`) + "화재 위치");
  items.push(swatch(`<polygon points="3,6 18,6 18,2 28,9 18,16 18,12 3,12" fill="${C.green}"/>`) + "대피 방향");
  items.push(swatch(`<rect x="8" y="2" width="14" height="14" rx="3" fill="none" stroke="${C.green}" stroke-width="2"/>`) + "대피 출구");
  if (robots) {
    items.push(swatch(`<circle cx="15" cy="9" r="7" fill="#062033" stroke="${C.cyan}" stroke-width="2"/>`) + "로봇 위치 (지난 마커)");
    items.push(swatch(`<circle cx="15" cy="9" r="7" fill="#062033" stroke="${C.off}" stroke-width="2" stroke-dasharray="3 2"/>`) + "끊긴 로봇 (마지막 위치)");
    items.push(swatch(`<circle cx="15" cy="9" r="7" fill="#062033" stroke="${C.orange}" stroke-width="2"/>`) + "길 중간에 멈춘 로봇");
  }
  return items.map((t) => `<span>${t}</span>`).join("");
}
