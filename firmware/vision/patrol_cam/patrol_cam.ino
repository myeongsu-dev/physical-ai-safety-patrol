// 비전 모듈(ESP32-S3, GC2145) 펌웨어 · 박명수
// 하는 일
//   1. 검은 선 찾기 (항상): MechDog 보드가 I2C(주소 0x53)로 읽는 Hiwonder 레지스터 형식 그대로 답한다.
//      검정은 0xA8(화면 위 구역)·0xA9(가운데 구역, 못 찾으면 아래 구역), 7바이트. 0xB0은 세 구역 21바이트 [ID 5, 중심x, 중심y, 좌상x, 좌상y, 우하x, 우하y].
//      좌표는 160x120 기준(가운데 x=80). 다른 레지스터는 0.
//   2. 영상 보내기 (켤 때만): USB 시리얼로 JPEG를 이어 보낸다. 찾은 선 상자와 구역 경계를 그려서 보냄.
//      Wi-Fi를 저장해 두면 http://patrol-cam.local/stream (MJPEG)으로도 보낸다. 받는 쪽이 붙어 있는 동안만 만든다.
// USB 시리얼 명령 (줄 단위): STREAM 1 / STREAM 0, THR <1~254> (검정 기준 밝기, 저장됨), INFO,
//   SCAN (보이는 2.4GHz Wi-Fi 목록), WIFI <이름>\t<비밀번호> (저장하고 붙음, 이름 없이 WIFI면 지움),
//   TXP <0.25dBm 단위, 8~84> (Wi-Fi 송신 세기, 저장됨. USB 없이 I2C 전원만이면 낮춰야 덜 끊김),
//   OTAKEY <열쇠, 32자까지> (저장됨. 있으면 Wi-Fi로 굽기 POST /update 받음, 이름 없이 OTAKEY면 지움)
// 빌드·굽기는 firmware/vision/README.md
#include <Arduino.h>
#include <Wire.h>
#include <Preferences.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <Update.h>
#include "esp_http_server.h"
#include "lwip/sockets.h"
#include "esp_camera.h"
#include "img_converters.h"

// ---- 핀 (Hiwonder 비전 모듈 = ESP32-S3-EYE 배치) ----
#define XCLK_PIN 15
#define SIOD_PIN 4
#define SIOC_PIN 5
static const int DATA_PINS[8] = {11, 9, 8, 10, 12, 18, 17, 16};  // Y2..Y9
#define VSYNC_PIN 6
#define HREF_PIN 7
#define PCLK_PIN 13
#define I2C_SDA 47
#define I2C_SCL 48
#define I2C_ADDR 0x53

#define BAUD 460800  // 921600은 CH340에서 바이트가 빠져 그림이 깨짐
#define W 320  // 카메라 프레임 (QVGA). 찾기는 2칸씩 건너 160x120 좌표로
#define H 240
#define SW 160
#define SH 120
#define BAND (SH / 3)  // 화면을 위·가운데·아래 3구역으로 나눔 (아래 구역부터 이어서 찾음)
#define BLACK_ID 5
#define COL_MIN 3      // 한 열에서 검정 칸이 이만큼 있어야 선 후보
#define AREA_MIN 40    // 구역 안 검정 칸 합이 이보다 작으면 못 찾음
#define CONTRAST_MIN 50  // 구역 안 가장 밝은·어두운 밝기 차이가 이보다 작으면 바닥뿐 (검은 테이프 약 120 vs 흰 바닥 약 250)
// 구역에서 가장 어두운 값이 이보다 밝으면 테이프 없음. 집 바닥 2026-11-26: 테이프 6~22, 테이프 없는 그늘진 바닥 111~123
// (T10: 테이프가 화면 밖으로 나간 뒤 그늘을 선으로 잡아 반대로 꺾음). 미션맵 조명에서 다시 볼 것
#define DARK_MAX 90
// 고른 검정 띠가 구역(160x40 = 6400칸)을 거의 다 덮으면 테이프가 아님 (렌즈가 가려졌거나 바닥 전체가 어두움 → 선 놓침으로 멈춤).
// 각도 16~24°에서 곧은 테이프 약 870~2900칸, 가운데 구역을 가로지르는 직각 코너 약 2600~5000칸 (렌즈 실측 f 548 ·
// 높이 3.7~4.1 cm로 계산). 코너를 버리지 않도록 크게 둠
#define AREA_MAX 6000
// 고른 검정 띠의 평균 밝기가 구역에서 가장 밝은 값의 이 비율(%)보다 밝으면 그림자 (테이프 20~39%, 그림자 62%:
// T19 2026-11-26 그늘 속 바닥 이음매를 선으로 잡아 엉뚱한 쪽으로 감). 가장 어두운 값만 보면 이음매(81)가 DARK_MAX를 통과함
#define DARK_RATIO 50
// 아래 구역부터 이어서 찾기: 각 구역은 기준 x(아래 구역은 바로 전 장의 아래 구역, 가운데는 아래, 위는 가운데)에
// 가장 가까운 띠를 고르고, 그 거리가 이보다 멀면 못 찾음. 화면에 다른 트랙이 같이 보여도 넘어가지 않게
// (2026-11-26 36초 주행: 가장 굵은 띠를 고르다 돌던 중 옆 트랙을 잡음). 기준이 없으면 가장 굵은 띠
#define JUMP_MAX 40
#define NEAR_KEEP 15  // 아래 구역을 이만큼(장) 놓쳐도 마지막 위치를 기준으로 씀 (약 0.5초)
#define TRIO_REG 0xB0  // 이 레지스터를 읽으면 위 · 가운데 · 아래 구역 7바이트씩 21바이트 (가운데는 아래로 바꾸기 전 값)

static uint8_t regs[256][7];  // I2C 레지스터 (주소 → 7바이트)
static uint8_t trio[21];      // TRIO_REG
static portMUX_TYPE regLock = portMUX_INITIALIZER_UNLOCKED;
static volatile uint8_t reqReg = 0;

static Preferences prefs;
// shortcut: 임시값. 집 바닥·조명에서 잰 값(자동 노출이라 검은 테이프 약 120, 흰 바닥 약 250, 2026-11-24).
//           미션맵 바닥·테이프·조명에서 다시 재서 고쳐야 함 (view.py --thr로 맞춘 뒤 여기 반영하고 build.py)
#define THR_DEFAULT 160
static volatile uint8_t thr = THR_DEFAULT;
static volatile bool streaming = false;
static uint16_t *shot = NULL;  // 영상용 복사본 (PSRAM)
static SemaphoreHandle_t shotReady, shotFree;
static volatile uint32_t frames = 0;
#define HOSTNAME "patrol-cam"
// Wi-Fi 영상: streamTask가 만든 마지막 JPEG를 넘겨받아 /stream이 보냄
static SemaphoreHandle_t webLock;
static uint8_t *webJpg = NULL;
static size_t webLen = 0;
static volatile uint32_t webSeq = 0;
static volatile int webClients = 0;
static httpd_handle_t httpd = NULL;

// ---------- I2C (MechDog 보드가 readfrom_mem(0x53, 레지스터, 7)로 읽음) ----------
static void onReceive(int n) {
  if (Wire.available()) reqReg = Wire.read();
  while (Wire.available()) Wire.read();
}

static void onRequest() {
  uint8_t out[21];
  int n = reqReg == TRIO_REG ? 21 : 7;
  portENTER_CRITICAL(&regLock);
  memcpy(out, n == 21 ? trio : regs[reqReg], n);
  portEXIT_CRITICAL(&regLock);
  Wire.write(out, n);
}

// ---------- 검은 선 찾기 ----------
static inline uint16_t px(const uint8_t *buf, int x, int y) {  // RGB565, 큰 바이트 먼저
  const uint8_t *p = buf + (y * W + x) * 2;
  return (p[0] << 8) | p[1];
}

static inline uint8_t luma(uint16_t c) {
  uint8_t r = (c >> 11) << 3, g = ((c >> 5) & 0x3F) << 2, b = (c & 0x1F) << 3;
  return (r * 77 + g * 150 + b * 29) >> 8;
}

// 구역 하나에서 가장 굵은 검정 띠를 찾아 out[7]에 채움 (못 찾으면 0)
// 검정 기준은 구역의 가장 어두운·밝은 밝기의 중간 (thr는 상한). 조명이 바뀌면 고정값으로는 바닥까지 검정이 됨
// (2026-11-26 해 질 녘: 테이프 없는 바닥을 폭 전체로 잡아 x=79로 직진). 차이가 CONTRAST_MIN보다 작으면 바닥뿐 → 못 찾음
static void findLine(const uint8_t *buf, int band, int ref, uint8_t out[7]) {
  uint8_t cnt[SW], top[SW], bot[SW];
  uint16_t sv[SW];  // 열마다 검정 칸 밝기 합 (최대 40 x 255)
  int y0 = band * BAND, y1 = y0 + BAND;
  uint8_t lo = 255, hi = 0;
  for (int x = 0; x < SW; x++)
    for (int y = y0; y < y1; y++) {
      uint8_t v = luma(px(buf, x * 2, y * 2));
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  memset(out, 0, 7);
  if (hi - lo < CONTRAST_MIN || lo > DARK_MAX) return;
  uint8_t t = min((int)thr, (lo + hi) / 2);
  for (int x = 0; x < SW; x++) {
    cnt[x] = 0; top[x] = 255; bot[x] = 0; sv[x] = 0;
    for (int y = y0; y < y1; y++) {
      uint8_t v = luma(px(buf, x * 2, y * 2));
      if (v < t) {
        cnt[x]++;
        sv[x] += v;
        if (y < top[x]) top[x] = y;
        bot[x] = y;
      }
    }
  }
  // 후보 열이 이어진 구간(한 칸 끊김은 이어 봄) 중: 기준 x가 있으면 가운데가 가장 가까운 것, 없으면 검정 칸 합이 가장 큰 것
  int best = 0, bx1 = 0, bx2 = 0, bd = 9999, x = 0;
  while (x < SW) {
    if (cnt[x] < COL_MIN) { x++; continue; }
    int s = x, sum = 0, gap = 0, e = x;
    for (; x < SW; x++) {
      if (cnt[x] >= COL_MIN) { sum += cnt[x]; e = x; gap = 0; }
      else if (++gap > 1) break;
    }
    if (sum < AREA_MIN || sum > AREA_MAX) continue;
    int d = ref < 0 ? -sum : abs((s + e) / 2 - ref);
    if (d < bd) { bd = d; best = sum; bx1 = s; bx2 = e; }
  }
  if (!best || (ref >= 0 && bd > JUMP_MAX)) return;
  uint8_t ty = 255, by = 0;
  uint32_t dsum = 0, dn = 0;
  for (int i = bx1; i <= bx2; i++) {
    if (cnt[i] < COL_MIN) continue;
    if (top[i] < ty) ty = top[i];
    if (bot[i] > by) by = bot[i];
    dsum += sv[i]; dn += cnt[i];
  }
  if (dsum * 100 > (uint32_t)hi * DARK_RATIO * dn) return;  // 평균이 밝은 바닥의 절반보다 밝음 → 그림자
  out[0] = BLACK_ID;
  out[1] = (bx1 + bx2) / 2;
  out[2] = (ty + by) / 2;
  out[3] = bx1; out[4] = ty; out[5] = bx2; out[6] = by;
}

// 영상용 그림: 160x120 좌표의 상자를 320x240 복사본에 그림
static void rect(uint16_t *img, int x1, int y1, int x2, int y2, uint16_t c) {
  x1 *= 2; y1 *= 2; x2 = x2 * 2 + 1; y2 = y2 * 2 + 1;
  for (int x = x1; x <= x2; x++) { img[y1 * W + x] = c; img[y2 * W + x] = c; }
  for (int y = y1; y <= y2; y++) { img[y * W + x1] = c; img[y * W + x2] = c; }
}

// 위아래 줄을 맞바꿈. 이 카메라는 set_vflip이 듣지 않아 직접 뒤집음 (2026-11-26 실기: 로봇 앞이 화면 아래에 나왔음)
static void flipRows(uint8_t *buf) {
  uint8_t tmp[W * 2];
  for (int y = 0; y < H / 2; y++) {
    uint8_t *a = buf + y * W * 2, *b = buf + (H - 1 - y) * W * 2;
    memcpy(tmp, a, W * 2); memcpy(a, b, W * 2); memcpy(b, tmp, W * 2);
  }
}

static void detectTask(void *) {
  const uint16_t GREEN = 0xE007, GRAY = 0x1084;  // 큰 바이트 먼저 순서로 적은 RGB565
  uint8_t a8[7], a9[7], aa[7];
  int nearX = -1, nearMiss = 255;  // 바로 전까지 아래 구역 선 x, 놓친 장 수
  for (;;) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) { delay(10); continue; }
    flipRows(fb->buf);  // 이제 화면 위 = 로봇에서 먼 쪽 (0xA8 = 먼 구역)
    // 아래(발 바로 앞) → 가운데 → 위 순서로, 바로 아래 구역 위치에서 이어지는 띠만 (JUMP_MAX)
    findLine(fb->buf, 2, nearMiss < NEAR_KEEP ? nearX : -1, aa);
    if (aa[0]) { nearX = aa[1]; nearMiss = 0; } else if (nearMiss < 255) nearMiss++;
    findLine(fb->buf, 1, aa[0] ? aa[1] : -1, a9);
    findLine(fb->buf, 0, a9[0] ? a9[1] : aa[0] ? aa[1] : -1, a8);
    portENTER_CRITICAL(&regLock);
    memcpy(trio, a8, 7); memcpy(trio + 7, a9, 7); memcpy(trio + 14, aa, 7);
    if (!a9[0]) memcpy(a9, aa, 7);  // Hiwonder 형식(0xA9): 가운데에서 놓치면 아래 구역. 중심 y(80~119)로 구분
    memcpy(regs[0xA8], a8, 7);
    memcpy(regs[0xA9], a9, 7);
    portEXIT_CRITICAL(&regLock);
    frames++;
    if ((streaming || webClients) && xSemaphoreTake(shotFree, 0) == pdTRUE) {
      memcpy(shot, fb->buf, W * H * 2);
      for (int x = 0; x < W; x++) { shot[BAND * 2 * W + x] = GRAY; shot[BAND * 4 * W + x] = GRAY; }
      if (a8[0]) rect(shot, a8[3], a8[4], a8[5], a8[6], GREEN);
      if (a9[0]) rect(shot, a9[3], a9[4], a9[5], a9[6], GREEN);
      if (aa[0]) rect(shot, aa[3], aa[4], aa[5], aa[6], GREEN);
      xSemaphoreGive(shotReady);
    }
    esp_camera_fb_return(fb);
  }
}

// ---------- 영상 보내기 ----------
static void streamTask(void *) {
  for (;;) {
    if (xSemaphoreTake(shotReady, portMAX_DELAY) != pdTRUE) continue;
    uint8_t *jpg = NULL;
    size_t len = 0;
    if (fmt2jpg((uint8_t *)shot, W * H * 2, W, H, PIXFORMAT_RGB565, 40, &jpg, &len)) {
      if (streaming) Serial.write(jpg, len);
      if (webClients) {  // 마지막 장만 남김 (보내는 쪽이 늦으면 건너뜀)
        xSemaphoreTake(webLock, portMAX_DELAY);
        free(webJpg);
        webJpg = jpg; webLen = len; webSeq++;
        jpg = NULL;
        xSemaphoreGive(webLock);
      }
    }
    free(jpg);
    xSemaphoreGive(shotFree);
  }
}

// shortcut: httpd 태스크 하나가 이 함수에 머무르므로 한 번에 한 곳(Pi의 view.py)만 받음. 여러 곳이 볼 때는 Pi가 다시 나눠 줌
// 한 장(머리 + JPEG + 줄바꿈)을 한 번에 보냄. 조각으로 나눠 보내면 약한 신호에서 앞 조각 확인을 기다리느라
// 수백 ms씩 멈춤 (2026-11-26 -71 dBm에서 초당 0.5~7.5장으로 흔들림)
static bool sendAll(httpd_req_t *req, const char *p, size_t n) {
  while (n) {
    int k = httpd_send(req, p, n);
    if (k <= 0) return false;
    p += k; n -= k;
  }
  return true;
}

static esp_err_t streamHandler(httpd_req_t *req) {
  int one = 1;
  setsockopt(httpd_req_to_sockfd(req), IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);  // 작은 끝 조각도 바로 보냄
  static const char HEAD[] = "HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=f\r\nCache-Control: no-cache\r\n\r\n";
  if (!sendAll(req, HEAD, sizeof HEAD - 1)) return ESP_FAIL;
  // view.py는 USB가 없을 때만 Wi-Fi로 붙음. USB를 뽑아도 모듈은 모르고 UART로 계속 보내 Wi-Fi가 막힘
  // (2026-11-26: USB와 같이 보내면 초당 5.2장 · 핑 279 ms, Wi-Fi만이면 8.5장 · 36 ms)
  streaming = false;
  webClients++;
  uint32_t seen = webSeq;
  uint8_t *buf = NULL;
  size_t cap = 0;
  uint32_t t0 = millis();
  for (;;) {
    if (webSeq == seen) {
      if (millis() - t0 > 3000) break;  // 카메라가 멈췄으면 끊고 받는 쪽이 다시 붙게
      delay(5);
      continue;
    }
    const size_t HMAX = 80;
    xSemaphoreTake(webLock, portMAX_DELAY);
    size_t len = webLen;
    seen = webSeq;
    if (HMAX + len + 2 > cap) { free(buf); cap = HMAX + len + 2; buf = (uint8_t *)malloc(cap); if (!buf) cap = 0; }
    int n = 0;
    if (buf) {
      n = snprintf((char *)buf, HMAX, "--f\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", (unsigned)len);
      memcpy(buf + n, webJpg, len);
    }
    xSemaphoreGive(webLock);
    if (!buf) break;
    memcpy(buf + n + len, "\r\n", 2);
    if (!sendAll(req, (const char *)buf, n + len + 2)) break;
    t0 = millis();
  }
  free(buf);
  webClients--;
  return ESP_FAIL;  // 끊김 (연결을 닫음)
}

// Wi-Fi로 굽기: POST /update (헤더 X-Key, 본문 = 앱 bin). 다른 칸에 받아 검증 뒤에만 바꾸니 중간에 끊겨도 원래대로 켜짐.
// 열쇠(OTAKEY 명령, 저장됨)가 없으면 받지 않음. 웹 서버가 한 번에 요청 하나라 영상(/stream)을 닫아야 시작됨
static char otaKey[33] = "";

static esp_err_t updateHandler(httpd_req_t *req) {
  char hdr[33] = "";
  if (!otaKey[0] || httpd_req_get_hdr_value_str(req, "X-Key", hdr, sizeof(hdr)) != ESP_OK || strcmp(hdr, otaKey)) {
    httpd_resp_send_err(req, HTTPD_403_FORBIDDEN, "key");
    return ESP_FAIL;
  }
  if (!Update.begin(req->content_len)) {
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, Update.errorString());
    return ESP_FAIL;
  }
  static uint8_t buf[4096];
  int left = req->content_len, waits = 0;
  while (left > 0) {
    int r = httpd_req_recv(req, (char *)buf, min(left, (int)sizeof(buf)));
    if (r == HTTPD_SOCK_ERR_TIMEOUT && ++waits < 6) continue;  // 약한 Wi-Fi에서 잠깐 멈추는 건 기다림 (5초 × 6)
    if (r <= 0 || Update.write(buf, r) != (size_t)r) {
      Update.abort();
      httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "write");
      return ESP_FAIL;
    }
    left -= r; waits = 0;
  }
  if (!Update.end(true)) {
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, Update.errorString());
    return ESP_FAIL;
  }
  httpd_resp_sendstr(req, "ok");
  delay(300);
  ESP.restart();
  return ESP_OK;
}

// 저장된 Wi-Fi로 붙고 /stream을 연다. 이름이 없으면 Wi-Fi를 끔
static void startWifi() {
  String ssid = prefs.getString("ssid", ""), pass = prefs.getString("pass", "");
  if (!ssid.length()) { WiFi.mode(WIFI_OFF); return; }
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);
  WiFi.setSleep(false);  // 절전 켜면 영상이 100 ms 넘게 밀림
  WiFi.begin(ssid.c_str(), pass.c_str());
  WiFi.setTxPower((wifi_power_t)prefs.getChar("txp", WIFI_POWER_19_5dBm));
  if (httpd) return;
  httpd_config_t c = HTTPD_DEFAULT_CONFIG();
  c.core_id = 0;  // 선 찾기(코어 1)와 떨어뜨림
  httpd_uri_t u = {};
  u.uri = "/stream"; u.method = HTTP_GET; u.handler = streamHandler;
  httpd_uri_t up = {};
  up.uri = "/update"; up.method = HTTP_POST; up.handler = updateHandler;
  if (httpd_start(&httpd, &c) == ESP_OK) {
    httpd_register_uri_handler(httpd, &u);
    httpd_register_uri_handler(httpd, &up);
  }
}

static bool startCamera() {
  camera_config_t c = {};
  c.pin_pwdn = -1; c.pin_reset = -1;
  c.pin_xclk = XCLK_PIN; c.pin_sccb_sda = SIOD_PIN; c.pin_sccb_scl = SIOC_PIN;
  c.pin_d0 = DATA_PINS[0]; c.pin_d1 = DATA_PINS[1]; c.pin_d2 = DATA_PINS[2]; c.pin_d3 = DATA_PINS[3];
  c.pin_d4 = DATA_PINS[4]; c.pin_d5 = DATA_PINS[5]; c.pin_d6 = DATA_PINS[6]; c.pin_d7 = DATA_PINS[7];
  c.pin_vsync = VSYNC_PIN; c.pin_href = HREF_PIN; c.pin_pclk = PCLK_PIN;
  c.xclk_freq_hz = 15000000;  // Hiwonder 예제 값
  c.ledc_channel = LEDC_CHANNEL_0; c.ledc_timer = LEDC_TIMER_0;
  c.pixel_format = PIXFORMAT_RGB565;
  c.frame_size = FRAMESIZE_QVGA;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;
  if (esp_camera_init(&c) != ESP_OK) return false;
  // 좌우 뒤집기 (2026-11-26 실기로 좌우 맞음 확인). 상하는 detectTask의 flipRows가 맡음
  sensor_t *s = esp_camera_sensor_get();
  s->set_hmirror(s, 1);
  return true;
}

void setup() {
  Serial.setTxBufferSize(16384);
  Serial.begin(BAUD);
  prefs.begin("patrolcam", false);
  thr = prefs.getUChar("thr", THR_DEFAULT);
  prefs.getString("otakey", otaKey, sizeof(otaKey));
  bool cam = startCamera();
  shot = (uint16_t *)ps_malloc(W * H * 2);
  shotReady = xSemaphoreCreateBinary();
  shotFree = xSemaphoreCreateBinary();
  xSemaphoreGive(shotFree);
  webLock = xSemaphoreCreateMutex();
  startWifi();
  Wire.onReceive(onReceive);
  Wire.onRequest(onRequest);
  Wire.begin((uint8_t)I2C_ADDR, I2C_SDA, I2C_SCL, 100000);
  Serial.printf("\nINFO patrol_cam camera=%s psram=%d thr=%d\n", cam ? "ok" : "FAIL", ESP.getPsramSize(), thr);
  if (!cam || !shot) return;  // 카메라가 없으면 레지스터는 0으로 둠 (MechDog은 선 못 찾음으로 읽음)
  xTaskCreatePinnedToCore(detectTask, "detect", 8192, NULL, 5, NULL, 1);
  xTaskCreatePinnedToCore(streamTask, "stream", 8192, NULL, 3, NULL, 0);
}

void loop() {
  static String line;
  static uint32_t t0 = millis(), f0 = 0;
  static bool mdns = false;
  if (!mdns && WiFi.status() == WL_CONNECTED && MDNS.begin(HOSTNAME)) {  // 붙은 뒤에 이름을 알림
    MDNS.addService("http", "tcp", 80);
    mdns = true;
  }
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch != '\n') { if (line.length() < 120) line += ch; continue; }
    line.trim();
    if (line == "STREAM 1") streaming = true;
    else if (line == "STREAM 0") streaming = false;
    else if (line.startsWith("THR ")) {
      int v = line.substring(4).toInt();
      if (v >= 1 && v <= 254) { thr = v; prefs.putUChar("thr", v); }
    } else if (line == "SCAN") {
      if (WiFi.getMode() == WIFI_OFF) WiFi.mode(WIFI_STA);
      int n = WiFi.scanNetworks();
      for (int i = 0; i < n; i++) Serial.printf("\nSCAN %d %s\n", WiFi.RSSI(i), WiFi.SSID(i).c_str());
      if (!prefs.getString("ssid", "").length()) WiFi.mode(WIFI_OFF);
    } else if (line.startsWith("WIFI")) {
      String v = line.substring(5);
      int tab = v.indexOf('\t');
      prefs.putString("ssid", tab < 0 ? v : v.substring(0, tab));
      prefs.putString("pass", tab < 0 ? "" : v.substring(tab + 1));
      WiFi.disconnect();
      mdns = false;
      startWifi();
    } else if (line.startsWith("TXP ")) {
      int v = line.substring(4).toInt();
      if (v >= 8 && v <= 84) { prefs.putChar("txp", v); WiFi.setTxPower((wifi_power_t)v); }
    } else if (line.startsWith("OTAKEY")) {  // 이름 없이 OTAKEY면 지움 (Wi-Fi 굽기 끔)
      String v = line.length() > 7 ? line.substring(7) : "";
      if (v.length() < sizeof(otaKey)) { prefs.putString("otakey", v); strcpy(otaKey, v.c_str()); }
    }
    if (line.length()) {  // 영상 중에도 JPEG 사이에 끼어 나감 (받는 쪽은 FFD8~FFD9만 잘라 씀)
      uint8_t a9[7];
      portENTER_CRITICAL(&regLock);
      memcpy(a9, regs[0xA9], 7);
      portEXIT_CRITICAL(&regLock);
      float fps = (frames - f0) * 1000.0 / max(1UL, millis() - t0);
      String wifi = WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : prefs.getString("ssid", "").length() ? "connecting" : "off";
      Serial.printf("\nINFO stream=%d thr=%d fps=%.1f a9=%d,%d,%d wifi=%s web=%d rssi=%d txp=%d\n", streaming, thr, fps, a9[0], a9[1], a9[2],
                    wifi.c_str(), webClients, (int)WiFi.RSSI(), (int)WiFi.getTxPower());
      t0 = millis(); f0 = frames;
    }
    line = "";
  }
  delay(5);
}
