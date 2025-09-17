#!/bin/env python3
import pandas as pd
import matplotlib.pyplot as plt

# Obscure global variables
max_thp = 320
max_lost = 20
windo = 10


class Run(object):
    def __init__(self, label, logdir):
        super().__init__()
        self.logdir = logdir
        self.label = label
        self.df = pd.read_csv(f"{logdir}/kpm.log", sep=";")
        self.thps = None
        self.throughput = None
        self.jain = None
        self.reward = None
        self.lost = None
        self.set_metrics(self)
        self.actions = {}
        if "default" not in logdir:
            self.process_actions(f"{logdir}/actions.log")

    def set_metrics(self, prefix="Throughput"):
        thps = self.df[[col for col in self.df.columns if "Throughput" in col]]
        lost = self.df[[col for col in self.df.columns if "NOK" in col]]

        # Sum of throughputs
        sum_throughput = thps.sum(axis=1)
        normalized_throughput = sum_throughput / max_thp

        # Sum of lost
        sum_lost = lost.sum(axis=1)
        normalized_lost = sum_lost / max_lost

        # Jain's fairness index
        numerator = thps.sum(axis=1) ** 2
        denominator = thps.pow(2).sum(axis=1) * thps.shape[1]
        jain_index = numerator / denominator

        reward = 0.4 * normalized_throughput + 0.6 * jain_index
        print("reward:", reward)

        self.throughput = sum_throughput.rolling(window=windo).mean()
        self.jain = jain_index.rolling(window=windo).mean()
        self.reward = reward.rolling(window=windo).mean()
        self.lost = sum_lost

    def process_actions(self, actions: str):
        header = ""
        c = {}
        with open(actions) as f:
            for line in f:
                line = line.strip()
                if line.startswith("Epizody"):
                    if len(c) != 0:
                        self.actions[header] = c
                        c = {}
                    header = line
                else:
                    split = line.split(": ")
                    c[str(split[0])] = int(split[1])
            self.actions[header] = c


runs = [
    # Run("Round Robin", "/home/tymons/praca/runs/default"),
    Run("DQN (32x32, Tanh)", "/home/minhkeke/Code/5G-RAN/oran_lastest/oran-sc-ric/xApps/python/update/16-DQN-Tanh-64x64"),
    # Run("PPO (p32x32-v32x32, Tanh)", "/home/tymons/praca/runs/PPO-Tanh-p32x32-v32x32"),
]


if __name__ == "__main__":
    # Compute metrics
    # Plot 1: Sum Throughput
    plt.style.use("ggplot")
    plt.figure(figsize=(20, 10))
    # plt.ylim(0, 29712)
    for run in runs:
        plt.plot(run.throughput, label=run.label, alpha=0.8)
    plt.title(
        f"Sumaryczna przepustowość w czasie (średnia krocząca, okno = {windo})",
        fontsize=16,
    )
    plt.xlabel("Krok czasowy")
    plt.ylabel("Średnia sumaryczna przepustowość [bps]", fontsize=14)
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.savefig("przepustowosc.png", dpi=300, bbox_inches="tight")
    plt.show()

    # Plot 2: Jain's Fairness Index
    plt.figure(figsize=(20, 10))
    # plt.ylim(bottom=0)
    for run in runs:
        plt.plot(run.jain, label=run.label, alpha=0.8)
    plt.title(
        f"Wskaźnik sprawiedliwości Jain’a w czasie (średnia krocząca, okno = {windo})",
        fontsize=16,
    )
    plt.xlabel("Krok Czasowy")
    plt.ylabel("Średni wskaźnik Jain’a", fontsize=14)
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.savefig("jain.png", dpi=300, bbox_inches="tight")
    plt.show()

    # Plot 3: Reward
    plt.figure(figsize=(20, 10))
    # plt.ylim(bottom=0)
    for run in runs:
        plt.plot(run.reward, label=run.label, alpha=0.8)
    plt.title(f"RW = {windo})", fontsize=16)
    plt.xlabel("Krok Czasowy")
    plt.ylabel(r"Średnia nagroda = $0.7 \cdot T + 0.3 \cdot J$", fontsize=14)
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.savefig("nagroda.png", dpi=300, bbox_inches="tight")
    plt.show()

    # Plot 4: Sum Lost :(
    plt.style.use("ggplot")
    plt.figure(figsize=(20, 10))
    # plt.ylim(0, 1000)
    for run in runs:
        plt.plot(run.lost, label=run.label, alpha=0.8)
    plt.title(f"Sumaryczna liczba utraconych pakietów w czasie", fontsize=16)
    plt.xlabel("Krok czasowy")
    plt.ylabel("Liczba utraconych pakietów", fontsize=14)
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.savefig("utrata.png", dpi=300, bbox_inches="tight")
    plt.show()

    for run in runs:
        if len(run.actions) != 0:
            fig, axs = plt.subplots(2, 2, figsize=(20, 10), sharey=True)
            axs = axs.flatten()
            i = 0
            for header, counter in run.actions.items():
                axs[i].bar(
                    counter.keys(),
                    counter.values(),
                    color="steelblue",
                    edgecolor="black",
                )
                axs[i].set_title(f"{header}", fontsize=14)
                axs[i].set_xlabel("Akcje", fontsize=14)
                axs[i].set_ylabel("Częstość", fontsize=14)
                i += 1
            fig.suptitle(f"Częstość podejmowanych akcji - {run.label}", fontsize=16)
            plt.tight_layout(rect=[0, 0, 1, 0.96])
            name = run.logdir.split("/")[-1]
            plt.savefig(f"obrazki/{name}-akcje.png", dpi=300, bbox_inches="tight")
            plt.show()