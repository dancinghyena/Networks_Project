#!/usr/bin/env bash
IFACE=${1:-eth0}
sudo tc qdisc del dev "$IFACE" root 2>/dev/null || true
echo "[netem] reset on $IFACE"
sudo tc qdisc show dev "$IFACE"
