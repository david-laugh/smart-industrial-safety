/*
 * camera_probe — XIAO ESP32S3 Sense camera bring-up test (P0/P1)
 *
 * Camera only: no Wi-Fi, no Edge Impulse. Reports
 *   - PSRAM detection and size
 *   - esp_camera_init() result (e.g. 0x105 ESP_ERR_NOT_FOUND = sensor did not answer on SCCB)
 *   - detected sensor PID / model
 *   - JPEG capture size, SOI/EOI markers, capture time
 *   - heap / PSRAM free over time (frame buffers must always be returned)
 *
 * Board: XIAO_ESP32S3, PSRAM: OPI PSRAM, USB CDC On Boot: Enabled
 * Pins: CAMERA_MODEL_XIAO_ESP32S3 from arduino-esp32 CameraWebServer/camera_pins.h
 */
#include "esp_camera.h"

// CAMERA_MODEL_XIAO_ESP32S3
#define PWDN_GPIO_NUM  -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  10
#define SIOD_GPIO_NUM  40
#define SIOC_GPIO_NUM  39
#define Y9_GPIO_NUM    48
#define Y8_GPIO_NUM    11
#define Y7_GPIO_NUM    12
#define Y6_GPIO_NUM    14
#define Y5_GPIO_NUM    16
#define Y4_GPIO_NUM    18
#define Y3_GPIO_NUM    17
#define Y2_GPIO_NUM    15
#define VSYNC_GPIO_NUM 38
#define HREF_GPIO_NUM  47
#define PCLK_GPIO_NUM  13

static const uint32_t INIT_RETRY_MS = 5000;
static const uint32_t CAPTURE_INTERVAL_MS = 2000;

static bool camera_ready = false;
static uint32_t frames_ok = 0;
static uint32_t frames_failed = 0;

static void print_memory(const char *tag) {
  Serial.printf("[mem] %s heap_free=%u heap_min=%u psram_free=%u psram_min=%u\n", tag,
                ESP.getFreeHeap(), ESP.getMinFreeHeap(), ESP.getFreePsram(), ESP.getMinFreePsram());
}

static bool init_camera() {
  camera_config_t config = {};
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.ledc_timer = LEDC_TIMER_0;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA;
  config.jpeg_quality = 12;
  config.fb_count = 1;
  config.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  config.grab_mode = CAMERA_GRAB_LATEST;

  uint32_t t0 = millis();
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("[camera] init FAILED err=0x%x (%s) after %lu ms\n", err, esp_err_to_name(err),
                  (unsigned long)(millis() - t0));
    if (err == ESP_ERR_NOT_FOUND) {
      Serial.println("[camera] 0x105: no sensor answered on SCCB (SDA=40, SCL=39). "
                     "Check the B2B connector and camera ribbon before suspecting software.");
    }
    esp_camera_deinit();
    return false;
  }

  sensor_t *s = esp_camera_sensor_get();
  if (s == nullptr) {
    Serial.println("[camera] init OK but sensor handle is null");
    return false;
  }
  camera_sensor_info_t *info = esp_camera_sensor_get_info(&s->id);
  Serial.printf("[camera] init OK in %lu ms, PID=0x%04x model=%s\n", (unsigned long)(millis() - t0),
                s->id.PID, info ? info->name : "unknown");
  return true;
}

static void capture_once() {
  uint32_t t0 = millis();
  camera_fb_t *fb = esp_camera_fb_get();
  uint32_t dt = millis() - t0;
  if (fb == nullptr) {
    frames_failed++;
    Serial.printf("[capture] FAILED (ok=%lu failed=%lu)\n", (unsigned long)frames_ok, (unsigned long)frames_failed);
    return;
  }

  bool soi = fb->len >= 2 && fb->buf[0] == 0xFF && fb->buf[1] == 0xD8;
  bool eoi = fb->len >= 2 && fb->buf[fb->len - 2] == 0xFF && fb->buf[fb->len - 1] == 0xD9;
  size_t len = fb->len;
  size_t width = fb->width;
  size_t height = fb->height;
  esp_camera_fb_return(fb);

  frames_ok++;
  Serial.printf("[capture] #%lu %ux%u jpeg=%u bytes SOI=%d EOI=%d in %lu ms (failed=%lu)\n",
                (unsigned long)frames_ok, (unsigned)width, (unsigned)height, (unsigned)len, soi, eoi,
                (unsigned long)dt, (unsigned long)frames_failed);
}

void setup() {
  Serial.begin(115200);
  uint32_t wait_start = millis();
  while (!Serial && millis() - wait_start < 3000) {
    delay(10);
  }
  Serial.println();
  Serial.println("=== camera_probe (XIAO ESP32S3 Sense) ===");
  Serial.printf("[chip] %s rev %d, %d cores, %lu MHz, flash %u bytes\n", ESP.getChipModel(), ESP.getChipRevision(),
                ESP.getChipCores(), (unsigned long)ESP.getCpuFreqMHz(), ESP.getFlashChipSize());
  Serial.printf("[psram] found=%d size=%u bytes\n", psramFound(), ESP.getPsramSize());
  if (!psramFound()) {
    Serial.println("[psram] not found: set Tools > PSRAM > OPI PSRAM");
  }
  print_memory("boot");
  camera_ready = init_camera();
  print_memory("after-init");
}

void loop() {
  static uint32_t last_ms = 0;
  uint32_t now = millis();

  if (!camera_ready) {
    if (now - last_ms >= INIT_RETRY_MS) {
      last_ms = now;
      camera_ready = init_camera();
    }
    return;
  }

  if (now - last_ms >= CAPTURE_INTERVAL_MS) {
    last_ms = now;
    capture_once();
    if (frames_ok % 10 == 0) {
      print_memory("periodic");
    }
  }
}
