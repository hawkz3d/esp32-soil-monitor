# ESP32 农业监控系统

基于 ESP32 的家庭农业数据采集体系，包含两代实现：

| 模块 | 采集方式 | 数据链路 | 场景 |
|---|---|---|---|
| `soil_monitor` 土壤湿度监控 | 5 路 TLC555 模拟传感器（ADC） | MQTT → Home Assistant 面板 | 盆栽湿度实时监控 |
| `smart_agri_gateway` 数据采集网关 | Modbus 读传感器节点 | MQTT → Streamlit 面板 + 邮件报警 | 无人值守 + 供电模拟演练 |

---

## 模块一：土壤湿度监控（soil_monitor）

用 ESP32-WROOM-32E + 5 路 TLC555 电容式土壤湿度传感器，通过 MQTT 上报到 Home Assistant。

### 架构

```
5x TLC555(AOUT 模拟输出) → ESP32 ADC1 → WiFi → MQTT → NAS Mosquitto → HA 自动发现 + 概览仪表盘
```

### 目录结构

```
firmware/
  soil_monitor/   主固件：5 路 ADC → WiFi → MQTT → HA Discovery
  soil_probe/     阶段探针：频率/ADC 探测（用于判断传感器输出类型）
  soil_probe_v2/  双测：ADC + 频率
  soil_probe_v3/  5 路模拟读取
nas/
  mosquitto/      MQTT broker 配置（Docker 部署）
  ha/             HA 配置参考
```

### 硬件接线

- 5 路传感器 OUT 接 GPIO32/33/34/35/36（**必须 ADC1**）
- ⚠️ ADC2（GPIO25/26/27 等）在 WiFi 开启时全部失效，不可用于模拟读取

### 校准

每路独立仪器校准，填 `CAL_ADC` / `CAL_HUM`：

- `CAL_ADC` = 各盆当前 ADC 读数，`CAL_HUM` = 仪器实测湿度%
- 映射：每路线性，锚点 = 当前实测点 + 泡水(→100%)；浇水 → ADC↓ → 湿度↑
- 2026-08-12 逐路实测：盆1~5 `CAL_ADC={1269,1192,1251,1150,1467}` `CAL_HUM={59,69,68,61,73}`，泡水基准 `CAL_WET_ADC=1034`

### 踩坑记录

- 探针泡水久会留水膜 → 读数停在湿态低值，**晾干恢复，非损坏**
- `MQTT_MAX_PACKET_SIZE` 必须 ≥1024（discovery JSON 约 600B）
- 面包板接触不良会压降，压缩 ADC 动态范围；供电要直连可靠

---

## 模块二：数据采集网关（smart_agri_gateway）

面向无人值守场景的采集网关：ESP32 通过 Modbus 读取传感器节点，经 MQTT 推送，PC 端可视化 + 邮件报警。
整套链路可在**无硬件**的情况下用软件模拟跑通，也可接真实 RS485 设备平滑切换。

### 架构

```
simulator/fake_greenhouse.py ──Modbus TCP──> simulator/gateway_sim.py / ESP32 固件 ──MQTT──> broker ──> pc/mqtt_sub.py ──> SQLite
      (虚拟传感器从站)          your_modbus_host   (软件版网关 / smart_agri_gateway.ino)   your_mqtt_host          (订阅入库+报警)      │
                                                                                                                                 ▼
                                                                                                                    pc/app.py (Streamlit 面板)
```

- `simulator/fake_greenhouse.py`：pymodbus 虚拟从站，模拟 N 个花盆（传感器 + 供电）
- `simulator/gateway_sim.py`：软件版网关，Modbus → MQTT（无 ESP32 也能跑通全链路）
- `firmware/smart_agri_gateway/`：ESP32 固件（硬件版网关，含无人值守）
- `pc/mqtt_sub.py`：订阅入库 + 报警判断
- `pc/app.py`：Streamlit 可视化面板

### 寄存器表（两端一致）

| 地址 | 含义 | 单位/缩放 |
|---|---|---|
| 0x0000 | 土壤湿度 | %（0-100）|
| 0x0001 | 温度 | ℃ ×10 |
| 0x0002 | 光照 | lux（0-1000）|
| 0x0003 | EC 肥力 | ×100 |
| 0x0004 | 电池电量 | %（0-100）|
| 0x0005 | 供电状态 | 0=市电 1=电池 2=断电 |

MQTT topic：`agri/pot/{uid}/state`（数据）、`agri/status`（网关心跳）。

### 运行（无硬件全链路）

1. 启动模拟从站：`python simulator/fake_greenhouse.py`
2. 启动软件网关：`python simulator/gateway_sim.py --mqtt-host <broker_ip>`
3. 启动订阅入库+报警：`python pc/mqtt_sub.py --host <broker_ip>`
4. 启动面板：`streamlit run pc/app.py`

> 报警默认 `dry-run`。配置 `pc/config.py` 的 SMTP 后改 `ALARM_DRY_RUN=False`，或 `python pc/mqtt_sub.py --send` 真发邮件。

### 配置（占位符）

所有网络地址/凭据默认占位符，本地运行前替换：`pc/config.py`（`MQTT_HOST`/SMTP，可用环境变量 `SMART_AGRI_MQTT_HOST` 覆盖）、`gateway_sim.py --mqtt-host`、固件 `WIFI_SSID/PASS`/`MODBUS_HOST`/`MQTT_HOST`。

### 供电模拟

全局供电状态自动循环（市电→电池→断电），每盆电量独立演化；`simulator/power_cmd.txt` 支持手动注入：

```
on / batt / off           # 全局市电 / 电池 / 断电
off 3 / on 3              # 仅 3 号盆断电 / 恢复
auto                      # 恢复自动循环
```

### 无人值守（ESP32 固件）

- WiFi 断线自动重连；MQTT 断连离线缓存到 NVS，恢复后按时间戳补发
- 心跳 `agri/status` + LWT 遗嘱（断线秒判离线）；看门狗 15s 自动重启
- Modbus 读失败连续 3 次 → `offline:1`

### 报警规则（pc/mqtt_sub.py）

| 条件 | 默认值 |
|---|---|
| 湿度过低 | <25%，持续 30s（去抖）|
| 电量过低 | <20% |
| 从站离线 | `offline=1` |
| 从站断电 | `power=2` |
| 网关心跳超时 | >60s |

### 演练手册

- **断网演练**：停 broker → 固件进离线缓存 → 恢复后补发（SQLite 时间戳连续）
- **断电演练**：写 `power_cmd.txt` 为 `off 3` → 面板该盆变 OFFLINE，报警打印
- **掉线演练**：Ctrl+C 杀 fake_greenhouse → `offline:1` 报警
- **心跳演练**：停 gateway_sim/拔 ESP32 电源 → 心跳超时报警

---

## 依赖

- 土壤监控：Arduino IDE + PubSubClient 库
- 网关 PC 端：`pip install -r pc/requirements.txt`（paho-mqtt / streamlit / plotly / pandas）
- 网关模拟层：pymodbus 3.15+（SimData/SimDevice 新 API）
