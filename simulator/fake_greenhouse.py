#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fake_greenhouse.py — pymodbus 3.15 虚拟从站（模拟现代化农业传感器网络 + 供电）

在 PC 上运行本脚本，ESP32 通过 Modbus TCP 轮询它，模拟"几个花盆"的传感器。
每个花盆 = 1 个 Modbus 从站（uid = 花盆号）。

保持寄存器表（与 ESP32 固件一致）：
  0x0000  土壤湿度 %（0-100）
  0x0001  温度 ℃ ×10（如 24.5℃ -> 245）
  0x0002  光照 lux（0-1000）
  0x0003  EC 肥力 ×100（如 1.25 -> 125）
  0x0004  电池电量 %（0-100）
  0x0005  供电状态（0=市电 1=电池 2=断电）

供电模拟（无人值守演练核心）：
  - 每盆独立电量：市电充电 / 电池放电 / 断电冻结
  - 全局供电状态按 POWER_SCHEDULE 定时自动循环（市电→电池→断电）
  - 命令文件注入（simulator/power_cmd.txt）：改内容立即生效，手动命令暂缓自动循环 300s
      命令格式：
        on           全局切市电
        batt         全局切电池
        off          全局断电（所有盆读请求返回异常 -> ESP32 判离线）
        on 3 / off 3 仅切 3 号盆
        auto         恢复自动循环（清所有手动覆盖）
        auto 3       清 3 号盆的手动覆盖

断电时从站返回异常（SLAVE_FAILURE），ESP32 端会判定该花盆 offline —— 模拟"没电=不回答"。

运行：  python fake_greenhouse.py
停止：  Ctrl+C
"""
import math
import os
import random
import threading
import time

from pymodbus.constants import ExcCodes
from pymodbus.server import StartTcpServer
from pymodbus.simulator import SimDevice
from pymodbus.simulator.simdata import SimData
from pymodbus.simulator.simutils import DataType

# ============ 配置 ============
HOST = "0.0.0.0"
PORT = 502                # 如被占用/权限问题，可改 1502（ESP32 固件同步改）
POT_COUNT = 5             # 花盆数 = Modbus 从站地址数
BASE_UID = 1

# 花盆参数，按顺序对应 uid=1..N
# (初始湿度%, 初始温度℃, 初始EC, 蒸发率%/s, 自动浇水?, 初始电量%)
POTS = [
    (60, 24.0, 1.20, 0.03, True,  88),
    (55, 23.5, 1.10, 0.04, True,  72),
    (70, 23.0, 1.30, 0.06, False, 95),   # 花盆3 禁自动浇水 -> 湿度持续跌破报警线
    (50, 24.5, 1.15, 0.02, True,  64),
    (65, 23.8, 1.25, 0.03, True,  81),
]
DRY_WARNING = 25              # 湿度低于此值触发自动浇水（若启用）
AUTO_WATER_RANGE = (55, 75)   # 自动浇水后湿度跳升区间
TEMP_BASE = 23.0              # 室温基线 ℃
TEMP_AMP = 3.0                # 昼夜温差幅度 ℃
LIGHT_DAY_PEAK = 800.0        # 白天光照峰值 lux
LIGHT_NIGHT = 20.0            # 夜间光照 lux

# 全局供电自动循环：(状态, 持续秒) 循环播放
#   0=市电(充电) 1=电池(放电) 2=断电(冻结+不响应)
POWER_SCHEDULE = [
    (0, 90),
    (1, 90),
    (2, 45),
]
MANUAL_HOLD_S = 300           # 手动命令后暂缓自动循环的秒数
BATT_CHARGE = 0.20            # 市电充电 %/s
BATT_DRAIN = 0.05             # 电池放电 %/s

CMD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "power_cmd.txt")
# =================================


class PowerSupply:
    """全局供电状态：自动循环 + 手动覆盖。"""

    def __init__(self):
        self.cycle_start = time.time()
        self.state = 0
        self.manual_hold_until = 0.0

    def _scheduled(self, now):
        total = sum(d for _, d in POWER_SCHEDULE)
        t = (now - self.cycle_start) % total
        acc = 0
        for st, d in POWER_SCHEDULE:
            acc += d
            if t < acc:
                return st
        return POWER_SCHEDULE[-1][0]

    def update(self, now):
        if now < self.manual_hold_until:
            return
        self.state = self._scheduled(now)

    def force(self, st, now):
        self.state = st
        self.manual_hold_until = now + MANUAL_HOLD_S


POWER = PowerSupply()


class Pot:
    """单个花盆的模拟状态。tick() 用真实时间差演化，访问频率无关。"""

    def __init__(self, uid, init_moist, init_temp, init_ec, evap, auto_water, init_batt):
        self.uid = uid
        self.moist = init_moist
        self.temp = init_temp
        self.ec = init_ec
        self.evap = evap
        self.auto_water = auto_water
        self.batt = init_batt
        self.light = LIGHT_NIGHT
        self.power_override = None   # None=跟随全局, 0/1/2=手动固定
        self.last_tick = None

    def effective_power(self):
        return self.power_override if self.power_override is not None else POWER.state

    def tick(self, now):
        if self.last_tick is None:
            dt = 1.0
        else:
            dt = min(max(now - self.last_tick, 0.0), 60.0)
        self.last_tick = now

        p = self.effective_power()

        # 电量演化
        if p == 0:
            self.batt = min(100.0, self.batt + BATT_CHARGE * dt)
        elif p == 1:
            self.batt = max(0.0, self.batt - BATT_DRAIN * dt)

        # 断电：数据冻结（action 会返回异常，读不到，这里不演化）
        if p == 2:
            return

        # 湿度：随时间蒸发下降
        self.moist -= self.evap * dt * random.uniform(0.7, 1.3)
        if self.auto_water and self.moist < DRY_WARNING:
            self.moist = random.uniform(*AUTO_WATER_RANGE)  # 浇水事件
        self.moist = max(0.0, min(100.0, self.moist))

        # 温度：室温基线 + 昼夜正弦（峰值约 14 点）+ 噪声
        lt = time.localtime(now)
        local_hour = lt.tm_hour + lt.tm_min / 60.0
        self.temp = TEMP_BASE + TEMP_AMP * math.sin((local_hour - 8) / 24 * 2 * math.pi) \
            + random.uniform(-0.3, 0.3)

        # 光照：6-18 点白天正弦包络
        if 6 <= local_hour <= 18:
            progress = (local_hour - 6) / 12.0
            self.light = LIGHT_DAY_PEAK * math.sin(progress * math.pi) \
                + random.uniform(-10, 10)
        else:
            self.light = LIGHT_NIGHT
        self.light = max(0.0, self.light)

        # EC：缓慢漂移
        self.ec += random.uniform(-0.005, 0.005)
        self.ec = max(0.5, min(3.0, self.ec))

    def registers(self):
        """返回 6 个保持寄存器的值（与寄存器表对应）。"""
        return [
            int(round(self.moist)),
            int(round(self.temp * 10)),
            int(round(self.light)),
            int(round(self.ec * 100)),
            int(round(self.batt)),
            int(round(self.effective_power())),
        ]


def make_action(pot):
    """为单个花盆构造 SimDevice 的 action 回调。"""
    async def action(function_code, start_address, address, count,
                     current_registers, set_values):
        if pot.effective_power() == 2:
            return ExcCodes.DEVICE_FAILURE   # 断电：不回答（返回异常）
        pot.tick(time.time())
        regs = pot.registers()
        for i in range(min(len(current_registers), len(regs))):
            current_registers[i] = regs[i]
        return None
    return action


def build_devices():
    """为每个花盆建一个 SimDevice（一个 Modbus 从站）。"""
    devices = []
    pots = []
    pot_by_uid = {}
    for i, cfg in enumerate(POTS[:POT_COUNT]):
        uid = BASE_UID + i
        init_moist, init_temp, init_ec, evap, auto, init_batt = cfg
        pot = Pot(uid, init_moist, init_temp, init_ec, evap, auto, init_batt)
        sim = SimData(
            address=0,
            values=pot.registers(),
            datatype=DataType.REGISTERS,
        )
        devices.append(SimDevice(id=uid, simdata=[sim], action=make_action(pot)))
        pots.append(pot)
        pot_by_uid[uid] = pot
    return devices, pots, pot_by_uid


def handle_cmd(content, pots, pot_by_uid):
    """解析 power_cmd.txt 内容并执行。"""
    parts = content.split()
    if not parts:
        return
    cmd = parts[0].lower()
    now = time.time()
    if cmd == "on" and len(parts) < 2:
        POWER.force(0, now)
    elif cmd == "batt":
        POWER.force(1, now)
    elif cmd == "off" and len(parts) < 2:
        POWER.force(2, now)
    elif cmd == "off" and len(parts) > 1:
        pot_by_uid[int(parts[1])].power_override = 2
    elif cmd == "on" and len(parts) > 1:
        pot_by_uid[int(parts[1])].power_override = 0
    elif cmd == "auto" and len(parts) < 2:
        POWER.manual_hold_until = 0
        for p in pots:
            p.power_override = None
    elif cmd == "auto" and len(parts) > 1:
        pot_by_uid[int(parts[1])].power_override = None
    print("[cmd] %s" % content)


def cmd_loop(pots, pot_by_uid):
    """后台线程：刷新全局供电状态 + 轮询命令文件。"""
    last = None
    while True:
        now = time.time()
        POWER.update(now)
        content = ""
        try:
            with open(CMD_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
        except FileNotFoundError:
            pass
        if content != last:
            last = content
            handle_cmd(content, pots, pot_by_uid)
        time.sleep(0.5)


def main():
    devices, pots, pot_by_uid = build_devices()
    threading.Thread(target=cmd_loop, args=(pots, pot_by_uid), daemon=True).start()

    print("[fake_greenhouse] simulating %d pots (uid %d~%d)" % (POT_COUNT, BASE_UID, BASE_UID + POT_COUNT - 1))
    print("[fake_greenhouse] Modbus TCP listening on %s:%d" % (HOST, PORT))
    print("[fake_greenhouse] regs: 0x0000 moist 0x0001 temp x10 0x0002 light 0x0003 EC x100 0x0004 batt 0x0005 power(0=ac 1=batt 2=off)")
    print("[fake_greenhouse] power auto-cycle %s, manual via %s" % (POWER_SCHEDULE, CMD_FILE))
    print("[fake_greenhouse] Ctrl+C to stop")
    try:
        StartTcpServer(devices, address=(HOST, PORT))
    except KeyboardInterrupt:
        print("\n[fake_greenhouse] stopped")


if __name__ == "__main__":
    main()
