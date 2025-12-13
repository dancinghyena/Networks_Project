#!/usr/bin/env bash
set -e

IFACE=${1:-eth0}
SCENARIO=${2:-baseline}

reset() {
  sudo tc qdisc del dev "$IFACE" root 2>/dev/null || true
}

case "$SCENARIO" in
  baseline)
    reset
    ;;

  loss_20)
    reset
    sudo tc qdisc add dev "$IFACE" root netem loss 20%
    ;;

  delay_200)
    reset
    sudo tc qdisc add dev "$IFACE" root netem delay 200ms
    ;;

  jitter_150)
    reset
    sudo tc qdisc add dev "$IFACE" root netem delay 100ms 150ms distribution normal
    ;;

  burst_small)
    reset
    sudo tc qdisc add dev "$IFACE" root netem loss 10% 25%
    ;;

  burst_medium)
    reset
    sudo tc qdisc add dev "$IFACE" root netem loss 20% 50%
    ;;

  burst_high)
    reset
    sudo tc qdisc add dev "$IFACE" root netem loss 30% 75%
    ;;

  *)
    echo "Usage: $0 <iface> {baseline|loss_20|delay_200|jitter_150|burst_small|burst_medium|burst_high}"
    exit 1
    ;;
esac

echo "[netem] Applied scenario: $SCENARIO on iface: $IFACE"
sudo tc qdisc show dev "$IFACE"
