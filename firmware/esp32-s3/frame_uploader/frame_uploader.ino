// frame_uploader.ino — XIAO ESP32-S3 Sense → Raspberry Pi Gateway 프레임 전송 (최소 구현)
//
// 계약: contracts/esp32-frame-metadata.schema.json 1.0.0, docs/protocols.md §1
// - 500ms 주기 촬영(VGA JPEG q12) → multipart(metadata → image) POST /api/v1/frames
// - JPEG 는 프레임 버퍼에서 바로 소켓으로 스트리밍(복사·Base64 없음)
// - 실패한 프레임은 쌓지 않는다. 5xx·네트워크 오류만 1회 즉시 재시도.
//
// 보드: esp32:esp32:XIAO_ESP32S3, PSRAM=OPI PSRAM (README.md 참고)

#include <Arduino.h>
#include <WiFi.h>
#include <esp_camera.h>
#include <esp_sntp.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <inttypes.h>
#include <sys/time.h>
#include <time.h>

#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "secrets.h 가 없습니다. secrets.example.h 를 secrets.h 로 복사해서 값을 채우세요."
#endif

#ifndef NTP_SERVER
#define NTP_SERVER "pool.ntp.org"
#endif

// 1 로 켜면 detectPerson() 이 true 일 때만 전송한다(trigger=EDGE_PERSON_DETECTED).
#ifndef USE_EDGE_DETECTION
#define USE_EDGE_DETECTION 0
#endif
#ifndef EDGE_MODEL_ID
#define EDGE_MODEL_ID "stub-none"
#endif

// ---------------------------------------------------------------- 설정 상수
static const char* const SCHEMA_VERSION   = "1.0.0";
static const char* const FIRMWARE_VERSION = "0.1.0";

static const framesize_t FRAME_SIZE      = FRAMESIZE_VGA;  // 640x480
static const char* const FRAME_SIZE_NAME = "VGA";
static const int JPEG_QUALITY            = 12;
static const int FB_COUNT                = 2;

static const uint32_t CAPTURE_INTERVAL_MS    = 500;
static const uint32_t HTTP_CONNECT_TIMEOUT_MS = 3000;
static const uint32_t HTTP_RESPONSE_TIMEOUT_MS = 5000;
static const uint32_t DEFAULT_RETRY_AFTER_MS = 1000;   // 429 인데 retry_after_ms 가 없을 때
static const uint32_t MAX_RETRY_AFTER_MS     = 60000;  // 비정상적으로 큰 값 방어
static const uint32_t WIFI_RECONNECT_MS      = 10000;
static const size_t   MAX_IMAGE_BYTES        = 1048576;  // 스키마 byte_length 상한
static const size_t   WRITE_CHUNK            = 4096;
static const uint32_t STATS_EVERY_FRAMES     = 100;

// XIAO ESP32-S3 Sense (OV2640/OV3660) 핀맵
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

// ---------------------------------------------------------------- 상태
static char     g_bootId[9];          // 부팅마다 랜덤 32비트 hex
static uint32_t g_frameSeq = 0;       // 부팅 후 0부터, 촬영한 프레임마다 +1
static uint32_t g_nextCaptureAt = 0;  // millis()
static volatile bool g_sntpSynced = false;
static bool     g_wifiWasConnected = false;
static uint32_t g_lastWifiAttempt = 0;
static uint32_t g_statSent = 0, g_statAccepted = 0, g_statFailed = 0;

struct EdgeResult {
  bool  enabled;
  bool  personDetected;
  float personScore;
  float latencyMs;
};

struct SendResult {
  int      http;          // HTTP 상태 코드, 네트워크 오류면 -1
  long     retryAfterMs;  // 응답의 retry_after_ms, 없거나 null 이면 -1
  char     errorCode[32]; // 응답의 error.code (로그용)
  uint32_t elapsedMs;
};

// ---------------------------------------------------------------- 사람 감지 자리
// Edge Impulse 등으로 교체할 자리. 지금은 항상 false(사람 없음).
// score 는 0~1. 최종 PPE 판정은 Raspberry Pi 가 한다.
bool detectPerson(camera_fb_t* fb, float* score) {
  (void)fb;
  if (score) *score = 0.0f;
  return false;
}

// ---------------------------------------------------------------- 시간
static void onTimeSync(struct timeval* tv) {
  (void)tv;
  if (!g_sntpSynced) Serial.println("[time] SNTP synced");
  g_sntpSynced = true;
}

static uint64_t uptimeMs() { return (uint64_t)(esp_timer_get_time() / 1000); }

// 프레임 버퍼 timestamp(esp_timer 기준)를 uptime ms 로. 비어 있으면 현재 uptime.
static uint64_t frameUptimeMs(const camera_fb_t* fb) {
  uint64_t us = (uint64_t)fb->timestamp.tv_sec * 1000000ULL + (uint64_t)fb->timestamp.tv_usec;
  return us ? us / 1000 : uptimeMs();
}

// 동기화돼 있으면 촬영 시각(UTC, ms)을 RFC 3339 'Z' 로 쓰고 true.
static bool captureTsUtc(uint64_t captureUptimeMs, char* out, size_t n) {
  if (!g_sntpSynced) return false;
  struct timeval now;
  gettimeofday(&now, nullptr);
  if (now.tv_sec < 1704067200) return false;  // 2024-01-01 이전이면 신뢰하지 않음
  int64_t nowMs = (int64_t)now.tv_sec * 1000 + now.tv_usec / 1000;
  int64_t ageMs = (int64_t)(uptimeMs() - captureUptimeMs);
  if (ageMs < 0) ageMs = 0;
  int64_t capMs = nowMs - ageMs;
  time_t sec = (time_t)(capMs / 1000);
  struct tm tm;
  gmtime_r(&sec, &tm);
  size_t len = strftime(out, n, "%Y-%m-%dT%H:%M:%S", &tm);
  snprintf(out + len, n - len, ".%03dZ", (int)(capMs % 1000));
  return true;
}

// ---------------------------------------------------------------- 메타데이터
// device_id(소문자·숫자·하이픈), EDGE_MODEL_ID 등은 JSON 이스케이프가 필요 없는 값만 쓴다.
static int buildMetadata(char* out, size_t n, uint32_t seq, uint64_t captureUptimeMs,
                         const char* tsOrNull, const char* trigger, const camera_fb_t* fb,
                         const EdgeResult& e) {
  char timeSync[96];
  if (tsOrNull) {
    snprintf(timeSync, sizeof(timeSync),
             "{\"synced\":true,\"source\":\"SNTP\",\"capture_ts_utc\":\"%s\"}", tsOrNull);
  } else {
    snprintf(timeSync, sizeof(timeSync),
             "{\"synced\":false,\"source\":\"NONE\",\"capture_ts_utc\":null}");
  }

  char edge[192];
  if (e.enabled) {
    snprintf(edge, sizeof(edge),
             "{\"enabled\":true,\"person_detected\":%s,\"person_score\":%.3f,"
             "\"model_id\":\"%s\",\"latency_ms\":%.1f}",
             e.personDetected ? "true" : "false", constrain(e.personScore, 0.0f, 1.0f),
             EDGE_MODEL_ID, e.latencyMs);
  } else {
    snprintf(edge, sizeof(edge),
             "{\"enabled\":false,\"person_detected\":null,\"person_score\":null,"
             "\"model_id\":null,\"latency_ms\":null}");
  }

  return snprintf(out, n,
                  "{\"schema_version\":\"%s\",\"device_id\":\"%s\",\"boot_id\":\"%s\","
                  "\"frame_seq\":%" PRIu32 ",\"mcu_uptime_ms\":%" PRIu64 ","
                  "\"time_sync\":%s,\"trigger\":\"%s\","
                  "\"image\":{\"content_type\":\"image/jpeg\",\"width\":%u,\"height\":%u,"
                  "\"byte_length\":%u,\"jpeg_quality\":%d,\"frame_size\":\"%s\"},"
                  "\"edge_detection\":%s,\"firmware_version\":\"%s\"}",
                  SCHEMA_VERSION, DEVICE_ID, g_bootId, seq, captureUptimeMs, timeSync, trigger,
                  (unsigned)fb->width, (unsigned)fb->height, (unsigned)fb->len, JPEG_QUALITY,
                  FRAME_SIZE_NAME, edge, FIRMWARE_VERSION);
}

// ---------------------------------------------------------------- HTTP
static bool writeAll(WiFiClient& c, const uint8_t* p, size_t n) {
  while (n > 0) {
    size_t w = c.write(p, n > WRITE_CHUNK ? WRITE_CHUNK : n);
    if (w == 0) return false;
    p += w;
    n -= w;
    yield();
  }
  return true;
}

// 한 줄 읽기(CRLF 제거). deadline 지나면 false.
static bool readLine(WiFiClient& c, char* buf, size_t n, uint32_t deadline) {
  size_t i = 0;
  while ((int32_t)(millis() - deadline) < 0) {
    if (!c.available()) {
      if (!c.connected()) break;
      delay(1);
      continue;
    }
    int ch = c.read();
    if (ch < 0) continue;
    if (ch == '\n') {
      if (i > 0 && buf[i - 1] == '\r') i--;
      buf[i] = '\0';
      return true;
    }
    if (i + 1 < n) buf[i++] = (char)ch;
  }
  buf[i] = '\0';
  return i > 0;
}

static long jsonIntField(const char* body, const char* key) {
  const char* p = strstr(body, key);
  if (!p) return -1;
  p = strchr(p + strlen(key), ':');
  if (!p) return -1;
  while (*++p == ' ') {}
  return isdigit((unsigned char)*p) ? strtol(p, nullptr, 10) : -1;  // null → -1
}

static void jsonErrorCode(const char* body, char* out, size_t n) {
  out[0] = '\0';
  const char* p = strstr(body, "\"code\"");
  if (!p || !(p = strchr(p + 6, '"'))) return;
  const char* q = strchr(++p, '"');
  if (!q) return;
  size_t len = min((size_t)(q - p), n - 1);
  memcpy(out, p, len);
  out[len] = '\0';
}

static void readResponse(WiFiClient& c, SendResult& r) {
  uint32_t deadline = millis() + HTTP_RESPONSE_TIMEOUT_MS;
  char line[160];
  if (!readLine(c, line, sizeof(line), deadline)) return;  // r.http = -1 유지
  int code = 0;
  if (sscanf(line, "HTTP/%*s %d", &code) != 1) return;
  r.http = code;

  long contentLength = -1;
  while (readLine(c, line, sizeof(line), deadline) && line[0] != '\0') {
    if (strncasecmp(line, "Content-Length:", 15) == 0) contentLength = strtol(line + 15, nullptr, 10);
  }
  char body[512];
  size_t want = contentLength >= 0 ? min((size_t)contentLength, sizeof(body) - 1) : sizeof(body) - 1;
  size_t got = 0;
  while (got < want && (int32_t)(millis() - deadline) < 0) {
    if (c.available()) {
      int k = c.read((uint8_t*)body + got, want - got);
      if (k > 0) got += k;
    } else if (!c.connected()) {
      break;
    } else {
      delay(1);
    }
  }
  body[got] = '\0';
  r.retryAfterMs = jsonIntField(body, "\"retry_after_ms\"");
  jsonErrorCode(body, r.errorCode, sizeof(r.errorCode));
}

// multipart: metadata(application/json) → image(image/jpeg, frame.jpg)
static SendResult postFrame(const char* meta, size_t metaLen, const camera_fb_t* fb) {
  SendResult r = {-1, -1, "", 0};
  uint32_t t0 = millis();

  char boundary[40];
  snprintf(boundary, sizeof(boundary), "----esp32frame%s%08" PRIx32, g_bootId, esp_random());

  char partMeta[160];
  int partMetaLen = snprintf(partMeta, sizeof(partMeta),
                             "--%s\r\nContent-Disposition: form-data; name=\"metadata\"\r\n"
                             "Content-Type: application/json\r\n\r\n", boundary);
  char partImage[192];
  int partImageLen = snprintf(partImage, sizeof(partImage),
                              "\r\n--%s\r\nContent-Disposition: form-data; name=\"image\"; "
                              "filename=\"frame.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n", boundary);
  char tail[64];
  int tailLen = snprintf(tail, sizeof(tail), "\r\n--%s--\r\n", boundary);
  size_t contentLength = partMetaLen + metaLen + partImageLen + fb->len + tailLen;

  char head[384];
  int headLen = snprintf(head, sizeof(head),
                         "POST /api/v1/frames HTTP/1.1\r\nHost: %s:%d\r\n"
                         "Authorization: Bearer %s\r\n"
                         "Content-Type: multipart/form-data; boundary=%s\r\n"
                         "Content-Length: %u\r\nConnection: close\r\n\r\n",
                         GATEWAY_HOST, GATEWAY_PORT, DEVICE_TOKEN, boundary, (unsigned)contentLength);

  WiFiClient client;
  client.setNoDelay(true);
  if (!client.connect(GATEWAY_HOST, GATEWAY_PORT, HTTP_CONNECT_TIMEOUT_MS)) {
    r.elapsedMs = millis() - t0;
    return r;
  }
  bool ok = writeAll(client, (const uint8_t*)head, headLen) &&
            writeAll(client, (const uint8_t*)partMeta, partMetaLen) &&
            writeAll(client, (const uint8_t*)meta, metaLen) &&
            writeAll(client, (const uint8_t*)partImage, partImageLen) &&
            writeAll(client, fb->buf, fb->len) &&  // 프레임 버퍼에서 바로 전송
            writeAll(client, (const uint8_t*)tail, tailLen);
  // 쓰기 도중 끊겨도 서버가 먼저 보낸 오류 응답(401/413 등)이 있으면 읽는다.
  if (ok || client.available()) readResponse(client, r);
  client.stop();
  r.elapsedMs = millis() - t0;
  return r;
}

// ---------------------------------------------------------------- Wi-Fi
static bool ensureWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    if (!g_wifiWasConnected) {
      Serial.printf("[wifi] connected ip=%s rssi=%d\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
      g_wifiWasConnected = true;
    }
    return true;
  }
  if (g_wifiWasConnected) {
    Serial.println("[wifi] disconnected");
    g_wifiWasConnected = false;
    g_lastWifiAttempt = millis();
  }
  if (millis() - g_lastWifiAttempt >= WIFI_RECONNECT_MS) {
    g_lastWifiAttempt = millis();
    Serial.println("[wifi] reconnecting...");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }
  return false;
}

// ---------------------------------------------------------------- 카메라
static bool initCamera() {
  if (!psramFound()) {
    Serial.println("[cam] PSRAM not found (Tools > PSRAM: OPI PSRAM)");
    return false;
  }
  camera_config_t c = {};
  c.pin_pwdn = PWDN_GPIO_NUM;
  c.pin_reset = RESET_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM;
  c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_d7 = Y9_GPIO_NUM;
  c.pin_d6 = Y8_GPIO_NUM;
  c.pin_d5 = Y7_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM;
  c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d2 = Y4_GPIO_NUM;
  c.pin_d1 = Y3_GPIO_NUM;
  c.pin_d0 = Y2_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM;
  c.pin_href = HREF_GPIO_NUM;
  c.pin_pclk = PCLK_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.ledc_timer = LEDC_TIMER_0;
  c.ledc_channel = LEDC_CHANNEL_0;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAME_SIZE;
  c.jpeg_quality = JPEG_QUALITY;
  c.fb_count = FB_COUNT;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;  // 항상 가장 최근 프레임

  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("[cam] init failed 0x%x\n", err);
    return false;
  }
  return true;
}

// ---------------------------------------------------------------- 프레임 처리
static void processFrame() {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("[cam] capture failed");
    return;
  }
  uint32_t seq = g_frameSeq++;
  uint64_t capUptime = frameUptimeMs(fb);
  char ts[32];
  bool haveTs = captureTsUtc(capUptime, ts, sizeof(ts));

  EdgeResult edge = {false, false, 0.0f, 0.0f};
  const char* trigger = "PERIODIC";
#if USE_EDGE_DETECTION
  {
    int64_t t0 = esp_timer_get_time();
    float score = 0.0f;
    bool person = detectPerson(fb, &score);
    edge = {true, person, score, (float)(esp_timer_get_time() - t0) / 1000.0f};
    if (!person) {
      Serial.printf("[frame] seq=%" PRIu32 " no person, skip\n", seq);
      esp_camera_fb_return(fb);
      return;
    }
    trigger = "EDGE_PERSON_DETECTED";
  }
#endif

  if (fb->len == 0 || fb->len > MAX_IMAGE_BYTES) {
    Serial.printf("[frame] seq=%" PRIu32 " jpeg=%uB out of range, skip\n", seq, (unsigned)fb->len);
    esp_camera_fb_return(fb);
    return;
  }

  char meta[768];
  int metaLen = buildMetadata(meta, sizeof(meta), seq, capUptime, haveTs ? ts : nullptr, trigger, fb, edge);
  if (metaLen <= 0 || metaLen >= (int)sizeof(meta)) {
    Serial.printf("[frame] seq=%" PRIu32 " metadata overflow\n", seq);
    esp_camera_fb_return(fb);
    return;
  }

  SendResult r = postFrame(meta, metaLen, fb);
  bool retried = false;
  if (r.http < 0 || r.http >= 500) {  // 네트워크 오류·5xx: 1회만 즉시 재시도
    Serial.printf("[frame] seq=%" PRIu32 " http=%d ms=%" PRIu32 " -> retry once\n", seq, r.http, r.elapsedMs);
    r = postFrame(meta, metaLen, fb);
    retried = true;
  }
  size_t jpegLen = fb->len;
  esp_camera_fb_return(fb);  // 실패해도 프레임은 버린다(큐에 쌓지 않음)

  g_statSent++;
  Serial.printf("[frame] seq=%" PRIu32 " jpeg=%uB http=%d ms=%" PRIu32 "%s%s%s\n", seq, (unsigned)jpegLen,
                r.http, r.elapsedMs, retried ? " retried" : "", r.errorCode[0] ? " err=" : "", r.errorCode);

  switch (r.http) {
    case 202:
      g_statAccepted++;
      break;
    case 429: {
      uint32_t wait = r.retryAfterMs >= 0 ? (uint32_t)min((long)MAX_RETRY_AFTER_MS, r.retryAfterMs)
                                          : DEFAULT_RETRY_AFTER_MS;
      Serial.printf("[http] 429 backoff %" PRIu32 " ms\n", wait);
      g_nextCaptureAt = millis() + max(wait, CAPTURE_INTERVAL_MS);
      g_statFailed++;
      break;
    }
    case 400:
      Serial.println("[http] 400 rejected (firmware bug?) - not resent");
      g_statFailed++;
      break;
    case 401:
      Serial.println("[http] 401 unauthorized - check DEVICE_ID / DEVICE_TOKEN in secrets.h");
      g_statFailed++;
      break;
    case 413:
      Serial.printf("[http] 413 payload too large (jpeg=%uB) - lower quality/resolution\n", (unsigned)jpegLen);
      g_statFailed++;
      break;
    case 415:
      Serial.println("[http] 415 unsupported media type - not resent");
      g_statFailed++;
      break;
    default:
      g_statFailed++;  // 5xx·네트워크 오류(재시도 후) 또는 예상 밖 코드: 다음 프레임
      break;
  }

  if (g_statSent % STATS_EVERY_FRAMES == 0) {
    Serial.printf("[stats] sent=%" PRIu32 " accepted=%" PRIu32 " failed=%" PRIu32
                  " heap=%u psram=%u uptime=%" PRIu64 "s\n",
                  g_statSent, g_statAccepted, g_statFailed, (unsigned)ESP.getFreeHeap(),
                  (unsigned)ESP.getFreePsram(), uptimeMs() / 1000);
  }
}

// ---------------------------------------------------------------- setup / loop
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.printf("\n[boot] frame_uploader fw=%s device=%s\n", FIRMWARE_VERSION, DEVICE_ID);

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // 전송 지연 감소
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  g_lastWifiAttempt = millis();

  // RF 가 켜진 뒤의 esp_random() 은 하드웨어 난수
  snprintf(g_bootId, sizeof(g_bootId), "%08" PRIx32, esp_random());
  Serial.printf("[boot] boot_id=%s edge_detection=%d\n", g_bootId, USE_EDGE_DETECTION);

  sntp_set_time_sync_notification_cb(onTimeSync);
  configTime(0, 0, NTP_SERVER);  // UTC

  if (!initCamera()) {
    Serial.println("[boot] camera init failed - restarting in 5 s");
    delay(5000);
    ESP.restart();
  }
  Serial.printf("[boot] camera ok heap=%u psram=%u\n", (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getFreePsram());
  g_nextCaptureAt = millis();
}

void loop() {
  if (!ensureWiFi()) {
    delay(100);
    return;
  }
  if ((int32_t)(millis() - g_nextCaptureAt) < 0) {
    delay(5);  // 워치독·Wi-Fi 태스크에 양보
    return;
  }
  // 다음 촬영 시각은 이번 촬영 시작 기준(전송이 길면 바로 다음 프레임)
  g_nextCaptureAt = millis() + CAPTURE_INTERVAL_MS;
  processFrame();
  delay(1);
}
