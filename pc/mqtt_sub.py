#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mqtt_sub.py — 订阅 agri/# 写入 SQLite，并做报警判断（邮件或 dry-run）。

数据管道：ESP32 每 5s 发布 agri/pot/{uid}/state + agri/status(心跳)，
本脚本订阅后：
  1. 写入 SQLite（state 表 + heartbeat 表），供 app.py 面板读取
  2. 报警判断：
     - 湿度 < MOIST_ALARM 持续 MOIST_HOLD_S 秒（去抖）
     - 电量 < BATT_ALARM
     - payload offline=1（从站掉线）
     - power=2（从站断电）
     - 网关心跳超时 HEARTBEAT_TIMEOUT_S

用法：
  python mqtt_sub.py                        # 用 config.py 配置
  python mqtt_sub.py --host <broker_ip>     # 覆盖 MQTT 地址
  python mqtt_sub.py --moist 70             # 临时覆盖湿度阈值（调试报警用）
  python mqtt_sub.py --send                 # 真发邮件（默认 dry-run 只打印）
"""
import argparse
import json
import sqlite3
import threading
import time

import paho.mqtt.client as mqtt

import config

DB = config.DB_PATH

# ---- 报警状态机 ----
ALARM_STATE = {}  # key -> {"active": bool, "since": float, "notified": bool}


def fire_alarm(msg):
    """发出报警：dry-run 打印；否则 SMTP 发邮件。"""
    if config.ALARM_DRY_RUN:
        print("[ALARM][dry-run] " + msg, flush=True)
        return
    try:
        import smtplib
        from email.mime.text import MIMEText
        body = ("Smart Agri Gateway Alarm\nTime: %s\n\n%s"
                % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        mail = MIMEText(body, "plain", "utf-8")
        mail["Subject"] = "[SmartAgri] Alarm"
        mail["From"] = config.ALARM_FROM
        mail["To"] = ", ".join(config.ALARM_TO)
        if config.SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=10)
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10)
            server.starttls()
        server.login(config.SMTP_USER, config.SMTP_PASS)
        server.sendmail(config.ALARM_FROM, config.ALARM_TO, mail.as_string())
        server.quit()
        print("[ALARM] sent: " + msg, flush=True)
    except Exception as exc:  # noqa: BLE001
        print("[ALARM] send FAIL: %s" % exc, flush=True)


def check_alarm(key, active, desc, hold_s=0):
    """带去抖的报警判断：条件持续 hold_s 秒才通知一次，恢复后重置。"""
    now = time.time()
    st = ALARM_STATE.setdefault(key, {"active": False, "since": now, "notified": False})
    if active:
        if not st["active"]:
            st["active"] = True
            st["since"] = now
        if not st["notified"] and now - st["since"] >= hold_s:
            st["notified"] = True
            fire_alarm(desc)
    else:
        if st["active"]:
            st["active"] = False
            st["notified"] = False


# ---- 数据库 ----
def init_db():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS state(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid INTEGER, ts INTEGER, moist REAL, temp_c REAL,
        light INTEGER, ec REAL, batt INTEGER, power INTEGER,
        offline INTEGER, recv_ts REAL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_state_uid_ts ON state(uid, ts)")
    conn.execute("""CREATE TABLE IF NOT EXISTS heartbeat(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER, status TEXT, recv_ts REAL)""")
    conn.commit()
    conn.close()


def insert_state(uid, p, now):
    conn = sqlite3.connect(DB)
    conn.execute(
        "INSERT INTO state(uid,ts,moist,temp_c,light,ec,batt,power,offline,recv_ts) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (uid, p.get("ts", 0), p.get("moisture"), p.get("temp_c"),
         p.get("light"), p.get("ec"), p.get("batt"), p.get("power"),
         1 if p.get("offline") else 0, now))
    conn.commit()
    conn.close()


def insert_heartbeat(p, now):
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO heartbeat(ts,status,recv_ts) VALUES(?,?,?)",
                 (p.get("ts", int(now)), p.get("status", "?"), now))
    conn.commit()
    conn.close()


# ---- 报警判断（在 MQTT 消息回调里触发）----
def evaluate_state(uid, p):
    power = p.get("power")
    if config.POWER_OFF_ALARM:
        check_alarm(f"pot{uid}_power", power == 2,
                    f"Pot {uid} POWER OFF", hold_s=0)

    offline = p.get("offline")
    if config.OFFLINE_ALARM:
        check_alarm(f"pot{uid}_offline", offline,
                    f"Pot {uid} OFFLINE (modbus read fail)", hold_s=0)

    moist = p.get("moisture")
    if moist is not None:
        check_alarm(f"pot{uid}_moist", moist < config.MOIST_ALARM,
                    f"Pot {uid} moisture LOW: {moist}%%", hold_s=config.MOIST_HOLD_S)

    batt = p.get("batt")
    if batt is not None:
        check_alarm(f"pot{uid}_batt", batt < config.BATT_ALARM,
                    f"Pot {uid} battery LOW: {batt}%%", hold_s=0)


def on_message(client, userdata, msg):
    topic = msg.topic
    try:
        p = json.loads(msg.payload.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return
    now = time.time()
    if topic.startswith("agri/pot/") and topic.endswith("/state"):
        try:
            uid = int(topic.split("/")[2])
        except ValueError:
            return
        insert_state(uid, p, now)
        evaluate_state(uid, p)
    elif topic == "agri/status":
        insert_heartbeat(p, now)


def heartbeat_watch():
    """后台线程：检查网关心跳是否超时。"""
    while True:
        time.sleep(10)
        conn = sqlite3.connect(DB)
        row = conn.execute("SELECT MAX(ts) FROM heartbeat").fetchone()
        conn.close()
        ts = row[0] if row and row[0] else 0
        now = int(time.time())
        lost = ts == 0 or (now - ts) > config.HEARTBEAT_TIMEOUT_S
        check_alarm("gateway_hb", lost,
                    "Gateway heartbeat LOST (no status for %ds)" % (now - ts if ts else 0),
                    hold_s=0)


def main():
    ap = argparse.ArgumentParser(description="Smart Agri MQTT subscriber + alarm")
    ap.add_argument("--host", default=config.MQTT_HOST)
    ap.add_argument("--port", type=int, default=config.MQTT_PORT)
    ap.add_argument("--moist", type=float, default=config.MOIST_ALARM,
                    help="override moisture alarm threshold")
    ap.add_argument("--send", action="store_true",
                    help="really send email (default: dry-run print only)")
    args = ap.parse_args()
    config.MOIST_ALARM = args.moist
    if args.send:
        config.ALARM_DRY_RUN = False

    init_db()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(args.host, args.port, keepalive=30)
    client.subscribe("agri/#")
    threading.Thread(target=heartbeat_watch, daemon=True).start()
    print("[mqtt_sub] connected %s:%d  dry_run=%s  moist_alarm=%.1f%%"
          % (args.host, args.port, config.ALARM_DRY_RUN, config.MOIST_ALARM), flush=True)
    client.loop_forever()


if __name__ == "__main__":
    main()
