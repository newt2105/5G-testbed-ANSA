#!/usr/bin/env python3
import csv
import os
import time
import queue
import random
import threading
from my_xapp import MonRcApp

class KpmDataCollector:
    def __init__(self, xapp: MonRcApp, queue: queue.Queue, 
                 csv_file="kpm_data.csv", n_samples=300, debug=True):
        self.xapp = xapp
        self.kpm_queue = queue
        self.csv_file = csv_file
        self.n_samples = n_samples
        self.debug = debug

        self.output_dir = os.path.dirname(csv_file)
        if self.output_dir and not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
        
        # Action set: PRB pairs
        self.prb_actions = [
            [10, 20], [13, 17], [15, 15], [17, 13], [20, 10],
            [20, 40], [25, 35], [30, 30], [35, 25], [40, 20], 
            [30, 70], [40, 60], [50, 50], [60, 40], [70, 30],
        ]
                
        # Tạo xApp
        log_file = "/tmp/kpm_log.csv"
        self.xapp = xapp
        self.xapp_thread = threading.Thread(target=self.xapp.start)
        self.xapp_thread.start()
        
        # Đợi xApp subscribe xong
        time.sleep(2.0)
        
        # CSV header
        self.header = [
            "PRB_UE0","PRB_UE1",
            "Thp_UE0","Thp_UE1",
            "PRB_UE0_Used","PRB_UE1_Used",
            "MCS_UE0","MCS_UE1",
            "OK_UE0","OK_UE1",
            "NOK_UE0","NOK_UE1",
            "UE0_Latency","UE1_Latency"
        ]
    
    def _next_csv_file(self):
        i = 1
        while True:
            path = os.path.join(self.output_dir, f"kpm_data_{i}.csv")
            if not os.path.exists(path):
                return path
            i += 1

    def apply_action_and_get_kpm(self, action):
        prb_ue0, prb_ue1 = action

        self.xapp.set_prb(0, prb_ue0)
        self.xapp.set_prb(1, prb_ue1)
        print(f"[COLLECTOR] Applying PRB action: UE0={prb_ue0}%, UE1={prb_ue1}%")

        # Chờ KPM mới
        # while kpm_data is None:
        #     try:
        #         kpm_data = self.kpm_queue.get(timeout=0.2)
        #     except queue.Empty:
        #         pass

        try:
            while True:
                self.kpm_queue.get_nowait()
        except queue.Empty:
            pass

        # while not self.kpm_queue.empty():
        #     try:
        #         self.kpm_queue.get_nowait()
        #     except:
        #         break
        
        time.sleep(2.0)

        try:
            kpm_data = self.kpm_queue.get(timeout=5.0)
        except queue.Empty:
            if self.debug:
                print("[COLLECTOR] No KPM received, using previous values or zeros")
            return [prb_ue0, prb_ue1] + [0.0]*12

        splitted = kpm_data.split(';')
        
        # row = [prb_ue0, prb_ue1] + [
        #     float(x) if x not in ["None", None, ""] else 0.0
        #     for x in splitted[:12]
        # ]
        # return row

        parsed = []
        for x in splitted[:12]:
            if x in ["None", None, "", "null"]:
                parsed.append(0.0)
            else:
                try:
                    parsed.append(float(x))
                except:
                    parsed.append(0.0)

        while len(parsed) < 12:
            parsed.append(0.0)

        return [prb_ue0, prb_ue1] + parsed
    
    def run(self):
        csv_file = self._next_csv_file()
        if self.debug:
            print(f"[COLLECTOR] Writing data to {csv_file}")
        
        actions = self.prb_actions.copy()
        # random.shuffle(actions)

        with open(csv_file, "w", newline='') as f:
            writer = csv.writer(f)
            writer.writerow(self.header)
            
            for i in range(self.n_samples):
                action = actions[i % len(actions)]
                row = self.apply_action_and_get_kpm(action)
                writer.writerow(row)
                if self.debug:
                    print(f"[COLLECTOR] [{i+1}/{self.n_samples}] Row written: {row}")
                time.sleep(3.0)  # điều chỉnh tần suất thu thập
        
        # Stop xApp
        self.xapp.stop()
        self.xapp_thread.join()
        print(f"[COLLECTOR] Data collection finished. Saved to {self.csv_file}")


if __name__ == "__main__":
    kpm_log = "/tmp/kpm_data.log"
    queue = queue.Queue()
    xApp = MonRcApp(queue, kpm_log, False)
    collector = KpmDataCollector(xApp, queue, 
                                 csv_file="kpm_output.csv", 
                                 n_samples=300, debug=True)
    collector.run()