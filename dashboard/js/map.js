// 미션맵 지도 (바탕만). 대시보드 계획 페이지의 관제 지도를 옮긴 것.
// 배치는 미션맵 약도 그대로, 코너는 직각, QR 대신 마커 번호.
// 좌표는 약도 이미지를 옮긴 값(2000 x 1444 기준)이라 server/map_config.py의 xy(원본 사진 기준)와는 다르다.
// 마커 번호 1~15는 같다.
//
// 경보 위치, 로봇 위치, 대피 화살표는 여기 그리지 않는다. 나중에 받은 데이터로 위에 얹는다.

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
export function baseMap() {
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
  if (robots) {
    items.push(swatch(`<circle cx="15" cy="9" r="7" fill="#062033" stroke="${C.cyan}" stroke-width="2"/>`) + "로봇 위치 (지난 마커)");
    items.push(swatch(`<circle cx="15" cy="9" r="7" fill="#062033" stroke="${C.off}" stroke-width="2" stroke-dasharray="3 2"/>`) + "끊긴 로봇 (마지막 위치)");
  }
  return items.map((t) => `<span>${t}</span>`).join("");
}
