// 장치가 아직 없는 시험 (E~I). 목록에서 필요한 장치와 함께 회색으로 보인다.
// 장치가 생기면 이 목록에서 빼고 A1_spin.js처럼 파일 하나로 만든다 (needs · record · steps · metrics).
const plan = (id, group, title, needs, refs, setup) => ({ id, group, title, needs, refs, setup, kind: "measure", planned: true, steps: () => [] });
const xiao = { xiao: { channels: ["temp", "image"] } };
const pi = { pi: { channels: ["detect", "image"] } };
const server = { server: { channels: ["event"] } };
const dogs = (n) => Object.fromEntries(Array.from({ length: n }, (_, i) => [i ? `dog${i + 1}` : "dog", {}]));

export default [
  plan("E1", "E 머리 카메라 · 온도", "온도 센서 거리별 값", xiao, ["F-05", "기술검증: 온도 센서"], "실외 가스버너, 10·20·30·50cm에서 표면 온도"),
  plan("E2", "E 머리 카메라 · 온도", "과열 임계 45/50℃ 판정", xiao, ["F-05", "F-10"], "온도를 올리며 45℃ 정지 · 50℃ 확정 시점"),
  plan("E3", "E 머리 카메라 · 온도", "장애물 사진 촬영 · 전송 시간", xiao, ["F-03 사진"], "정지 → 사진 도착까지 ms"),
  plan("F1", "F 온보드 AI (Pi 5)", "불꽃 · 헬멧 · 사람 · 장애물 인식률", pi, ["F-05", "F-06", "F-07"], "정답 라벨 사진 묶음 vs 추론 결과"),
  plan("F2", "F 온보드 AI (Pi 5)", "추론 속도 CPU vs Hailo", pi, ["기술검증: 온보드 AI 추론"], "프레임당 ms, 초당 장 수"),
  plan("F3", "F 온보드 AI (Pi 5)", "보행자는 장애물에서 제외", pi, ["F-07"], "걷는 사람 / 서 있는 사람 / 박스"),
  plan("G1", "G 관제 · 통신", "이벤트 → 대시보드 2초 안", server, ["F-08 2초 안 표시"], "감지 시각 vs 화면 표시 시각"),
  plan("G2", "G 관제 · 통신", "로봇 4대 + Pi 5 4대 동시 수신", server, ["기술검증: 서버 동시 수신"], "10분 동안 끊김 · 지연"),
  plan("H1", "H 다중 협업", "과열 교차 확인", { ...dogs(2), ...server }, ["F-10"], "1호 정지 → 2호 도착 · 재측정까지"),
  plan("H2", "H 다중 협업", "충전 교대 인계", { ...dogs(2), ...server }, ["F-11"], "배터리 부족 → 대기 로봇이 같은 구간을 이어 돎"),
  plan("H3", "H 다중 협업", "합류 양보", { ...dogs(2), ...server }, ["F-13", "03 동선 규칙"], "합류점에서 A 우선, B 대기"),
  plan("H4", "H 다중 협업", "화재 대피 배정", { ...dogs(4), ...server }, ["F-12"], "4대가 갈림길 ①~④에 닿는 시간"),
  plan("I1", "I 시연 리허설", "장면 1~6 통과", { ...dogs(4), ...server }, ["03 시연 흐름"], "장면별 시간 · 실패 지점"),
];
