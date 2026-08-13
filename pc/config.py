# config.py — 面板/报警配置
# 本文件为占位符配置，本地运行前请替换成真实值。
# mqtt_sub.py 支持命令行覆盖 MQTT 地址和湿度阈值，见 --help。
import os

# ---- MQTT broker（NAS Mosquitto）----
MQTT_HOST = os.environ.get("SMART_AGRI_MQTT_HOST", "your_mqtt_host")
MQTT_PORT = int(os.environ.get("SMART_AGRI_MQTT_PORT", "1883"))

# ---- SQLite ----
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smart_agri.db")

# ---- 邮件报警（SMTP，占位符）----
ALARM_DRY_RUN = True          # True=只打印不真发邮件；配好 SMTP 后改 False
SMTP_HOST = "smtp.example.com"
SMTP_PORT = 465               # 465=SSL；若用 587 需 STARTTLS（脚本自动处理）
SMTP_USER = "your_account@example.com"
SMTP_PASS = "your_password"
ALARM_FROM = "your_account@example.com"
ALARM_TO = ["receiver@example.com"]

# ---- 报警阈值 ----
MOIST_ALARM = 25.0        # 湿度低于此值报警（%）
MOIST_HOLD_S = 30         # 持续低于阈值多少秒才报警（去抖）
BATT_ALARM = 20.0         # 电量低于此值报警（%）
OFFLINE_ALARM = True      # payload offline=1 立即报警
POWER_OFF_ALARM = True    # power=2（断电）报警
HEARTBEAT_TIMEOUT_S = 60  # 心跳超过多久判网关离线（秒）

# ---- 云端 MQTT（cloud_bridge.py 推送云端框架，占位符）----
CLOUD_MQTT_HOST = os.environ.get("SMART_AGRI_CLOUD_HOST", "your_cloud_broker")
CLOUD_MQTT_PORT = int(os.environ.get("SMART_AGRI_CLOUD_PORT", "1883"))
CLOUD_MQTT_USER = "your_cloud_user"
CLOUD_MQTT_PASS = "your_cloud_password"
CLOUD_MQTT_TLS = False            # 云 broker 通常走 TLS（如 EMQX 8883 / 阿里云 MQTT），连上后改 True
CLOUD_SUB_TOPIC = "agri/#"        # 本地订阅并转发到云端的 topic
CLOUD_TOPIC_PREFIX = ""           # 转发到云端时的 topic 前缀（可带租户/站点标识，如 "site1/"），留空则原样转发
