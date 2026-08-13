// smart_agri_gateway.ino — 现代化农业数据采集网关（Modbus TCP 主站 -> MQTT，无人值守增强）
//
// 功能：
//   1) Modbus TCP 主站：轮询 PC 上 pymodbus 虚拟从站（fake_greenhouse.py）
//   2) MQTT 上报：agri/pot/{uid}/state，带 NTP 时间戳
//   3) 无人值守：
//      - WiFi 断线自动重连（原土壤固件缺这步，WiFi 断后不会恢复）
//      - MQTT 断连离线缓存到 NVS(Preferences)，恢复后按时间戳补发
//      - 心跳 agri/status(online) + LWT 遗嘱(offline)，PC 端据此判网关在线/离线
//      - 看门狗 15s，死机自动重启
//      - 从站读失败 -> payload offline 标记（连续 3 次失败判离线）
//      - NTP 时间戳，断网期间也用本地时钟（若未同步 ts=0）
//
// 寄存器表（与 fake_greenhouse.py 对应）：
//   0x0000 土壤湿度 %   0x0001 温度 x10   0x0002 光照 lux
//   0x0003 EC x100      0x0004 电池电量 % 0x0005 供电状态(0市电 1电池 2断电)
//
// 依赖库：PubSubClient（库管理器搜 "PubSubClient" 装 knolleary 版）
#define MQTT_MAX_PACKET_SIZE 1024
#include <WiFi.h>
#include <PubSubClient.h>
#include <Preferences.h>
#include <time.h>
#include "esp_task_wdt.h"

// ===== WiFi / 服务器配置 =====
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";
const char* MODBUS_HOST = "your_modbus_host";   // 模拟从站所在 PC 的局域网 IP
const uint16_t MODBUS_PORT = 502;
const char* MQTT_HOST = "your_mqtt_host";       // NAS Mosquitto 的局域网 IP
const uint16_t MQTT_PORT = 1883;

// ===== 采集配置 =====
const uint8_t  POT_COUNT = 5;          // 花盆数（轮询的从站个数）
const uint8_t  BASE_UID  = 1;          // 从站起始地址
const uint16_t REG_START = 0x0000;
const uint8_t  REG_COUNT = 6;          // 湿度/温度/光照/EC/电量/供电
const unsigned long POLL_MS = 5000;    // 轮询+上报间隔
const unsigned long HEARTBEAT_MS = 10000;  // 心跳间隔
const uint8_t  OFFLINE_THRESHOLD = 3;  // 连续读失败 N 次判离线
const uint8_t  CACHE_MAX_ITEMS = 30;   // 离线缓存最大条数

// ===== 状态 =====
Preferences prefs;
WiFiClient modbusClient;   // 连模拟从站（Modbus TCP）
WiFiClient mqttNet;
PubSubClient mqtt(mqttNet);

uint16_t last_hr[8][REG_COUNT];  // 每 uid 最近一次成功值（读失败时复用）
uint8_t  fail_count[8];

// ============================================================
// 时间
// ============================================================
time_t now_ts() {
  time_t t = time(nullptr);
  return (t < 1700000000L) ? 0 : t;   // NTP 未同步返回 0
}

// ============================================================
// NVS 离线缓存：CSV 行 = uid,ts,moist,temp_x10,light,ec_x100,batt,power,offline
// ============================================================
String cache_read() { return prefs.getString("cache", ""); }

void cache_push(const String& line) {
  String c = prefs.getString("cache", "");
  c += line;
  c += "\n";
  while (true) {  // 截断到最近 CACHE_MAX_ITEMS 条
    int cnt = 0;
    for (int i = 0; i < (int)c.length(); i++) if (c[i] == '\n') cnt++;
    if (cnt <= CACHE_MAX_ITEMS) break;
    int nl = c.indexOf('\n');
    if (nl < 0) break;
    c = c.substring(nl + 1);
  }
  prefs.putString("cache", c);
}

// MQTT 恢复连接后补发缓存
void cache_flush() {
  String c = prefs.getString("cache", "");
  if (c.length() == 0) return;
  prefs.putString("cache", "");   // 先清，补发失败会重新入缓存
  int start = 0;
  while (start < (int)c.length()) {
    int nl = c.indexOf('\n', start);
    if (nl < 0) break;
    String line = c.substring(start, nl);
    start = nl + 1;
    if (line.length() == 0) continue;

    int f[9] = {0};
    int i = 0, pos = 0;
    while (i < 9 && pos <= (int)line.length()) {
      int comma = line.indexOf(',', pos);
      String tok = (comma < 0) ? line.substring(pos) : line.substring(pos, comma);
      f[i++] = tok.toInt();
      if (comma < 0) break;
      pos = comma + 1;
    }
    if (i < 9) continue;
    char topic[48], payload[160];
    snprintf(topic, sizeof(topic), "agri/pot/%d/state", f[0]);
    snprintf(payload, sizeof(payload),
             "{\"ts\":%d,\"moisture\":%d,\"temp_c\":%.1f,\"light\":%d,\"ec\":%.2f,\"batt\":%d,\"power\":%d,\"offline\":%d}",
             f[1], f[2], f[3] / 10.0f, f[4], f[5] / 100.0f, f[6], f[7], f[8]);
    if (!mqtt.publish(topic, payload, false)) {
      cache_push(line);   // 仍断，重新入缓存
      break;
    }
    delay(50);
  }
}

// ============================================================
// Modbus TCP 读保持寄存器（手写协议帧，零额外库依赖）
// ============================================================
bool modbusReadHolding(uint8_t uid, uint16_t start, uint8_t qty, uint16_t* out) {
  if (!modbusClient.connected()) {
    if (!modbusClient.connect(MODBUS_HOST, MODBUS_PORT)) return false;
    modbusClient.setTimeout(2000);
  }
  static uint16_t tid = 0;
  tid++;
  uint8_t req[12];
  req[0] = tid >> 8; req[1] = tid & 0xFF;
  req[2] = 0x00; req[3] = 0x00;
  req[4] = 0x00; req[5] = 0x06;
  req[6] = uid;
  req[7] = 0x03;
  req[8] = start >> 8; req[9] = start & 0xFF;
  req[10] = qty >> 8; req[11] = qty & 0xFF;
  modbusClient.write(req, sizeof(req));

  uint8_t hdr[9];   // MBAP(6)+unit(1)+func(1)+bytecount(1)
  unsigned long t0 = millis();
  while (modbusClient.available() < 9 && millis() - t0 < 2000) delay(1);
  if (modbusClient.available() < 9) { modbusClient.stop(); return false; }
  modbusClient.read(hdr, 9);
  if (hdr[7] == 0x83) {   // 异常响应（含断电模拟）
    Serial.printf("  uid=%d exception=%02X (power-off?)\n", uid, hdr[8]);
    modbusClient.stop();
    return false;
  }
  uint8_t n = hdr[8];
  if (n != qty * 2) { modbusClient.stop(); return false; }
  uint8_t data[32];
  t0 = millis();
  while (modbusClient.available() < n && millis() - t0 < 2000) delay(1);
  if (modbusClient.available() < n) { modbusClient.stop(); return false; }
  modbusClient.read(data, n);
  for (uint8_t i = 0; i < qty; i++) out[i] = (data[i * 2] << 8) | data[i * 2 + 1];
  return true;
}

// ============================================================
// WiFi / MQTT
// ============================================================
void setup_wifi() {
  Serial.print("Connecting WiFi");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long t = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t < 20000) {
    delay(500);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println(" connected, IP=" + WiFi.localIP().toString());
  } else {
    Serial.println(" FAILED");
  }
}

// 无人值守：WiFi 断了要重连（原固件缺这步）
void ensure_wifi() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi lost, reconnecting...");
    setup_wifi();
  }
}

void mqtt_reconnect() {
  while (!mqtt.connected()) {
    // will: 异常断连时 broker 把 agri/status 置为 offline(retained)
    if (mqtt.connect("smart_agri_gateway", "agri/status", 1, true, "offline")) {
      Serial.println("MQTT connected");
      cache_flush();   // 恢复连接后补发离线缓存
    } else {
      Serial.printf("MQTT fail rc=%d, retry in 5s\n", mqtt.state());
      delay(5000);
    }
  }
}

// ============================================================
// 采集 + 上报
// ============================================================
void publish_one(uint8_t uid, uint16_t* hr, uint8_t offline) {
  time_t ts = now_ts();
  char csv[96];
  snprintf(csv, sizeof(csv), "%u,%ld,%u,%u,%u,%u,%u,%u,%u",
           uid, (long)ts, hr[0], hr[1], hr[2], hr[3], hr[4], hr[5], offline);
  if (!mqtt.connected()) {
    cache_push(String(csv));   // 离线：入缓存，恢复后补发
    return;
  }
  char topic[48], payload[160];
  snprintf(topic, sizeof(topic), "agri/pot/%d/state", uid);
  snprintf(payload, sizeof(payload),
           "{\"ts\":%ld,\"moisture\":%u,\"temp_c\":%.1f,\"light\":%u,\"ec\":%.2f,\"batt\":%u,\"power\":%u,\"offline\":%u}",
           (long)ts, hr[0], hr[1] / 10.0f, hr[2], hr[3] / 100.0f, hr[4], hr[5], offline);
  if (!mqtt.publish(topic, payload, false)) {
    cache_push(String(csv));
  }
}

void poll_and_publish() {
  for (uint8_t i = 0; i < POT_COUNT; i++) {
    uint8_t uid = BASE_UID + i;
    uint16_t hr[REG_COUNT];
    if (modbusReadHolding(uid, REG_START, REG_COUNT, hr)) {
      fail_count[uid] = 0;
      for (uint8_t k = 0; k < REG_COUNT; k++) last_hr[uid][k] = hr[k];
      publish_one(uid, hr, 0);
      Serial.printf("pot%d: moist=%u%% temp=%.1fC batt=%u%% power=%u\n",
                    uid, hr[0], hr[1] / 10.0f, hr[4], hr[5]);
    } else {
      fail_count[uid]++;
      uint8_t off = (fail_count[uid] >= OFFLINE_THRESHOLD) ? 1 : 0;
      publish_one(uid, last_hr[uid], off);   // 复用上次值 + offline 标记
      Serial.printf("pot%d: read FAIL (offline=%u)\n", uid, off);
    }
    delay(100);
  }
}

void heartbeat() {
  static unsigned long last = 0;
  if (millis() - last >= HEARTBEAT_MS) {
    last = millis();
    mqtt.publish("agri/status", "online", true);
  }
}

// ============================================================
// 看门狗（兼容 Arduino-ESP32 2.x / 3.x）
// ============================================================
void setup_wdt() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  esp_task_wdt_config_t cfg = {
    .timeout_ms = 15000,
    .idle_core_mask = 0,
    .trigger_panic = true,
  };
  esp_task_wdt_init(&cfg);
#else
  esp_task_wdt_init(15, true);
#endif
  esp_task_wdt_add(NULL);
}

// ============================================================
void setup() {
  Serial.begin(115200);
  setup_wifi();
  configTime(8 * 3600, 0, "ntp.aliyun.com", "pool.ntp.org");
  prefs.begin("smartagri", false);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(1024);
  mqtt_reconnect();
  setup_wdt();
}

void loop() {
  esp_task_wdt_reset();
  ensure_wifi();
  if (!mqtt.connected()) mqtt_reconnect();
  mqtt.loop();
  heartbeat();

  static unsigned long last = 0;
  if (millis() - last >= POLL_MS) {
    last = millis();
    poll_and_publish();
  }
}
