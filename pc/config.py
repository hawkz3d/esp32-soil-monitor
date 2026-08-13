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
