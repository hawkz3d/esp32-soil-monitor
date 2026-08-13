#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gateway_sim.py — Python 版 ESP32 网关（无硬件也能跑通全链路）。

对应 ESP32 固件 smart_agri_gateway.ino 的行为：
  1. 每 POLL_S 秒轮询 Modbus 从站（fake_greenhouse.py）读 6 个保持寄存器
  2. 组 JSON 发布到 MQTT：agri/pot/{uid}/state
  3. 每 10s 发网关心跳 agri/status online
  4. Modbus 读失败 -> payload offline=1

用法：
  python gateway_sim.py                            # 默认 127.0.0.1:502 -> your_mqtt_host:1883
  python gateway_sim.py --mqtt-host <broker_ip> --modbus-host 127.0.0.1
"""
import argparse
import json
import time

import paho.mqtt.client as mqtt
from pymodbus.client import ModbusTcpClient

POT_COUNT = 5
BASE_UID = 1
REG_START = 0x0000
REG_COUNT = 6
POLL_S = 5.0
HEARTBEAT_S = 10.0


def read_pot(mb, uid):
    rr = mb.read_holding_registers(REG_START, count=REG_COUNT, device_id=uid)
    if rr.isError():
        return None
    r = rr.registers
    return {
        "ts": int(time.time()),
        "moisture": r[0],
        "temp_c": r[1] / 10.0,
        "light": r[2],
        "ec": r[3] / 100.0,
        "batt": r[4],
        "power": r[5],
        "offline": 0,
    }


def main():
    ap = argparse.ArgumentParser(description="Software gateway: Modbus -> MQTT")
    ap.add_argument("--mqtt-host", default="your_mqtt_host")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--modbus-host", default="127.0.0.1")
    ap.add_argument("--modbus-port", type=int, default=502)
    args = ap.parse_args()

    mb = ModbusTcpClient(args.modbus_host, port=args.modbus_port)
    if not mb.connect():
        print("[gateway_sim] modbus connect FAIL %s:%d" % (args.modbus_host, args.modbus_port))
        return

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(args.mqtt_host, args.mqtt_port, keepalive=30)
    print("[gateway_sim] modbus %s:%d -> mqtt %s:%d"
          % (args.modbus_host, args.modbus_port, args.mqtt_host, args.mqtt_port), flush=True)

    last_hb = 0.0
    while True:
        # 心跳
        if time.time() - last_hb >= HEARTBEAT_S:
            last_hb = time.time()
            client.publish("agri/status", json.dumps({"ts": int(time.time()), "status": "online"}), qos=0, retain=True)

        # 轮询各盆
        for i in range(POT_COUNT):
            uid = BASE_UID + i
            p = read_pot(mb, uid)
            if p is None:
                p = {"ts": int(time.time()), "offline": 1,
                     "moisture": 0, "temp_c": 0.0, "light": 0, "ec": 0.0, "batt": 0, "power": 0}
                print("[gateway_sim] pot%d read FAIL (offline)" % uid, flush=True)
            client.publish("agri/pot/%d/state" % uid, json.dumps(p), qos=0)
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
