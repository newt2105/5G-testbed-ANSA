#!/usr/bin/env python3

import gymnasium as gym
import numpy as np
import torch as th
import signal
import threading
import queue
import os
from gymnasium import spaces
from stable_baselines3 import PPO
from stable_baselines3 import DQN
from stable_baselines3.common.env_checker import check_env
from my_xapp import MonRcApp
from enum import Enum
from datetime import date
from collections import Counter

class xAppEnv(gym.Env):
    def __init__(self, xapp: MonRcApp, queue: queue.Queue, n_steps: int, log_dir: str):
        super(xAppEnv, self).__init__()
        self.debug = True
        # DRL Action + State
        self.current_step = 0
        self.n_steps = n_steps
        self.observation_space = spaces.Box(low=-1, high=1, shape=(12,), dtype=np.float32)
        self.prb_pairs = np.array([
            [10, 20], [13, 17], [15, 15], [17, 13], [20, 10], # Terrible choices, sums to 30
            [20, 40], [25, 35], [30, 30], [35, 25], [40, 20], # Bad choices, sums to 60
            [25, 75], [30, 70], [35, 65], [40, 60], [45, 55], [50, 50], [55, 45], [60, 40], [65, 35], [70, 30], [75, 25] # Good choices, sums to 100
        ], dtype=np.int32)
        self.action_space = spaces.Discrete(len(self.prb_pairs))
        self.state = np.zeros(12)
        self.kpm_queue = queue
        self.starting_episode = 1
        self.current_episode = 0
        self.log_dir = log_dir
        self.action_history = Counter({d: 0 for d in range(len(self.prb_pairs))})

        # Values specific for the use case
        # Got by trial and error, for 10MHz bandwidth
        self.max_throughput = 32000 #30669
        self.total_prbs = 52
        self.max_packets = 1000
        self.ues = 2
        self.prb_diff = 3
        self.prbs = [50, 50]
        self.max_delay = 80000.0 # 126819

        self.xapp = xapp
        self.xapp_thread = threading.Thread(target=self.xapp.start)
        self.xapp_thread.start()

    def _process_actions(self):
        if self.current_episode != 0 and self.current_episode % 25 == 0:
            if self.debug:
                print(f"Updating {self.log_dir}/action.logs")
                print(self.action_history)

            # Store frequency of actions in a given episode
            with open(f"{self.log_dir}/actions.log", "a") as f:
                f.write(f"Epizody {self.starting_episode} -> {self.current_episode}\n")
                for i in range(len(self.prb_pairs)):
                    f.write(f" {self.prb_pairs[i]}: {self.action_history.get(i, 0)}\n")
            self.starting_episode = self.current_episode + 1
            self.action_history = Counter({d: 0 for d in range(len(self.prb_pairs))})

    def reset(self, seed=None, options=None):
        self.episode_reward = 0.0  # reset reward cho episode mới
        self.current_step = 0
        self.prbs = [50, 50]
        self._apply_prbs()
        self._process_actions()
        self.state = np.zeros(12, dtype=np.float32)
        while not self.kpm_queue.empty():
            # Clear the queue or we'll perform actions based on slightly older KPMs
             self.kpm_queue.get()
        return self.state, {}

    def step(self, action):
        # Action is choosing a pair of prbs and then applying it
        # print("action: ", action)
        self.action_history[action] += 1
        if self.prbs != self.prb_pairs[action].tolist():
            self.prbs = self.prb_pairs[action].tolist()
            self._apply_prbs()
        if self.debug and self.current_step % 25 == 0:
            print(f"Current prbs: {self.prbs}")

        # Fetch updated KPMs, remove stale KPMs
        while self.kpm_queue.qsize() > 1:
            try:
                self.kpm_queue.get_nowait()
            except:
                break
        kpms = self.kpm_queue.get()
        # print("kpms: ", kpms)

        # Get new state
        self.state = self._decode_kpms(kpms, self.max_throughput, self.total_prbs, self.max_delay)
        # print("state: ", self.state)

        # Reward: Total Throughput + Delay
        splitted = kpms.split(';')
        thp_target = 10000   # 10 Mbps 
        delay_target = 500  # 50 ms

        throughput = self._safe_float(splitted[0])
        applied_prbs = [int(x) for x in splitted[self.ues:2*self.ues]]
        delay = self._safe_float(splitted[6*self.ues - 1])
        # delay_avg = np.mean(delay) if delay else 0.0
        # thp_avg = np.mean(throughputs) if throughputs else 0.0
        # r_thp = min(throughput / self.max_throughput, 1.0)
        # r_fair = self._jain_fairness(throughputs)
        r_prbs = sum(applied_prbs) / self.total_prbs
        # r_delay = max(0.0, 1.0 - delay / delay_target)

        delay_avg = delay
        thp_avg = throughput

        r_thp = max(-1, min(1, (thp_avg - thp_target) / thp_target))

        r_delay = max(-1, min(1, (delay_target - delay_avg) / delay_target))
        
        reward = 0.5 * r_thp + 0.5 * r_delay
        self.episode_reward += reward  # cộng dồn reward

        if self.debug and self.current_step % 25 == 0:
            print(f"Reward ({reward:.4f}) -> Thp: {r_thp:.4f}, Delay: {r_delay:.4f}, PRBs: {r_prbs:.4f} ({applied_prbs})")

        done = False
        self.current_step += 1
        if self.current_step == self.n_steps:
            self.current_episode += 1
            done = True
            self._log_episode_reward()  # khi episode kết thúc thì log reward
        
        return self.state, reward, done, False, {}

    def _decode_kpms(self, kpms, max_throughput, total_prbs, max_delay):
        splitted = kpms.split(';')
        if self.debug and self.current_step % 25 == 0:
            print(f"Splitted: {splitted}")
        st = np.zeros(self.observation_space.shape, dtype=self.observation_space.dtype)
        for i in range(len(splitted) - self.ues):
            min_v = 0
            if i < self.ues: # throughput
                max_v = max_throughput
            elif i < 2 * self.ues: # prbs
                max_v = total_prbs
            elif i < 3 * self.ues: # mcs
                max_v = 28
            elif i in range(5 * self.ues, 6 * self.ues): # delay
                max_v = max_delay  # microseconds

            value = self._safe_float(splitted[i])
            if i < 3 * self.ues: # thp, prbs, mcs
                st[i] = self._normalize(value, min_v, max_v)
            elif i in range(3 * self.ues, 5 * self.ues): # lost packets
                st[i] = self._safe_float(splitted[i+2]) / self._safe_float(splitted[i]) if self._safe_float(splitted[i]) != 0 else 0
                if self.debug and self.current_step % 25 == 0:
                    print(f"{st[i] * 100:.4f}% loss")

            if i in range(5 * self.ues, 6 * self.ues): # delay
                st[i] = self._normalize(value, min_v, max_v)

        return st

    def _normalize(self, value, min_v, max_v):
        return 2 * (value - min_v) / (max_v - min_v) - 1 # min max scaling [-1;1]

    def _apply_prbs(self, id=-1):
        if id < 0:
            for ue_id in range(self.ues):
                self.xapp.set_prb(ue_id, self.prbs[ue_id])
        else:
            if self.debug and self.current_step % 25 == 0:
                print(f"Applying PRBS: {self.prbs[id]} for UE: {id}")
            self.xapp.set_prb(id, self.prbs[id])

    def _safe_float(self, x):
        try:
            if x is None or x == '' or str(x).lower() in ['none', 'nan']:
                return 0.0
            return float(x)
        except (ValueError, TypeError):
            return 0.0

    def _log_episode_reward(self):
            log_file = os.path.join(self.log_dir, "rewards.txt")
            with open(log_file, "a") as f:
                avg_reward = self.episode_reward / self.n_steps
                f.write(f"Episode {self.current_episode}: total={self.episode_reward:.4f}, avg={avg_reward:.4f}\n")

if __name__ == "__main__":
    # with granularity period 50, 1000 steps take 50 seconds
    # 150 steps -> 7.5s
    # with 150ms - 1000 steps takes 150 seconds, 2.5min
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
    # Create xApp for fetching KPM and setting PRBs
    queue = queue.Queue()
    xApp = MonRcApp(queue, kpm_log, False)
    ran_func_id = 2
    xApp.e2sm_kpm.set_ran_func_id(ran_func_id)
    # Connect exit signals
    signal.signal(signal.SIGQUIT, xApp.signal_handler)
    signal.signal(signal.SIGTERM, xApp.signal_handler)
    signal.signal(signal.SIGINT, xApp.signal_handler)

    # Learning
    env = xAppEnv(xApp, queue, steps, drl_log)
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
            learning_rate=1e-3,
            batch_size=32,
            gamma=0.99,
            verbose=1,
            tensorboard_log=drl_log,
            policy_kwargs=policy_kwargs
        )
    elif algorithm == "DQN":
        # keep the same activation and a comparable network size
        policy_kwargs = dict(
            activation_fn=th.nn.Tanh,
            net_arch=[64, 64],
        )
        model = DQN(
            policy="MlpPolicy",
            env=env,
            learning_rate=5e-4,
            gamma=0.99,
            batch_size=64,
            buffer_size=50_000,           # replay buffer
            learning_starts=2_000,        # warm-up before updates
            train_freq=4,                # update every 4 env steps
            gradient_steps=1,            # 1 gradient step per training call
            target_update_interval=2_000,# sync target net every 2k updates
            exploration_initial_eps=1.0, # ε-greedy: start fully random
            exploration_fraction=0.1,    # decay ε over first 10% of training
            exploration_final_eps=0.02,  # final ε
            tensorboard_log=drl_log,
            verbose=1,
            policy_kwargs=policy_kwargs,
        )
    else:
        raise ValueError(f"Unknown algorithm: {algorithm}")

    model.learn(total_timesteps=int(iterations * steps))
    model.save(f"{drl_log}/{config_name}")
    env.xapp.stop() # Calls stop function from xAppBase
    env.xapp_thread.join()
