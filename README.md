 # 5G testbed ANSA LAB

 This repository provides the instruction to deploy a 5G architechture using Open5GS, srsRAN_Project and ORAN SC RIC.

 ## Quick start
 ### 1. Build Preparation

 To deploy 5G Core and RAN, please run the following command to install dependencies:

 ```bash
chmod +x setup.sh

./setup.sh
```

1.1. Install ZeroMQ

```bash
git clone https://github.com/zeromq/czmq.git
cd czmq
./autogen.sh
./configure 
make
sudo make install
sudo ldconfig
```
1.2. GNU-Radio Companion
This will allow connect multiple UEs, Please install GNU-Radio Companion following the instructions here:

```bash
sudo apt-get install gnuradio
```

### 2. 5G RAN

We set up an end-to-end 5G network using the **srsRAN_Project gNB** [[docs](https://docs.srsran.com/projects/project/en/latest/),[code](https://github.com/srsran/srsRAN_Project/)] (that is equipped with an E2 agent) and **srsUE** from **srsRAN-4g** project [[docs](https://docs.srsran.com/projects/4g/en/latest/),[code](https://github.com/srsran/srsRAN_4G)]. Please follow the official installation guidelines and remember to compile both projects with **ZeroMQ** support.

We follow this [application note](https://docs.srsran.com/projects/project/en/latest/tutorials/source/near-rt-ric/source/). To this end, we execute gNB and srsUE with the configs provided in the `./e2-agents/srsRAN` directory (gNB config differs only with the IP address of the RIC compared to the config from the tutorial). Note that, we use ZMQ-based RF devices for emulation of the wireless transmission between gNB and UE, therefore the entire RAN setup can be run on a single host machine.

Use this command to install srsRAN_Project (include Open5gs):

```bash
git clone https://github.com/srsran/srsRAN_Project.git
cd srsRAN_Project
mkdir build
cd build
cmake ../ -DENABLE_EXPORT=ON -DENABLE_ZEROMQ=ON -DAUTO_DETECT_ISA=OFF
make -j`nproc` 
```

<!-- **Note :** In the case that have error with `make`, we change the version of `gcc` and `g++`:

 ```bash
sudo apt install build-essential
sudo apt -y install gcc-10 g++-10
sudo update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-10 10
sudo update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-10 10
sudo update-alternatives --config gcc
sudo update-alternatives --config g++  
``` -->

### 3. srsRAN_4G

We use **srsUE** from **srsRAN-4g** project [[docs](https://docs.srsran.com/projects/4g/en/latest/),[code](https://github.com/srsran/srsRAN_4G)].

```bash
git clone https://github.com/srsRAN/srsRAN_4G.git
cd srsRAN_4G
mkdir build
cd build
cmake ../
```

### 4. ORAN SC RIC

```bash
git clone https://github.com/srsran/oran-sc-ric 
```

## Configuration

Configuration file for gnb, UEs, and GNU-Radio flow graph are stored in `config` 

The configuration file for gnb must be in `srsRAN_Project/build/apps/gnb/`, for UE: `srsRAN_4G/build/srsue/src`

## Applying network slicing:

To apply network slicing, you have to change some file:

1. `srsRAN_Project/docker/open5gs/open5gs-5gc.yml`

```yaml
amf:
...
 plmn_support:
 - plmn_id:
 mcc: 001
 mnc: 01
 s_nssai:
 - sst: 1
 sd: 000001
 - sst: 1
 sd: 000002
 - sst: 1
 sd: 000003 
```

2. `czmq/srsRAN_4G/srsue/src/stack/upper/nas_5g.cc`
Comment these line:

```yaml
...
# if (cfg.enable_slicing) {
# reg_req.requested_nssai_present = true;
# s_nssai_t nssai;
# set_nssai(nssai);
# reg_req.requested_nssai.s_nssai_list.push_back(nssai);
# }
... 
```

3. Move 3 files in `open5gs` to `srsRAN_Project/docker/open5gs`

## Running the network

1. Start Open5gs

```bash
docker compose up --build 5gc
```

After that, you should check the information of each UE on  http://localhost:9999/

2. Start Oran-sc-ric:

```bash
cd ./oran-sc-ric
docker compose up
```

3. Start gnb

```bash
cd  ./srsRAN_Project/build/apps/gnb/
sudo ./gnb -c gnb_zmq.yaml 
```

The gNB should connect to both the core network and the RIC.  
**Note:** The RIC uses 60s time-to-wait. Therefore, after disconnecting from RIC, an E2 agent (inside gNB) has to wait 60s before trying to connect again. Otherwise, the RIC sends an `E2 SETUP FAILURE` message and gNB is not connected to the RIC.

4. Start UEs

```bash
cd ./srsRAN_4G/build/srsue/src
sudo ip netns add ue1 
sudo ip netns add ue2 
sudo ip netns add ue3

sudo ./srsue ./ue1_zmq.conf
sudo ./srsue ./ue2_zmq.conf
sudo ./srsue ./ue3_zmq.conf
```
