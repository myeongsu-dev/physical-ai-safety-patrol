// 경보음. 소리 파일 없이 Web Audio로 삐 소리를 낸다.
// 브라우저는 사용자가 화면을 한 번 누르기 전에는 소리를 막으므로, "경보음 켜기" 버튼을 눌러야 켜진다.

// 경보 종류별 소리: [높이(Hz), 삐 소리 횟수]
// evacuate: 서버가 화재를 확정해 대피 모드로 바꿨을 때. 경보 종류가 아니라 모드에 붙는 소리
const PATTERN = {
  evacuate: [880, 3],
  obstacle: [660, 2],
  heat: [520, 1],
  flame: [520, 1],
  floor_object: [520, 1],
};
const BEEP_SEC = 0.15;
const GAP_SEC = 0.1;

// AudioCtx: 시험에서 바꿔 끼울 수 있게 받는다
export function createAlarm(AudioCtx = globalThis.AudioContext ?? globalThis.webkitAudioContext) {
  let ctx = null;
  let on = false;

  return {
    get supported() {
      return Boolean(AudioCtx);
    },
    get enabled() {
      return on;
    },
    // 브라우저가 소리를 막고 있는지 ("suspended"면 막힘)
    get blocked() {
      return Boolean(on && ctx && ctx.state && ctx.state !== "running");
    },
    // 버튼을 누를 때 부른다 (사용자 동작 안에서 만들어야 소리가 난다).
    // 소리 장치가 깨어날 때까지 기다린다. 켜졌으면 true
    async enable() {
      if (!AudioCtx) return false;
      ctx ??= new AudioCtx();
      on = true;
      try {
        await ctx.resume?.();
      } catch {
        // 막힌 경우. blocked로 알 수 있다
      }
      return true;
    },
    disable() {
      on = false;
    },
    // 경보 종류에 맞는 소리 한 번. 꺼져 있으면 아무것도 안 함. 낸 삐 소리 횟수를 돌려준다
    play(type) {
      if (!on || !ctx) return 0;
      const [freq, count] = PATTERN[type] ?? [520, 1];
      const t0 = ctx.currentTime;
      for (let i = 0; i < count; i++) {
        const start = t0 + i * (BEEP_SEC + GAP_SEC);
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "square";
        osc.frequency.value = freq;
        gain.gain.value = 0.15;
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(start);
        osc.stop(start + BEEP_SEC);
      }
      return count;
    },
  };
}
