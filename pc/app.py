#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app.py — Smart Agri Gateway 可视化面板（Streamlit）。

运行： streamlit run app.py
数据源：smart_agri.db（由 mqtt_sub.py 写入）。页面每 10s 自动刷新。
"""
import sqlite3
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config

POWER_STR = {0: "AC", 1: "BATT", 2: "OFF"}
POTS = range(1, 6)  # 花盆 uid 范围


def db_conn():
    return sqlite3.connect(config.DB_PATH)


def load_latest():
    """每盆最新一条状态。"""
    conn = db_conn()
    df = pd.read_sql_query(
        "SELECT uid, moist, temp_c, light, ec, batt, power, offline, MAX(recv_ts) as rts "
        "FROM state GROUP BY uid", conn)
    conn.close()
    return df


def load_history(uid, limit=300):
    """指定盆最近 limit 条，时间升序。"""
    conn = db_conn()
    df = pd.read_sql_query(
        "SELECT recv_ts AS t, moist, temp_c, light, ec, batt, power, offline "
        "FROM state WHERE uid=? ORDER BY id DESC LIMIT ?",
        conn, params=(uid, limit))
    conn.close()
    if df.empty:
        return df
    return df.iloc[::-1].reset_index(drop=True)


def load_last_hb():
    conn = db_conn()
    row = conn.execute("SELECT ts, status, recv_ts FROM heartbeat ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return row


def hb_status_markup(row):
    if not row:
        return "no data"
    ts, status, recv_ts = row
    age = int(time.time() - recv_ts)
    if age <= config.HEARTBEAT_TIMEOUT_S:
        return ":green[ONLINE]  %s  %ds ago" % (status, age)
    return ":red[OFFLINE]  %s  %ds ago" % (status, age)


st.set_page_config(page_title="Smart Agri Gateway", layout="wide")
st.markdown('<meta http-equiv="refresh" content="10">', unsafe_allow_html=True)

st.title("Smart Agri Gateway")

hb = load_last_hb()
st.caption("Gateway: " + hb_status_markup(hb))

latest = load_latest()

# ---- 每盆状态卡片 ----
cols = st.columns(len(POTS))
for col, uid in zip(cols, POTS):
    row = latest[latest["uid"] == uid]
    with col:
        st.subheader("Pot %d" % uid)
        if row.empty:
            st.error("no data")
            continue
        r = row.iloc[0]
        pwr = POWER_STR.get(int(r["power"]), "?")
        offline = int(r["offline"])
        if offline:
            st.error("OFFLINE")
        st.metric("Moisture", "%.0f%%" % r["moist"])
        st.metric("Temp", "%.1f C" % r["temp_c"])
        st.metric("Light", "%d lux" % r["light"])
        st.metric("EC", "%.2f" % r["ec"])
        st.metric("Battery", "%.0f%%" % r["batt"])
        st.write("Power: **%s**" % pwr)

st.divider()

# ---- 趋势曲线 ----
def trend_fig(metric, title, unit=""):
    fig = go.Figure()
    for uid in POTS:
        df = load_history(uid)
        if df.empty:
            continue
        x = pd.to_datetime(df["t"], unit="s")
        fig.add_trace(go.Scatter(x=x, y=df[metric], mode="lines",
                                 name="Pot %d" % uid))
    fig.update_layout(title=title, height=280, margin=dict(l=10, r=10, t=40, b=10))
    fig.update_yaxes(title_text=unit)
    return fig


c1, c2 = st.columns(2)
with c1:
    st.plotly_chart(trend_fig("moist", "Soil Moisture %", "%"), width="stretch")
with c2:
    st.plotly_chart(trend_fig("temp_c", "Temperature C", "C"), width="stretch")

c3, c4 = st.columns(2)
with c3:
    st.plotly_chart(trend_fig("batt", "Battery %", "%"), width="stretch")
with c4:
    st.plotly_chart(trend_fig("light", "Light lux", "lux"), width="stretch")

st.divider()

# ---- 原始数据表 ----
if st.checkbox("Show raw data", value=False):
    conn = db_conn()
    df = pd.read_sql_query(
        "SELECT uid, datetime(recv_ts,'unixepoch','+8 hours') AS time, "
        "moist, temp_c, light, ec, batt, power, offline "
        "FROM state ORDER BY id DESC LIMIT 100", conn)
    conn.close()
    st.dataframe(df, width="stretch")
