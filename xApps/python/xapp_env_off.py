#!/usr/bin/env python3
import random
import time
import gymnasium as gym
import numpy as np
import torch as th
import signal
import threading
import queue
import os
import csv
from gymnasium import spaces
from stable_baselines3 import PPO
from stable_baselines3 import DQN
from stable_baselines3.common.env_checker import check_env
from enum import Enum
from datetime import date
from collections import Counter


class xAppEnv(gym.Env):
    def __init__(self, xapp = None, queue = None, 
                 n_steps: int = 50, log_dir: str = "./", 
                 csv_file: str = None, 
                 random_start_jitter: bool = True, 
                 ma_window: int = 20):
        super(xAppEnv, self).__init__()
        self.debug = True
        self.current_step = 0
        self.n_steps = n_steps
        self.log_dir = log_dir
        self.csv_file = csv_file
        self.xapp = xapp

        self.random_start_jitter = random_start_jitter
        # moving average window for logging
        self.ma_window = ma_window
        self.episode_rewards_history = []

        self.xapp_thread = None
        if self.xapp is not None:
            self.xapp_thread = threading.Thread(target=self.xapp.start)
            self.xapp_thread.start()
        self.csv_data = self._load_csv(self.csv_file, self.log_dir)
        self.total_samples = len(self.csv_data)
        if self.total_samples == 0:
            raise RuntimeError("[xAppEnv] CSV file contains no data")
        
        self.row_index = 0
        self.prb_pairs = np.array([
            [10, 20], [13, 17], [15, 15], [17, 13], [20, 10], # Terrible choices, sums to 30
            [20, 40], [25, 35], [30, 30], [35, 25], [40, 20], # Bad choices, sums to 60
            [30, 70], [40, 60], [50, 50], [60, 40], [70, 30], # Good choices, sums to 100        
        ], dtype=np.int32)
        # DRL Action + State
        self.observation_space = spaces.Box(low=-1, high=1, shape=(12,), dtype=np.float32)
        self.action_space = spaces.Discrete(len(self.prb_pairs))
        self.state = np.zeros(12, dtype=np.float32)
        self.kpm_queue = queue
        self.starting_episode = 1
        self.current_episode = 0
        self.action_history = Counter({d: 0 for d in range(len(self.prb_pairs))})
        # Values specific for the use case
        # Got by trial and error, for 10MHz bandwidth
        self.max_throughput = 32000 #30669
        self.total_prbs = 52
        self.max_packets = 1000
        self.ues = 2
        self.prb_diff = 3
        self.prbs = [50, 50]
        self.max_latency = 150000 # 126819

    def _process_actions(self):
        if self.current_episode != 0 and self.current_episode % 25 == 0:
            if self.debug:
                print(f"Updating {self.log_dir}/action.logs")
                print(self.action_history)
            # Store frequency of actions in a given episode
            with open(f"{self.log_dir}/actions.log", "a") as f:
                f.write(f"Episode {self.starting_episode} -> {self.current_episode}\n")
                for i in range(len(self.prb_pairs)):
                    f.write(f" {self.prb_pairs[i]}: {self.action_history.get(i, 0)}\n")

            self.starting_episode = self.current_episode + 1
            self.action_history = Counter({d: 0 for d in range(len(self.prb_pairs))})

    def _load_csv(self, csv_file, log_dir):
            if csv_file is None or not os.path.isfile(csv_file):
                raise FileNotFoundError(f"[xAppEnv] CSV file not found: {csv_file}")
            rows = []
            with open(csv_file, "r") as fi:
                reader = csv.reader(fi)
                next(reader, None)  # skip header
                for row in reader:
                    rows.append(row)

            return rows
    
    def reset(self, seed=None, options=None):
        self._process_actions()
        self.current_step = 0
        self.episode_reward = 0.0
        # Jitter start index a little to break periodicity but keep sequentiality within episode
        if self.random_start_jitter:
            # shift by a small random offset so each episode starts at slightly different position
            jitter = np.random.randint(0, max(1, self.n_steps))
            self.row_index = (self.row_index + jitter) % self.total_samples
        # else: keep row_index as-is (strict sequential across episodes)

        self.state = np.zeros(12, dtype=np.float32)
        return self.state, {}
    
    def step(self, action):
        self.action_history[action] += 1
        if self.prbs != self.prb_pairs[action].tolist():
            self.prbs = self.prb_pairs[action].tolist()
            self._apply_prbs()

        if self.debug and self.current_step % 25 == 0:
            print(f"Current prbs: {self.prbs}")

        prb = self.prb_pairs[action]

        # Tìm row trong CSV có PRB gần nhất
        best_idx = None
        best_dist = 9999

        for i, row in enumerate(self.csv_data):
            csv_prb = np.array([int(row[0]), int(row[1])])
            dist = np.linalg.norm(csv_prb - prb)   # khoảng cách Euclid

            if dist < best_dist:
                best_dist = dist
                best_idx = i

        # Dùng row gần nhất làm outcome
        row = self.csv_data[best_idx]

        # row_idx = self.row_index
        # row = self.csv_data[row_idx]
        # self.row_index = (self.row_index + 1) % self.total_samples

        kpms = ";".join(row[2:14])   # bỏ 2 cột PRB đầu
        self.state = self._decode_kpms(kpms, self.max_throughput, self.total_prbs, self.max_latency)
        splitted = kpms.split(';')
        thp_target = 10000   # 10 Mbps 
        latency_target = 50000  # 50 ms / 0.1 

        throughput = self._safe_float(splitted[0])
        applied_prbs = [self._safe_float(x) for x in splitted[self.ues:2*self.ues]]
        latency = self._safe_float(splitted[6*self.ues - 1])
        # latency_avg = np.mean(latency) if latency else 0.0
        # thp_avg = np.mean(throughputs) if throughputs else 0.0
        # r_thp = min(throughput / self.max_throughput, 1.0)
        # r_fair = self._jain_fairness(throughputs)
        r_prbs = sum(applied_prbs) / self.total_prbs
        r_thp = max(-0.2, min(1, (throughput - thp_target) / thp_target))
        r_latency = max(-0.2, min(1, (latency_target - latency) / latency_target))       
        reward = 0.5 * r_thp + 0.5 * r_latency
        self.episode_reward += reward  # cộng dồn reward

        # if self.debug and self.current_step % 25 == 0:
        print(f"[xAppEnv] Reward ({reward:.4f}) -> Thp: {r_thp:.4f}, Latency: {r_latency:.4f}, PRBs: {r_prbs:.4f} ({applied_prbs})")


        # step bookkeeping
        self.current_step += 1
        done = (self.current_step >= self.n_steps)
        info = {}

        if done:
            self.current_episode += 1
            self._log_episode_reward()

        return self.state, reward, done, False, info

    def _decode_kpms(self, kpms, max_throughput, total_prbs, max_latency):
        splitted = kpms.split(';')
        # if self.debug and self.current_step % 25 == 0:
        # print(f"[xAppEnv] Splitted: {splitted}")
        st = np.zeros(self.observation_space.shape, dtype=self.observation_space.dtype)
        for i in range(len(splitted) - self.ues):
            min_v = 0
            if i < self.ues: # throughput
                max_v = max_throughput
            elif i < 2 * self.ues: # prbs
                max_v = total_prbs
            elif i < 3 * self.ues: # mcs
                max_v = 28
            elif i in range(5 * self.ues, 6 * self.ues): # latency
                max_v = max_latency  # microseconds

            value = self._safe_float(splitted[i])
            if i < 3 * self.ues: # thp, prbs, mcs
                st[i] = self._normalize(value, min_v, max_v)
            elif i in range(3 * self.ues, 5 * self.ues): # lost packets
                st[i] = self._safe_float(splitted[i+2]) / self._safe_float(splitted[i]) if self._safe_float(splitted[i]) != 0 else 0
                # if self.debug and self.current_step % 25 == 0:
                #     print(f"{st[i] * 100:.4f}% loss")
            if i in range(5 * self.ues, 6 * self.ues): # latency
                st[i] = self._normalize(value, min_v, max_v)
        return st

    def _normalize(self, value, min_v, max_v):
        return 2 * (value - min_v) / (max_v - min_v) - 1 # min max scaling [-1;1]

    def _apply_prbs(self, id=-1):
            prbs = self.prb_pairs[id] if 0 <= id < len(self.prb_pairs) else self.prb_pairs[self.row_index % len(self.prb_pairs)]
            print(f"[xAppEnv] Applying PRBs: {prbs}")
            self.prbs = prbs.tolist()
            return self.prbs
    
    def _safe_float(self, x):
        try:
            if x is None or x == '' or str(x).lower() in ['none', 'nan']:
                return 0.0
            return float(x)
        except (ValueError, TypeError):
            return 0.0

    def _log_episode_reward(self):
        if self.log_dir is None:
            return
        log_file = os.path.join(self.log_dir, "rewards.txt")
        with open(log_file, "a") as f:
            avg_reward = self.episode_reward / self.n_steps
            f.write(f"[xAppEnv] Episode {self.current_episode}: total={self.episode_reward:.4f}, avg={avg_reward:.4f}\n")

if __name__ == "__main__":
    # with granularity period 50, 1000 steps take 50 seconds
    # 150 steps -> 7.5s
    # with 150ms - 1000 steps takes 150 seconds, 2.5min
    # algorithm = "PPO"
    algorithm = "DQN"  
    iterations = 300 
    steps = 50
    # In the format: Algorithm-ActivationFunction-p<pi layers>-v<vf layers>
    config_name = f"{algorithm}-Tanh-64x64"
    # Logs
    log_dir = './update'
    for i in range(1, 100):
        drl_log = f"{log_dir}/{config_name}"
        if i > 1:
            drl_log = f"{log_dir}/{i}-{config_name}"
        if os.path.isdir(drl_log) == False:
            break
    os.makedirs(drl_log, exist_ok=True)
    kpm_log = f"{drl_log}/kpm.log"
    csv_file =  "/opt/xApps/kpm_data_21.csv" # Use latest found in log_dir
    # Create xApp for fetching KPM and setting PRBs
    env = xAppEnv(n_steps=steps, log_dir=drl_log, csv_file=csv_file)    

    # Learning
    if algorithm == "PPO":
        # PPO Custom actor (pi) and value function (vf) networks
        policy_kwargs = dict(
            activation_fn=th.nn.Tanh,
            net_arch=dict(pi=[32, 32], vf=[32, 32])
        )
        model = PPO(
            policy="MlpPolicy",
            env=env,
            n_steps=steps,
            learning_rate=3e-4,
            batch_size=32,
            gamma=0.0,
            verbose=1,
            tensorboard_log=drl_log,
            policy_kwargs=policy_kwargs
        )
    elif algorithm == "DQN":
        # keep the same activation and a comparable network size
        policy_kwargs = dict(
            activation_fn=th.nn.Tanh,
            net_arch=[256, 256],
        )
        model = DQN(
            policy="MlpPolicy",
            env=env,
            learning_rate=5e-4,
            gamma=0.99,
            batch_size=256,
            buffer_size=200_000,           # replay buffer
            learning_starts=2_000,        # warm-up before updates
            train_freq=4,                # update every 4 env steps
            gradient_steps=4,            # 4 gradient step per training call
            target_update_interval=4000,# sync target net every 2k updates
            exploration_initial_eps=1.0, # ε-greedy: start fully random
            exploration_fraction=0.2,    # decay ε over first 10% of training
            exploration_final_eps=0.02,  # final ε
            tensorboard_log=drl_log,
            verbose=1,
            policy_kwargs=policy_kwargs,
        )
    else:
        raise ValueError(f"[xAppEnv] Unknown algorithm: {algorithm}")
    
    start_time = time.time()

    model.learn(total_timesteps=int(iterations * steps))
    
    end_time = time.time()
    duration = end_time - start_time

    print(f"[xAppEnv] Training completed in {duration:.2f} seconds ({duration/60:.2f} minutes)")

    model.save(f"{drl_log}/{config_name}")

    # cleanup if xapp exists
    if getattr(env, "xapp", None):
        try:
            env.xapp.stop()
        except Exception:
            pass
    if getattr(env, "xapp_thread", None):
        try:
            env.xapp_thread.join(timeout=1.0)
        except Exception:
            pass