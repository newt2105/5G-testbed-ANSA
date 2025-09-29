# #!/bin/bash
# set -e

# # Thêm route (xóa trước nếu đã tồn tại)
# sudo ip route del 10.45.0.0/16 via 10.53.1.2 2>/dev/null || true
# sudo ip route add 10.45.0.0/16 via 10.53.1.2

# # Routes cho UE
# sudo ip netns exec ue1 ip route replace default via 10.45.1.1 dev tun_srsue
# sudo ip netns exec ue2 ip route replace default via 10.45.2.1 dev tun_srsue
# # sudo ip netns exec ue3 ip route replace default via 10.45.3.1 dev tun_srsue

# # iperf song song
# sudo ip netns exec ue1 iperf -c 10.45.1.1 -u -b 10M -i 1 -t 100 > ue1.log 2>&1 &
# sudo ip netns exec ue2 iperf -c 10.45.2.1 -u -b 10M -i 1 -t 100 > ue2.log 2>&1 &
# # sudo ip netns exec ue3 iperf -c 10.45.3.1 -u -b 10M -i 1 -t 100 > ue3.log 2>&1 &

# wait


#!/bin/bash
set -e

# ==== Cấu hình ====
DURATION=3000        # Thời gian chạy iperf (giây)
BANDWIDTH="100M"    # Băng thông UDP cho mỗi UE
INTERVAL=0.1         # Khoảng thời gian in log (giây)

# Địa chỉ UE (cần đúng với cấu hình mạng của bạn)
UE1_NS="ue1"
UE1_IP="10.45.1.2"
UE2_NS="ue2"
UE2_IP="10.45.2.2"
# UE3_NS="ue3"
# UE3_IP="10.45.3.2"

# ==== Route ====
sudo ip route del 10.45.0.0/16 via 10.53.1.2 2>/dev/null || true
sudo ip route add 10.45.0.0/16 via 10.53.1.2

# Routes trong namespace UE
sudo ip netns exec ${UE1_NS} ip route replace default via 10.45.1.1 dev tun_srsue
sudo ip netns exec ${UE2_NS} ip route replace default via 10.45.2.1 dev tun_srsue
# sudo ip netns exec ${UE3_NS} ip route replace default via 10.45.3.1 dev tun_srsue

# ==== Chạy iperf server trong UE ====
echo "Starting iperf servers in UE namespaces..."
sudo ip netns exec ${UE1_NS} iperf -s -u -B ${UE1_IP} > ue1_server.log 2>&1 &
PID_SRV1=$!
sudo ip netns exec ${UE2_NS} iperf -s -u -B ${UE2_IP} > ue2_server.log 2>&1 &
PID_SRV2=$!
# sudo ip netns exec ${UE3_NS} iperf -s -u -B ${UE3_IP} > ue3_server.log 2>&1 &

sleep 1  # chờ server lên

# ==== Chạy iperf client từ gNB/core xuống UE (downlink) ====
echo "Starting downlink traffic..."
iperf -c ${UE1_IP} -u -b ${BANDWIDTH} -t ${DURATION} -i ${INTERVAL} > ue1_downlink.log 2>&1 &
PID_CLI1=$!
iperf -c ${UE2_IP} -u -b ${BANDWIDTH} -t ${DURATION} -i ${INTERVAL} > ue2_downlink.log 2>&1 &
PID_CLI2=$!
# iperf -c ${UE3_IP} -u -b ${BANDWIDTH} -t ${DURATION} -i ${INTERVAL} > ue3_downlink.log 2>&1 &

# ==== Chờ tất cả xong ====
wait ${PID_CLI1} ${PID_CLI2}

# Kill iperf server sau khi test xong
kill ${PID_SRV1} ${PID_SRV2} 2>/dev/null || true

echo "Downlink test hoàn tất. Log lưu trong ue*_downlink.log và ue*_server.log"
