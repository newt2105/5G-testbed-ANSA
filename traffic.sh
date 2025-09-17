#!/bin/bash
set -e

# Thêm route (xóa trước nếu đã tồn tại)
sudo ip route del 10.45.0.0/16 via 10.53.1.2 2>/dev/null || true
sudo ip route add 10.45.0.0/16 via 10.53.1.2

# Routes cho UE
sudo ip netns exec ue1 ip route replace default via 10.45.1.1 dev tun_srsue
sudo ip netns exec ue2 ip route replace default via 10.45.2.1 dev tun_srsue
# sudo ip netns exec ue3 ip route replace default via 10.45.3.1 dev tun_srsue

# iperf song song
sudo ip netns exec ue1 iperf -c 10.45.1.1 -u -b 10M -i 1 -t 100 > ue1.log 2>&1 &
sudo ip netns exec ue2 iperf -c 10.45.2.1 -u -b 10M -i 1 -t 100 > ue2.log 2>&1 &
# sudo ip netns exec ue3 iperf -c 10.45.3.1 -u -b 10M -i 1 -t 100 > ue3.log 2>&1 &

wait
