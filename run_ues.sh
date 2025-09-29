#!/bin/bash
set -e

cd /home/minhkeke/Code/5G-RAN/srs2/srsRAN_4G/build/srsue/src

ip netns add ue1 || true
ip netns add ue2 || true
ip netns add ue3 || true

./srsue ./ue1_zmq.conf &
./srsue ./ue2_zmq.conf &
./srsue ./ue3_zmq.conf &

wait
