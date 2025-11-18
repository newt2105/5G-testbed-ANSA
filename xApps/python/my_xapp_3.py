#!/usr/bin/env python3

import time
import datetime
import argparse
import signal
import os
import queue
from lib.xAppBase import xAppBase
import numpy as np
from stable_baselines3 import DQN  
import torch


class MonRcApp(xAppBase):
    def __init__(self, queue: queue.Queue, log_file: str, debug = False, http_server_port=8092, rmr_port=4560):
        super(MonRcApp, self).__init__(None, http_server_port, rmr_port)
        self.debug = debug
        self.kpm_queue = queue
        self.e2_node_id = "gnbd_001_001_00019b_0"
        self.metrics = ["DRB.UEThpDl", 
                        "RRU.PrbUsedDl", 
                        "OkDl", 
                        "NokDl", 
                        "McsDl", 
                        "DRB.RlcSduDelayDl"]
        # Log for graphs and xApp
        self.log_file = log_file
        self._init_log()

    # Callback when a KPM indication is received
    def my_subscription_callback(self, e2_agent_id, subscription_id, indication_hdr, indication_msg):
        indication_hdr = self.e2sm_kpm.extract_hdr_info(indication_hdr)
        meas_data = self.e2sm_kpm.extract_meas_data(indication_msg)
        thp, prbs, mcs, ok, nok, delay = [], [], [], [], [], []

        if self.debug:
            print("\nRIC Indication Received from {} for Subscription ID: {}, KPM Report Style: 4".format(e2_agent_id, subscription_id))
            print("E2SM_KPM RIC Indication Content:")
            print("-ColletStartTime: ", indication_hdr['colletStartTime'])
            print("-Measurements Data:")

        granulPeriod = meas_data.get("granulPeriod", None)
        if granulPeriod is not None and self.debug:
            print("-granulPeriod: {}".format(granulPeriod))

        for ue_id, ue_meas_data in meas_data["ueMeasData"].items():
            if self.debug:
                print("--UE_id: {}".format(ue_id))
            granulPeriod = ue_meas_data.get("granulPeriod", None)
            if granulPeriod is not None and self.debug:
                print("---granulPeriod: {}".format(granulPeriod))

            for metric_name, value in ue_meas_data["measData"].items():
                if self.debug:
                    print("---Metric: {}, Value: {}".format(metric_name, value))
                if metric_name == "DRB.UEThpDl":
                        thp.append(f"{str(value[0])}")
                elif metric_name == "RRU.PrbUsedDl":
                        prbs.append(f"{str(value[0])}")
                elif metric_name == "McsDl":
                        mcs.append(f"{str(value[0])}")
                elif metric_name == "OkDl":
                        ok.append(f"{str(value[0])}")
                elif metric_name == "NokDl":
                        nok.append(f"{str(value[0])}")
                elif metric_name == "DRB.RlcSduDelayDl":
                    if value and value[0] is not None:
                        delay.append(f"{str(abs(value[0]))}")  # ensure non-negative value
                    else:
                        delay.append("0")  # or default value, e.g. 0

        current = ";".join(thp + prbs + mcs + ok + nok + delay)
        self.kpm_queue.put(current)
        with open(self.log_file, "a") as f:
            expected_zero = ";".join(["0"] * (len(thp) + len(prbs) + len(mcs) + len(ok) + len(nok) + len(delay)))
            if current != expected_zero:
                f.write(f"{current}\n")

    def set_prb(self, ue_id, prb_ratio):
        if self.debug:
            print(f"Setting slice level prb quota to {prb_ratio} for ue {ue_id}")
        self.e2sm_rc.control_slice_level_prb_quota(
            self.e2_node_id,
            ue_id,
            min_prb_ratio=0,
            max_prb_ratio=max(1,prb_ratio),
            dedicated_prb_ratio=max(1,prb_ratio),
            ack_request=1,
        )
    
    def load_trained_model(self, model_path="/home/duc/result/embb_urllc/DQN-Tanh-64x64.zip"):
        self.model = DQN.load(model_path)
        print(f"[INFO] Loaded trained model from {model_path}")

    def run_inference_once(self):
        try:
            current = self.kpm_queue.get_nowait()
            values = [float(v) for v in current.split(';')]
            obs = np.array(values, dtype=np.float32).reshape(1, -1)

            action, _states = self.model.predict(obs, deterministic=True)

            if isinstance(action, (list, np.ndarray)):
                for ue_id, prb_ratio in enumerate(action):
                    self.set_prb(ue_id, float(prb_ratio))
            else:
                self.set_prb(0, float(action))

            if self.debug:
                print(f"[RL] Obs={obs}, Action={action}")

        except queue.Empty:
            pass
        except Exception as e:
            print(f"[ERROR] Inference failed: {e}")


    # Mark the function as xApp start function using xAppBase.start_function decorator.
    # It is required to start the internal msg receive loop.
    @xAppBase.start_function
    def start(self):
        report_period = 150
        granul_period = 150
        # xApp will use E2SM KPM Report Style 4
        # It will store the last state for all of them to return it to the environment when asked
        subscription_callback = lambda agent, sub, hdr, msg: self.my_subscription_callback(agent, sub, hdr, msg)

        # dummy matching UE condition to get IDs of all connected UEs
        matchingUeConds = [{'testCondInfo': {'testType': ('ul-rSRP', 'true'), 'testExpr': 'lessthan', 'testValue': ('valueInt', 1000)}}]
        
        if self.debug:
            print("Subscribe to E2 node ID: {}, RAN func: e2sm_kpm, Report Style: 4, metrics: {}".format(self.e2_node_id, self.metrics))
        self.e2sm_kpm.subscribe_report_service_style_4(self.e2_node_id, report_period, matchingUeConds, self.metrics, granul_period, subscription_callback)

    def _init_log(self):
        header = ['UE0_Throughput', 'UE1_Throughput', 'UE2_Throughput',   # added UE2
                'UE0_PRBs_Used', 'UE1_PRBs_Used', 'UE2_PRBs_Used',        # added UE2
                'UE0_MCS', 'UE1_MCS', 'UE2_MCS',                          # added UE2
                'UE0_OK', 'UE1_OK', 'UE2_OK',                             # added UE2
                'UE0_NOK', 'UE1_NOK', 'UE2_NOK',                          # added UE2
                'UE0_Delay', 'UE1_Delay', 'UE2_Delay']                    # added UE2 
        with open(self.log_file, 'a') as f:
            h = ';'.join(header)
            f.write(f"{h}\n")


if __name__ == '__main__':
    # Create the xApp
    q = queue.Queue()
    log_file = "/tmp/kpm_log.csv"   # or other path you want

    xApp = MonRcApp(q, log_file, debug=True)   # send all parameters
    ran_func_id = 2
    xApp.e2sm_kpm.set_ran_func_id(ran_func_id)

    # Connect exit signals.
    signal.signal(signal.SIGQUIT, xApp.signal_handler)
    signal.signal(signal.SIGTERM, xApp.signal_handler)
    signal.signal(signal.SIGINT, xApp.signal_handler)

    # Start the xApp
    xApp.start()
    # Note: xApp will unsubscribe all active subscriptions at exit

    # Load model 
    xApp.load_trained_model("/home/duc/result/embb_urllc/DQN-Tanh-64x64.zip")

    print("[INFO] Starting real-time inference loop...")
    while True:
        xApp.run_inference_once()
        time.sleep(3)  # adjust the frequency (for example 0.5s, 2s...)
