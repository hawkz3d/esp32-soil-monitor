# ESP32 数据采集与监控平台

基于 ESP32 的通用传感数据采集网关：通过**模拟量 ADC** 或 **Modbus** 等接口采集各类传感器数据（土壤湿度、温度、光照、变频器参数等），经 MQTT 推送到 broker，由 PC 端完成可视化与报警。

土壤湿度只是其中的一个采集模块（走模拟量 ADC）；Modbus 是通用采集协议，新增任何支持 Modbus 的设备（土壤/环境传感器、变频器、仪表）只需约定寄存器表即可接入，无需改平台层。

## 架构

```
┌─ 采集层 ─────────────────────────────────────────────┐
│  模拟量 ADC  (5×TLC555 土壤传感器 → GPIO32-36)        │
│  Modbus RTU/TCP (任意从站 → 读保持寄存器)             │
└──────────────────┬───────────────────────────────────┘
                   ▼
           ESP32 网关（firmware/）
      soil_monitor      /    smart_agri_gateway
    (模拟量→MQTT→HA)        (Modbus主站→MQTT→面板)
                   │
                   ▼  MQTT (agri/#)
            broker (NAS Mosquitto)
                   │
      ┌────────────┴─────────────┐
      ▼                          ▼
  Home Assistant            PC 端（pc/）
  (自动发现+仪表盘)     Streamlit 面板 + 邮件报警
```

- **采集方式可叠加**：同一 ESP32 可同时接模拟量传感器与 Modbus 从站
- **无硬件也能跑**：`simulator/` 提供虚拟 Modbus 从站 + 软件网关，整套链路可软件模拟

## 目录结构

```
firmware/
  soil_monitor/           # 采集模块：模拟量 ADC → MQTT → HA Discovery
  smart_agri_gateway/     # 采集模块：Modbus 主站 → MQTT（含无人值守）
  soil_probe/...          # 传感器探测探针（ADC/频率）
simulator/                # 模拟层：虚拟 Modbus 从站 / 软件网关 / 供电模拟
pc/                       # 应用层：MQTT 订阅入库 + Streamlit 面板 + 邮件报警
nas/                      # 部署：Mosquitto 配置 / Home Assistant 配置
```

## 采集模块

### 模拟量 ADC —— 土壤湿度监控（firmware/soil_monitor）

5 路 TLC555 电容式土壤湿度传感器（AOUT 模拟输出）接 GPIO32/33/34/35/36（**必须 ADC1**，ADC2 在 WiFi 开启时失效），经 MQTT 上报到 Home Assistant。

- 每路独立仪器校准（`CAL_ADC` / `CAL_HUM`）：线性映射，锚点=当前实测点+泡水(→100%)
- 逐路实测（2026-08-12）：盆1~5 `CAL_ADC={1269,1192,1251,1150,1467}` `CAL_HUM={59,69,68,61,73}`，泡水基准 `CAL_WET_ADC=1034`
- MQTT Discovery 自动建实体；`MQTT_MAX_PACKET_SIZE` 必须 ≥1024
- 坑：探针泡水留水膜→读数停在湿态，晾干恢复非损坏；面包板接触不良会压降

### Modbus 主站 —— 通用数据采集（firmware/smart_agri_gateway）

ESP32 作为 Modbus 主站，轮询任意从站设备读保持寄存器，经 MQTT 推送到 `pc/` 面板与报警。寄存器表（两端约定）：

| 地址 | 含义 | 单位/缩放 |
|---|---|---|
| 0x0000 | 土壤湿度 | %（0-100）|
| 0x0001 | 温度 | ℃ ×10 |
| 0x0002 | 光照 | lux（0-1000）|
| 0x0003 | EC 肥力 | ×100 |
| 0x0004 | 电池电量 | %（0-100）|
| 0x0005 | 供电状态 | 0=市电 1=电池 2=断电 |

> 接其他 Modbus 设备时，把目标寄存器映射到这个约定表即可，平台层（MQTT/面板/报警）无需改动。

固件具备**无人值守**能力：WiFi 断线自动重连、MQTT 断连离线缓存到 NVS 恢复后补发、心跳 `agri/status` + LWT 遗嘱、看门狗 15s、Modbus 读失败连续 3 次 → `offline:1`。

## 应用层（pc/）

- `mqtt_sub.py`：订阅 `agri/#` 写入 SQLite，并做报警判断
- `app.py`：Streamlit 面板（每盆仪表卡 + 趋势曲线），10s 自动刷新
- `cloud_bridge.py`：本地 → 云端 MQTT 桥接（推送云端框架）
- `config.py`：配置（占位符，本地运行前替换）

MQTT topic：`agri/pot/{uid}/state`（数据）、`agri/status`（网关心跳）。

## 云端推送（cloud_bridge）

数据默认在局域网内流动。要"推送云端"，在本地 broker 与云端 broker 之间跑 `pc/cloud_bridge.py`：

```
ESP32/软件网关 ──MQTT──> 本地 broker (NAS)
                            │  cloud_bridge.py 转发 agri/#
                            ▼
                     云端 broker（EMQX Serverless / 阿里云 / 自建）
                            │
                            ▼
              pc/mqtt_sub.py --host <云broker> → SQLite → Streamlit（异地）
```

- 固件 / 网关照常只连本地 broker，桥接层负责上云，采集端无需改动
- 云端断连期间消息暂存内存队列，恢复后按序补发
- 配置在 `pc/config.py` 的 `CLOUD_*`（占位符）；云 broker 一般要求 TLS：

```bash
python pc/cloud_bridge.py --local-host <broker_ip> \
    --cloud-host your.cloud.emqx.io --cloud-port 8883 \
    --cloud-user user --cloud-pass pass --tls
```

- 云端侧直接复用 `pc/mqtt_sub.py --host <云broker>` 即可入库与展示

### 报警规则

| 条件 | 默认值 |
|---|---|
| 湿度过低 | <25%，持续 30s（去抖）|
| 电量过低 | <20% |
| 从站离线 | `offline=1` |
| 从站断电 | `power=2` |
| 网关心跳超时 | >60s |

## 快速开始

```bash
# 1. 启动虚拟 Modbus 从站（模拟 N 个花盆：传感器 + 供电）
python simulator/fake_greenhouse.py

# 2. 启动软件网关（Modbus → MQTT），或烧录 firmware/smart_agri_gateway
python simulator/gateway_sim.py --mqtt-host <broker_ip>

# 3. 订阅入库 + 报警
python pc/mqtt_sub.py --host <broker_ip>

# 4. Streamlit 面板
streamlit run pc/app.py
```

> 报警默认 `dry-run`（只打印）。配置 `pc/config.py` 的 SMTP 后改 `ALARM_DRY_RUN=False`，或 `python pc/mqtt_sub.py --send` 真发邮件。

## 配置（占位符）

所有网络地址/凭据默认占位符：`pc/config.py`（`MQTT_HOST`/SMTP，可用环境变量 `SMART_AGRI_MQTT_HOST` 覆盖）、`gateway_sim.py --mqtt-host`、固件 `WIFI_SSID/PASS`/`MODBUS_HOST`/`MQTT_HOST`。

## 供电模拟（simulator/power_cmd.txt）

全局供电状态自动循环（市电→电池→断电），每盆电量独立演化；手动注入：

```
on / batt / off           # 全局市电 / 电池 / 断电
off 3 / on 3              # 仅 3 号盆断电 / 恢复
auto                      # 恢复自动循环
```

## 演练手册

- **断网演练**：停 broker → 固件进离线缓存 → 恢复后补发（SQLite 时间戳连续）
- **断电演练**：写 `power_cmd.txt` 为 `off 3` → 面板该盆变 OFFLINE，报警打印
- **掉线演练**：Ctrl+C 杀 fake_greenhouse → `offline:1` 报警
- **心跳演练**：停 gateway_sim / 拔 ESP32 电源 → 心跳超时报警

## 依赖

- 固件：Arduino IDE + PubSubClient 库
- 模拟层：pymodbus 3.15+（SimData/SimDevice 新 API）
- PC 端：`pip install -r pc/requirements.txt`（paho-mqtt / streamlit / plotly / pandas）
