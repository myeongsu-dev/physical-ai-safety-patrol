// XIAO ESP32-S3 Sense 머리 카메라 → Pi 5 (USB 시리얼로 JPEG를 그대로 흘려보냄)
// Pi 쪽: edge/camera.py 가 FFD8…FFD9로 잘라 가장 최근 장만 씀
//
// Arduino IDE: 보드 "XIAO_ESP32S3", Tools → USB CDC On Boot: Enabled, PSRAM: OPI PSRAM
// 확인: python -m edge.hwcheck cam  → 초당 15장 근처, cam_check.jpg 확인
#include "esp_camera.h"

// CAMERA_MODEL_XIAO_ESP32S3 핀 (OV2640/OV3660)
#define PWDN_GPIO_NUM -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 10
#define SIOD_GPIO_NUM 40
#define SIOC_GPIO_NUM 39
#define Y9_GPIO_NUM 48
#define Y8_GPIO_NUM 11
#define Y7_GPIO_NUM 12
#define Y6_GPIO_NUM 14
#define Y5_GPIO_NUM 16
#define Y4_GPIO_NUM 18
#define Y3_GPIO_NUM 17
#define Y2_GPIO_NUM 15
#define VSYNC_GPIO_NUM 38
#define HREF_GPIO_NUM 47
#define PCLK_GPIO_NUM 13

const framesize_t FRAME = FRAMESIZE_VGA;  // 640×480
const int QUALITY = 12;                    // 낮을수록 화질↑ 크기↑ (10~15)
const uint32_t FRAME_GAP_MS = 60;          // 약 15장/초
const bool FLIP = true;                    // 카메라를 거꾸로 달았으면 true: 위아래·좌우를 함께 뒤집어 180° 돌림

void setup() {
  Serial.setTxBufferSize(32 * 1024);
  Serial.begin(2000000);  // USB CDC라 숫자는 실제 속도와 무관

  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAME;
  c.jpeg_quality = QUALITY;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;  // 밀린 장 대신 최신 장

  while (esp_camera_init(&c) != ESP_OK) {
    delay(1000);  // 카메라를 못 찾으면 1초마다 다시 (Pi는 장이 안 오는 것으로 앎)
  }
  sensor_t *s = esp_camera_sensor_get();
  if (s && FLIP) {
    s->set_vflip(s, 1);
    s->set_hmirror(s, 1);
  }
}

void loop() {
  uint32_t t = millis();
  camera_fb_t *fb = esp_camera_fb_get();
  if (fb) {
    Serial.write(fb->buf, fb->len);  // JPEG 그대로: FFD8 … FFD9
    esp_camera_fb_return(fb);
  }
  uint32_t used = millis() - t;
  if (used < FRAME_GAP_MS) delay(FRAME_GAP_MS - used);
}
