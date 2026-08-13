#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cloud_bridge.py — 本地 broker → 云端 broker 的 MQTT 桥接转发框架。

把本地 broker 上的 agri/# 消息实时转发到云端 broker，实现"经 MQTT 推送云端"。
ESP32 / 软件网关照常只连本地 broker，无需改动；桥接服务负责上云。

特性：
  - 本地订阅、云端发布，双客户端各自自动重连
  - 云端断连期间消息暂存内存队列，恢复后按序补发
  - 可选 TLS（EMQX 8883 / 阿里云 MQTT 等公网 broker 一般要求）
  - 可选 topic 前缀（多站点/租户隔离，如 site1/agri/...）

用法：
  python cloud_bridge.py                                    # 用 config.py 占位符
  python cloud_bridge.py --local-host <broker_ip> \
      --cloud-host your.cloud.emqx.io --cloud-port 8883 \
      --cloud-user user --cloud-pass pass --tls
"""
import argparse
import queue
import threading
import time

import paho.mqtt.client as mqtt

import config

# 云端断连时暂存本地消息的内存队列（FIFO，满了丢弃最旧语义由 queue 上限控制）
FWD_Q = queue.Queue(maxsize=2000)


def make_local_client(sub_topic, args):
    """本地订阅客户端：收到 agri/# 消息 -> 放入转发队列。"""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def on_connect(c, userdata, flags, reason_code, properties):
        c.subscribe(sub_topic)
        print("[local] connected %s:%d, subscribed %s"
              % (args.local_host, args.local_port, sub_topic), flush=True)

    def on_disconnect(c, userdata, disconnect_flags, reason_code, properties):
        print("[local] disconnected, rc=%s" % reason_code, flush=True)

    def on_message(c, userdata, msg):
        try:
            FWD_Q.put_nowait((msg.topic, msg.payload))
        except queue.Full:
            print("[bridge] fwd queue full, drop %s" % msg.topic, flush=True)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    return client


def make_cloud_client(args):
    """云端发布客户端：把队列里的消息 publish 到云端 broker。"""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if args.tls:
        client.tls_set()  # 默认验证公共 CA，适合主流云 broker
    if args.cloud_user:
        client.username_pw_set(args.cloud_user, args.cloud_pass)

    def on_connect(c, userdata, flags, reason_code, properties):
        print("[cloud] connected %s:%d" % (args.cloud_host, args.cloud_port), flush=True)

    def on_disconnect(c, userdata, disconnect_flags, reason_code, properties):
        print("[cloud] disconnected, rc=%s" % reason_code, flush=True)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    return client


def fwd_loop(cloud, args):
    """转发线程：从队列取消息，云端在线则 publish，否则放回队尾重试。"""
    while True:
        topic, payload = FWD_Q.get()
        if not cloud.is_connected():
            try:
                FWD_Q.put((topic, payload))
            except queue.Full:
                pass
            time.sleep(1)
            continue
        out_topic = args.prefix + topic if args.prefix else topic
        info = cloud.publish(out_topic, payload, qos=0)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            print("[bridge] publish rc=%s, requeue %s" % (info.rc, out_topic), flush=True)
            try:
                FWD_Q.put((topic, payload))
            except queue.Full:
                pass
            time.sleep(0.5)


def main():
    ap = argparse.ArgumentParser(description="Local -> cloud MQTT bridge")
    ap.add_argument("--local-host", default=config.MQTT_HOST)
    ap.add_argument("--local-port", type=int, default=config.MQTT_PORT)
    ap.add_argument("--cloud-host", default=config.CLOUD_MQTT_HOST)
    ap.add_argument("--cloud-port", type=int, default=config.CLOUD_MQTT_PORT)
    ap.add_argument("--cloud-user", default=config.CLOUD_MQTT_USER)
    ap.add_argument("--cloud-pass", default=config.CLOUD_MQTT_PASS)
    ap.add_argument("--tls", action="store_true", default=config.CLOUD_MQTT_TLS)
    ap.add_argument("--sub-topic", default=config.CLOUD_SUB_TOPIC)
    ap.add_argument("--prefix", default=config.CLOUD_TOPIC_PREFIX)
    args = ap.parse_args()

    local = make_local_client(args.sub_topic, args)
    local.connect(args.local_host, args.local_port, keepalive=30)
    local.loop_start()

    cloud = make_cloud_client(args)
    cloud.connect(args.cloud_host, args.cloud_port, keepalive=30)
    cloud.loop_start()

    threading.Thread(target=fwd_loop, args=(cloud, args), daemon=True).start()
    print("[bridge] local %s:%d -> cloud %s:%d%s"
          % (args.local_host, args.local_port, args.cloud_host, args.cloud_port,
             " (tls)" if args.tls else ""), flush=True)

    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        local.loop_stop()
        cloud.loop_stop()
        print("\n[bridge] stopped")


if __name__ == "__main__":
    main()
