// 미션맵 마커와 순찰 순서. server/map_config.py에서 옮겨 왔다.
// map_config.py가 바뀌면 여기도 같이 고친다 (selfcheck.mjs가 두 파일을 비교한다).

// xy: 지도 그림(missionmap_original.png, 1191x860) 위 픽셀 좌표
export const MARKERS = {
  1: [227, 118],
  2: [434, 97],
  3: [759, 97],
  4: [960, 117],
  5: [206, 299],
  6: [508, 334],
  7: [691, 334],
  8: [980, 255],
  9: [593, 412],
  10: [226, 507],
  11: [518, 527],
  12: [684, 527],
  13: [960, 507],
  14: [593, 650],
  15: [593, 755],
};

export const DOCK_MARKER = 15;

// entry: 충전 스테이션에서 나가며 지나는 마커, loop: 반복하는 고리, back: 복귀 때 지나는 마커
export const ROUTES = {
  A: {
    entry: [15, 14],
    loop: [11, 10, 5, 1, 2, 3, 4, 8, 13, 12],
    back: [14, 15],
  },
  B: {
    entry: [15, 14, 9],
    loop: [6, 5, 1, 2, 6, 7, 3, 4, 8, 7],
    back: [9, 14, 15],
  },
};

export const BATTERY_RETURN = 25;
