// 비전 모듈(ESP32-S3, GC2145) 펌웨어 · 박명수
// 하는 일
//   1. 검은 선 찾기 (항상): MechDog 보드가 I2C(주소 0x53)로 읽는 Hiwonder 레지스터 형식 그대로 답한다.
//      검정은 0xA8(화면 위 구역)·0xA9(가운데 구역), 7바이트 [ID 5, 중심x, 중심y, 좌상x, 좌상y, 우하x, 우하y].
//      좌표는 160x120 기준(가운데 x=80). 다른 레지스터는 0.
//   2. 영상 보내기 (켤 때만): USB 시리얼로 JPEG를 이어 보낸다. 찾은 선 상자와 구역 경계를 그려서 보냄.
// USB 시리얼 명령 (줄 단위): STREAM 1 / STREAM 0, THR <1~254> (검정 기준 밝기, 저장됨), INFO
// 빌드·굽기는 firmware/vision/README.md
#include <Arduino.h>
#include <Wire.h>
#include <Preferences.h>
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
#define BAND (SH / 3)  // 화면을 위·가운데·아래 3구역으로 나눔 (아래 구역은 안 씀, Hiwonder와 같음)
#define BLACK_ID 5
#define COL_MIN 3      // 한 열에서 검정 칸이 이만큼 있어야 선 후보
#define AREA_MIN 40    // 구역 안 검정 칸 합이 이보다 작으면 못 찾음

static uint8_t regs[256][7];  // I2C 레지스터 (주소 → 7바이트)
static portMUX_TYPE regLock = portMUX_INITIALIZER_UNLOCKED;
static volatile uint8_t reqReg = 0;

static Preferences prefs;
static volatile uint8_t thr = 60;
static volatile bool streaming = false;
static uint16_t *shot = NULL;  // 영상용 복사본 (PSRAM)
static SemaphoreHandle_t shotReady, shotFree;
static volatile uint32_t frames = 0;

// ---------- I2C (MechDog 보드가 readfrom_mem(0x53, 레지스터, 7)로 읽음) ----------
static void onReceive(int n) {
  if (Wire.available()) reqReg = Wire.read();
  while (Wire.available()) Wire.read();
}

static void onRequest() {
  uint8_t out[7];
  portENTER_CRITICAL(&regLock);
  memcpy(out, regs[reqReg], 7);
  portEXIT_CRITICAL(&regLock);
  Wire.write(out, 7);
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
static void findLine(const uint8_t *buf, int band, uint8_t out[7]) {
  uint8_t cnt[SW], top[SW], bot[SW];
  int y0 = band * BAND, y1 = y0 + BAND;
  for (int x = 0; x < SW; x++) {
    cnt[x] = 0; top[x] = 255; bot[x] = 0;
    for (int y = y0; y < y1; y++) {
      if (luma(px(buf, x * 2, y * 2)) < thr) {
        cnt[x]++;
        if (y < top[x]) top[x] = y;
        bot[x] = y;
      }
    }
  }
  // 후보 열이 이어진 구간 중 검정 칸 합이 가장 큰 것 (한 칸 끊김은 이어 봄)
  int best = 0, bx1 = 0, bx2 = 0, x = 0;
  while (x < SW) {
    if (cnt[x] < COL_MIN) { x++; continue; }
    int s = x, sum = 0, gap = 0, e = x;
    for (; x < SW; x++) {
      if (cnt[x] >= COL_MIN) { sum += cnt[x]; e = x; gap = 0; }
      else if (++gap > 1) break;
    }
    if (sum > best) { best = sum; bx1 = s; bx2 = e; }
  }
  memset(out, 0, 7);
  if (best < AREA_MIN) return;
  uint8_t ty = 255, by = 0;
  for (int i = bx1; i <= bx2; i++) {
    if (cnt[i] < COL_MIN) continue;
    if (top[i] < ty) ty = top[i];
    if (bot[i] > by) by = bot[i];
  }
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

static void detectTask(void *) {
  const uint16_t GREEN = 0xE007, GRAY = 0x1084;  // 큰 바이트 먼저 순서로 적은 RGB565
  uint8_t a8[7], a9[7];
  for (;;) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) { delay(10); continue; }
    findLine(fb->buf, 0, a8);
    findLine(fb->buf, 1, a9);
    portENTER_CRITICAL(&regLock);
    memcpy(regs[0xA8], a8, 7);
    memcpy(regs[0xA9], a9, 7);
    portEXIT_CRITICAL(&regLock);
    frames++;
    if (streaming && xSemaphoreTake(shotFree, 0) == pdTRUE) {
      memcpy(shot, fb->buf, W * H * 2);
      for (int x = 0; x < W; x++) { shot[BAND * 2 * W + x] = GRAY; shot[BAND * 4 * W + x] = GRAY; }
      if (a8[0]) rect(shot, a8[3], a8[4], a8[5], a8[6], GREEN);
      if (a9[0]) rect(shot, a9[3], a9[4], a9[5], a9[6], GREEN);
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
    if (fmt2jpg((uint8_t *)shot, W * H * 2, W, H, PIXFORMAT_RGB565, 40, &jpg, &len) && streaming) {
      Serial.write(jpg, len);
    }
    free(jpg);
    xSemaphoreGive(shotFree);
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
  return esp_camera_init(&c) == ESP_OK;
}

void setup() {
  Serial.setTxBufferSize(16384);
  Serial.begin(BAUD);
  prefs.begin("patrolcam", false);
  thr = prefs.getUChar("thr", 60);
  bool cam = startCamera();
  shot = (uint16_t *)ps_malloc(W * H * 2);
  shotReady = xSemaphoreCreateBinary();
  shotFree = xSemaphoreCreateBinary();
  xSemaphoreGive(shotFree);
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
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch != '\n') { if (line.length() < 40) line += ch; continue; }
    line.trim();
    if (line == "STREAM 1") streaming = true;
    else if (line == "STREAM 0") streaming = false;
    else if (line.startsWith("THR ")) {
      int v = line.substring(4).toInt();
      if (v >= 1 && v <= 254) { thr = v; prefs.putUChar("thr", v); }
    }
    if (line.length()) {  // 영상 중에도 JPEG 사이에 끼어 나감 (받는 쪽은 FFD8~FFD9만 잘라 씀)
      uint8_t a9[7];
      portENTER_CRITICAL(&regLock);
      memcpy(a9, regs[0xA9], 7);
      portEXIT_CRITICAL(&regLock);
      float fps = (frames - f0) * 1000.0 / max(1UL, millis() - t0);
      Serial.printf("\nINFO stream=%d thr=%d fps=%.1f a9=%d,%d,%d\n", streaming, thr, fps, a9[0], a9[1], a9[2]);
      t0 = millis(); f0 = frames;
    }
    line = "";
  }
  delay(5);
}
