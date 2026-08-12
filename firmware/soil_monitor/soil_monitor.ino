// soil_monitor.ino — ESP32 土壤湿度监控：5路ADC -> WiFi -> MQTT -> Home Assistant
// 依赖库：PubSubClient（工具 -> 管理库 -> 搜 "PubSubClient" -> 安装 knolleary 的）
// 配置：WIFI_SSID / WIFI_PASS 填你的 WiFi；CAL_DRY/CAL_WET 填每路实测校准基准
// 注意：MQTT_MAX_PACKET_SIZE 必须 >= discovery JSON 大小（约600字节），默认256不够
#define MQTT_MAX_PACKET_SIZE 1024
#include <WiFi.h>
#include <PubSubClient.h>

// ===== WiFi / MQTT 配置 =====
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";
const char* MQTT_HOST = "192.168.X.XXX";      
const int   MQTT_PORT = 1883;

// ===== 传感器引脚（ADC2 在 WiFi 开启时失效）=====
// ADC1 可用：GPIO32, 33, 34, 35, 36(SVP), 39(SVN) 共 6 路
const int PINS[5] = {32, 33, 34, 35, 36};
const char* SENSOR_NAMES[5] = {"soil_1", "soil_2", "soil_3", "soil_4", "soil_5"};

// ===== 校准基准：{dry, wet}，每路独立（5 路同型号，暂共用）=====
// dry = 完全干燥时 ADC（高值，映射为房间空气湿度 AIR_HUMIDITY_PCT）
// wet = 水饱和时 ADC（低值，映射为 100%）
// 2026-08-12 实测：dry=3248（S1 晾干稳定）、wet=1034（S1 泡水稳定）
const int CAL_DRY[5] = {3248, 3248, 3248, 3248, 3248};
const int CAL_WET[5] = {1034, 1034, 1034, 1034, 1034};

// 完全干燥时显示的房间空气湿度（%）：土壤完全干透时与空气水分平衡，
// 读数不应为 0% 而应回到环境湿度
const float AIR_HUMIDITY_PCT = 35.0f;

const unsigned long REPORT_MS = 30000; // 上报间隔（毫秒）

WiFiClient espClient;
PubSubClient mqtt(espClient);

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

// MQTT Discovery：让 HA 自动创建 5 个湿度传感器实体
void send_discovery() {
  for (int i = 0; i < 5; i++) {
    char topic[96];
    snprintf(topic, sizeof(topic), "homeassistant/sensor/%s/config", SENSOR_NAMES[i]);
    char payload[640];
    snprintf(payload, sizeof(payload),
      "{\"name\":\"Soil Moisture %d\",\"device_class\":\"moisture\","
      "\"unit_of_measurement\":\"%%\",\"state_topic\":\"soil/sensor/%d/state\","
      "\"unique_id\":\"esp32_soil_%d\",\"value_template\":\"{{ value_json.moisture }}\","
      "\"device\":{\"identifiers\":[\"esp32_soil_monitor\"],\"name\":\"ESP32 Soil Monitor\","
      "\"manufacturer\":\"DIY\",\"model\":\"ESP32-WROOM-32E\"}}",
      i + 1, i + 1, i + 1);
    bool ok = mqtt.publish(topic, payload, true);
    Serial.printf("disc[%d] %s (%d bytes)\n", i + 1, ok ? "ok" : "FAIL", (int)strlen(payload));
  }
}

// 经验修正：目前盆读数普遍偏高约 35 个百分点，直接减去（经验常数，盆干后可再调）
const float HUMIDITY_OFFSET = 35.0f;

// 湿度百分比：干燥(dry)时显示 AIR_HUMIDITY_PCT，水饱和(wet)时显示 100%，线性映射后减经验偏移
float moisture_percent(int idx, int adc) {
  int dry = CAL_DRY[idx], wet = CAL_WET[idx];
  if (dry <= wet) return AIR_HUMIDITY_PCT;
  float p = AIR_HUMIDITY_PCT + (float)(dry - adc) / (float)(dry - wet) * (100.0f - AIR_HUMIDITY_PCT);
  p -= HUMIDITY_OFFSET;
  if (p < AIR_HUMIDITY_PCT) p = AIR_HUMIDITY_PCT; // 下限=环境湿度，不再跌破
  if (p > 100) p = 100;
  return p;
}

int read_adc(int pin) {
  int sum = 0;
  for (int i = 0; i < 8; i++) sum += analogRead(pin);
  return sum / 8;
}

void publish_once() {
  for (int i = 0; i < 5; i++) {
    int adc = read_adc(PINS[i]);
    float m = moisture_percent(i, adc);
    float v = adc / 4095.0f * 3.3f;
    char topic[64];
    snprintf(topic, sizeof(topic), "soil/sensor/%d/state", i + 1);
    char payload[128];
    snprintf(payload, sizeof(payload), "{\"moisture\":%.1f,\"adc\":%d,\"voltage\":%.2f}", m, adc, v);
    mqtt.publish(topic, payload, false);
    Serial.printf("S%d: adc=%d %.1f%% %.2fV\n", i + 1, adc, m, v);
  }
}

bool discovery_sent = false;
unsigned long connect_time = 0;

void mqtt_reconnect() {
  while (!mqtt.connected()) {
    if (mqtt.connect("esp32_soil_monitor")) {
      Serial.println("MQTT connected");
      discovery_sent = false;
      connect_time = millis();
    } else {
      Serial.printf("MQTT fail rc=%d, retry in 5s\n", mqtt.state());
      delay(5000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < 5; i++) {
    pinMode(PINS[i], INPUT);
    analogSetPinAttenuation(PINS[i], ADC_11db);
  }
  setup_wifi();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(1024); // 必须 >= discovery JSON 大小，默认256不够
  mqtt_reconnect();
}

void loop() {
  if (!mqtt.connected()) mqtt_reconnect();
  mqtt.loop();

  // MQTT 连接稳定 3 秒后再发 discovery，避免连接未就绪时 publish 丢失
  if (!discovery_sent && mqtt.connected() && millis() - connect_time > 3000) {
    send_discovery();
    discovery_sent = true;
    Serial.println("Discovery sent");
  }

  static unsigned long last = 0;
  if (millis() - last >= REPORT_MS) {
    last = millis();
    publish_once();
  }
}
