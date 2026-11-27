// 관리자 잠금. 서버 .env에 ADMIN_PASSWORD가 있으면 경보 해제 · 대피 해제 · 출발 · 명령은
// 헤더 X-Admin-Password가 맞아야 받는다 (server/api.py, PR #23). 보기(WebSocket, GET)는 잠그지 않는다.
// 화면(DOM)은 건드리지 않는다. selfcheck.mjs에서 node로 바로 시험한다.

export const ADMIN_HEADER = "X-Admin-Password";

// 비밀번호가 맞는지 서버에 묻는다. request: fetch와 같은 모양의 함수
//   "ok"    : 맞음. 서버에 비밀번호가 없으면(잠금 안 함) 무엇을 보내도 맞음
//   "wrong" : 틀림 (401)
//   "error" : 서버에 못 물음 (끊김, 그 밖의 응답)
// /admin/check가 없는 서버(PR #23 전, 404)는 잠금이 없으므로 "ok"
export async function checkAdmin(request, password) {
  try {
    const res = await request("/admin/check", withPassword({}, password));
    if (res.ok || res.status === 404) return "ok";
    if (res.status === 401) return "wrong";
    return "error";
  } catch {
    return "error";
  }
}

// 요청 옵션에 비밀번호 헤더를 붙인다. 비밀번호가 없으면 그대로
export function withPassword(opts = {}, password) {
  if (!password) return opts;
  return { ...opts, headers: { ...(opts.headers ?? {}), [ADMIN_HEADER]: password } };
}

// 비밀번호는 이 탭에만 둔다 (sessionStorage). 탭을 닫으면 다시 묻는다.
// 사생활 보호 창 등에서 저장소를 못 쓰면 메모리에만 둔다
const KEY = "potent-admin-password";
let memory = "";

export function loadPassword() {
  try {
    return globalThis.sessionStorage?.getItem(KEY) ?? memory;
  } catch {
    return memory;
  }
}

export function savePassword(password) {
  memory = password ?? "";
  try {
    if (memory) globalThis.sessionStorage?.setItem(KEY, memory);
    else globalThis.sessionStorage?.removeItem(KEY);
  } catch {
    // 메모리에만
  }
}
