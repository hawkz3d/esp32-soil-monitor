#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_read.py — 验证模拟从站可读：连 127.0.0.1:502，读 uid 1~5 的 6 个保持寄存器。
用法：先启动 fake_greenhouse.py，再运行本脚本。
"""
from pymodbus.client import ModbusTcpClient

HOST = "127.0.0.1"
PORT = 502
REG_START = 0x0000
REG_COUNT = 6
UID_MAX = 5
POWER_STR = {0: "AC", 1: "BATT", 2: "OFF"}

client = ModbusTcpClient(HOST, port=PORT)
if not client.connect():
    print("connect FAIL")
    raise SystemExit(1)

for uid in range(1, UID_MAX + 1):
    rr = client.read_holding_registers(REG_START, count=REG_COUNT, device_id=uid)
    if rr.isError():
        print("uid=%d error (offline/power-off)" % uid)
        continue
    regs = rr.registers
    print("uid=%d moist=%d%% temp=%.1fC light=%d lux ec=%.2f batt=%d%% power=%s" % (
        uid, regs[0], regs[1] / 10.0, regs[2], regs[3] / 100.0,
        regs[4], POWER_STR.get(regs[5], regs[5])))

client.close()
print("done")
