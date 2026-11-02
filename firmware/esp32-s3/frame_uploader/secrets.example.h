// secrets.example.h — 이 파일을 같은 폴더에 secrets.h 로 복사해서 값을 채운다.
// secrets.h 는 .gitignore 에 등록되어 있다. 실제 값은 절대 커밋하지 않는다.
#pragma once

#define WIFI_SSID      "your-ssid"
#define WIFI_PASSWORD  "your-password"

// Raspberry Pi Gateway 주소 (IP 또는 호스트명, "http://" 없이)
#define GATEWAY_HOST   "192.168.0.10"
#define GATEWAY_PORT   8080

// Gateway 토큰 파일(device_id=token)의 한 줄과 같은 값
#define DEVICE_ID      "cam-line-a-01"
#define DEVICE_TOKEN   "change-me"

// (선택) SNTP 서버. 생략하면 pool.ntp.org. Pi 를 NTP 서버로 쓰면 그 주소를 넣는다.
// #define NTP_SERVER  "192.168.0.10"
