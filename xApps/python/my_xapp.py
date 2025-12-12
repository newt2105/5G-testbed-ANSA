#!/usr/bin/env python3

import threading
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
        
        # Define PRB allocation pairs for two UEs
        self.prb_pairs = [
            [10, 20], [13, 17], [15, 15], [17, 13], [20, 10], # Terrible choices, sums to 30
            [20, 40], [25, 35], [30, 30], [35, 25], [40, 20], # Bad choices, sums to 60
            [30, 70], [40, 60], [50, 50], [60, 40], [70, 30], # Good choices, sums to 100        
        ]
        self.model = None
        self.log_file = log_file
        self._init_log()

    def my_subscription_callback(self, e2_agent_id, subscription_id, indication_hdr, indication_msg):
        try:
            indication_hdr = self.e2sm_kpm.extract_hdr_info(indication_hdr)
            meas_data = self.e2sm_kpm.extract_meas_data(indication_msg)
            thp, prbs, mcs, ok, nok, latency = [], [], [], [], [], []

            if self.debug:
                print("\n[DEBUG] RIC Indication Received from {} for Subscription ID: {}, KPM Report Style: 4".format(e2_agent_id, subscription_id))
                print("E2SM_KPM RIC Indication Content:")
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
                        val = value[0] if value[0] is not None else 0
                        latency.append(str(val))

            current = ";".join(thp + prbs + mcs + ok + nok + latency)

            self.kpm_queue.put(current)
            with open(self.log_file, "a") as f:
                expected_zero = ";".join(["0"] * (len(thp) + len(prbs) + len(mcs) + len(ok) + len(nok) + len(latency)))
                if current != expected_zero:
                    f.write(f"{current}\n")

        except Exception:
            print("[ERROR] my_subscription_callback failed:")

    def set_prb(self, ue_id, prb_value):
        try:
            self.e2sm_rc.control_slice_level_prb_quota(
                self.e2_node_id,
                ue_id,
                min_prb_ratio = 0,
                max_prb_ratio = prb_value,
                dedicated_prb_ratio = prb_value,
                ack_request=1,
            )
            if self.debug:
                print(f"Setting slice level prb percentage to {prb_value}% for ue {ue_id}")
        except Exception as e:
            print("[ERROR] set_prb() failed:", e)

    def load_trained_model(self, model_path="/opt/xApps/10-DQN-Tanh-64x64/DQN-Tanh-64x64.zip"):
        if not os.path.isfile(model_path):
            print(f"[WARNING] Model file not found: {model_path}. Inference will not work.")
            self.model = None
            return
        self.model = DQN.load(model_path)
        print(f"[INFO] Loaded trained model from {model_path}")

    def run_inference(self):
        if self.model is None:
            if self.debug:
                print("[DEBUG] No model loaded, skipping inference.")
            return
        try:
            last_current = self.kpm_queue.get_nowait()
            if self.debug:
                print("[DEBUG] Drained one item from queue.", last_current)
        except queue.Empty:
            return
        if last_current is None:
            if self.debug:
                print("[DEBUG] No data in queue to run inference.")
            return
        try:
            values = [float(v) for v in last_current.split(';') if v != ""]
            obs = np.array(values, dtype=np.float32).reshape(1, -1)
            action_raw, _ = self.model.predict(obs, deterministic=True)
            action = int(action_raw) if isinstance(action_raw, (int, np.integer)) else int(action_raw.item())
            # Safety check
            if not (0 <= action < len(self.prb_pairs)):
                print(f"[WARNING] Predicted action {action} out of range, skipping.")
                return
            
            prb0, prb1 = self.prb_pairs[action]
            if self.debug:
                print(f"[INFO] Obs={obs.tolist()}, Action={action}, PRBs={(prb0, prb1)}")

            self.set_prb(0, int(prb0))
            self.set_prb(1, int(prb1))       
        except Exception as e:
            print("[ERROR] Interference failed:", e)

    def inference_loop(self):
        while True:
            try:
                self.run_inference()
            except Exception as e:
                print("[ERROR] inference_loop:", e)
            time.sleep(0.15)

    @xAppBase.start_function
    def start(self):
        report_period = 150
        granul_period = 150
        if self.debug:
            print("[DEBUG] Starting real-time inference loop...")
        threading.Thread(target=self.inference_loop, daemon=True).start()
        # xApp will use E2SM KPM Report Style 4
        subscription_callback = lambda agent, sub, hdr, msg: self.my_subscription_callback(agent, sub, hdr, msg)
        # dummy matching UE condition to get IDs of all connected UEs
        matchingUeConds = [{'testCondInfo': {'testType': ('ul-rSRP', 'true'), 'testExpr': 'lessthan', 'testValue': ('valueInt', 1000)}}]
        if self.debug:
            print("[DEBUG] Subscribe to E2 node ID: {}, RAN func: e2sm_kpm, Report Style: 4, metrics: {}".format(self.e2_node_id, self.metrics))
        self.e2sm_kpm.subscribe_report_service_style_4(self.e2_node_id, report_period, matchingUeConds, self.metrics, granul_period, subscription_callback)
    
    def _init_log(self):
        header = ['UE0_Throughput', 'UE1_Throughput',
                'UE0_PRBs_Used', 'UE1_PRBs_Used',
                'UE0_MCS', 'UE1_MCS',
                'UE0_OK', 'UE1_OK',
                'UE0_NOK', 'UE1_NOK',
                'UE0_Latency', 'UE1_Latency']         
        with open(self.log_file, 'a') as f:
            h = ';'.join(header)
            f.write(f"{h}\n")

if __name__ == '__main__':
    # Create the xApp
    q = queue.Queue()
    log_file = "/tmp/kpm_log.csv"   # hoặc đường dẫn bạn muốn
    xApp = MonRcApp(q, log_file, debug=True)   # truyền đủ tham số
    ran_func_id = 2
    xApp.e2sm_kpm.set_ran_func_id(ran_func_id)
    # Load model 
    try:
        xApp.load_trained_model()
    except Exception as e:
        print("[ERROR] loading model failed:", e)
    # Connect exit signals.
    signal.signal(signal.SIGQUIT, xApp.signal_handler)
    signal.signal(signal.SIGTERM, xApp.signal_handler)
    signal.signal(signal.SIGINT, xApp.signal_handler)
    # Start the xApp
    xApp.start()
    # Note: xApp will unsubscribe all active subscriptions at exit
    print("[INFO] Starting real-time inference loop...")