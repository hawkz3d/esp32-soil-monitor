# ESP32 土壤湿度监控系统

用 ESP32-WROOM-32E + 5 路 TLC555 电容式土壤湿度传感器，通过 MQTT 把数据上报到 NAS 上的 Home Assistant，实现盆栽土壤湿度实时监控。

## 架构

```
5x TLC555(AOUT 模拟输出) → ESP32 ADC1 → WiFi → MQTT → NAS Mosquitto → HA 自动发现 + 概览仪表盘
```

## 目录结构

```
firmware/
  soil_monitor/   主固件：5 路 ADC → WiFi → MQTT → HA Discovery
  soil_probe/     阶段探针：频率/ADC 探测（用于判断传感器输出类型）
  soil_probe_v2/  双测：ADC + 频率
  soil_probe_v3/  5 路模拟读取
nas/
  mosquitto/      MQTT broker 配置（Docker 部署）
  ha/             HA 配置参考（configuration.yaml / lovelace.json）
```

## 硬件接线

- 5 路传感器 OUT 接 GPIO32/33/34/35/36（**必须 ADC1**）
- ⚠️ ADC2（GPIO25/26/27 等）在 WiFi 开启时全部失效，不可用于模拟读取

## 固件配置

编辑 `firmware/soil_monitor/soil_monitor.ino`：

```c
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";
const char* MQTT_HOST = "192.168.0.XXX";   // NAS / MQTT broker 地址
```

依赖库：PubSubClient（Arduino IDE 库管理器安装 knolleary 版）。

## 校准

每路独立仪器校准，填 `CAL_ADC` / `CAL_HUM`：

- `CAL_ADC` = 各盆当前 ADC 读数
- `CAL_HUM` = 仪器实测的盆土湿度%
- 映射：每路线性，锚点 = 当前实测点 `(CAL_ADC[idx], CAL_HUM[idx])` + 泡水 `(CAL_WET_ADC→100%)`
- 浇水 → ADC↓ → 湿度↑；变干 → 湿度↓，下限 clamp 到 `AIR_HUMIDITY_PCT`（35%）

2026-08-12 逐路实测（用户仪器）：盆1(S1)=1269/59、盆2(S2)=1192/69、盆3(S3)=1251/68、盆4(S4)=1150/61、盆5(S5)=1467/73。泡水基准 `CAL_WET_ADC=1034`（S1 泡水实测，近似通用）。

换盆/换土后需重测：用仪器测当前湿度 + 记录对应 ADC，更新 `CAL_ADC` / `CAL_HUM` 对应项。

## NAS 端

- **Mosquitto**：`nas/mosquitto/mosquitto.conf`，listener 1883、allow_anonymous true
- **Home Assistant**：新版 MQTT 集成必须通过 UI 添加（不支持 yaml 配置）；MQTT Discovery 自动创建 `sensor.esp32_soil_monitor_soil_moisture_1~5` 实体

## 踩坑记录

- 探针泡水久会留水膜 → 读数停在湿态低值，**晾干恢复，非损坏**
- `MQTT_MAX_PACKET_SIZE` 必须 ≥1024（discovery JSON 约 600B，默认 256 会导致 publish 失败）
