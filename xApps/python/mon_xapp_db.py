import argparse
import signal
from datetime import datetime
from lib.xAppBase import xAppBase
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

class MyXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port):
        super(MyXapp, self).__init__(config, http_server_port, rmr_port)

        # Cấu hình InfluxDB
        self.influx = InfluxDBClient(
            url="http://influxdb:8086",
            token="605bc59413b7d5457d181ccf20f9fda15693f81b068d70396cc183081b264f3b",
            org="srs"
        )
        self.bucket = "srsran"
        self.write_api = self.influx.write_api(write_options=SYNCHRONOUS)

        self.subscribed_ue_ids = set()

    def write_point(self, measurement, tags, fields):
        point = Point(measurement).time(datetime.utcnow())
        for k, v in tags.items():
            point = point.tag(k, str(v))
        for k, v in fields.items():
            try:
                # Nếu là list thì lấy phần tử đầu tiên, nếu rỗng thì bỏ qua
                if isinstance(v, list):
                    v = v[0] if v else 0
                # Nếu là None thì bỏ qua
                if v is None:
                    continue
                point = point.field(k, float(v))
            except Exception as e:
                print(f"[InfluxDB] Field error: key={k}, value={v}, error={e}")
        try:
            self.write_api.write(bucket=self.bucket, record=point)
        except Exception as e:
            print(f"[InfluxDB] Write error: {e}")

    def prb_callback(self, e2_node_id, *_):
        meas_data = self.e2sm_kpm.extract_meas_data(_[-1])
        if "measData" in meas_data:
            data = meas_data["measData"]

            used_dl = data.get("RRU.PrbUsedDl", [0])
            avail_dl = data.get("RRU.PrbAvailDl", [0])
            used_ul = data.get("RRU.PrbUsedUl", [0])
            avail_ul = data.get("RRU.PrbAvailUl", [0])
            cqi = data.get("CQI", [0])
            rsrp = data.get("RSRP", [0])
            rsrq = data.get("RSRQ", [0])

            # Tổng hợp lại thành 2 trường
            total_dl = (used_dl[0] if used_dl else 0) + (avail_dl[0] if used_ul else 0)
            total_ul = (used_ul[0] if avail_dl else 0) + (avail_ul[0] if avail_ul else 0)

            fields = {
                "PRB_TotalDL": total_dl,
                "PRB_TotalUL": total_ul,
                "CQI": cqi[0] if cqi else 0,
                "RSRP": rsrp[0] if rsrp else 0,
                "RSRQ": rsrq[0] if rsrq else 0
            }

            self.write_point("prb_metrics", {"e2_node": e2_node_id}, fields)

    def ue_discovery_callback(self, e2_node_id, *_):
        meas_data = self.e2sm_kpm.extract_meas_data(_[-1])
        if "ueMeasData" not in meas_data:
            return
        ue_ids = set(map(int, meas_data["ueMeasData"].keys()))
        new_ues = ue_ids - self.subscribed_ue_ids
        if new_ues:
            self.subscribed_ue_ids.update(new_ues)
            self.subscribe_style_5(e2_node_id, list(self.subscribed_ue_ids))

    def style_5_callback(self, e2_node_id, *_):
        meas_data = self.e2sm_kpm.extract_meas_data(_[-1])
        if "ueMeasData" in meas_data:
            ue_count = len(meas_data["ueMeasData"])  # 👈 Đếm số lượng UE

            # Ghi số lượng UE vào InfluxDB
            self.write_point("ue_metrics_summary", {"e2_node": e2_node_id}, {"UE_Count": ue_count})

            for ue_id, ue_data in meas_data["ueMeasData"].items():
                metrics = ue_data.get("measData", {})

                metrics.setdefault("DRB.UEThpDl", 0)
                metrics.setdefault("DRB.UEThpUl", 0)
                metrics.setdefault("DRB.RlcSduDelayDl", 0)
                metrics.setdefault("DRB.RlcDelayUl", 0)
                metrics.setdefault("DRB.AirIfDelayUl", 0)
                metrics.setdefault("DRB.RlcPacketDropRateDl", 0)
                metrics.setdefault("RRU.PrbTotDl", 0)
                metrics.setdefault("RRU.PrbTotUl", 0)
                metrics.setdefault("DRB.RlcSduTransmittedVolumeUL", 0)
                metrics.setdefault("DRB.RlcSduTransmittedVolumeDL", 0)

                fields = {
                    "DRB_UEThpDl": metrics["DRB.UEThpDl"],
                    "DRB_UEThpUl": metrics["DRB.UEThpUl"],
                    "RlcSduDelayDl": metrics["DRB.RlcSduDelayDl"],
                    "RlcDelayUl": metrics["DRB.RlcDelayUl"],
                    "AirIfDelayUl": metrics["DRB.AirIfDelayUl"],
                    "RlcPacketDropRateDl": metrics["DRB.RlcPacketDropRateDl"],
                    "PRB_TotDl": metrics["RRU.PrbTotDl"],        
                    "PRB_TotUl": metrics["RRU.PrbTotUl"],
                    "RlcSduTransmittedVolumeUL": metrics["DRB.RlcSduTransmittedVolumeUL"],
                    "RlcSduTransmittedVolumeDL": metrics["DRB.RlcSduTransmittedVolumeDL"]
                }
                print("fields:", fields)
                self.write_point("ue_metrics", {"e2_node": e2_node_id, "ue_id": ue_id}, fields)


    def subscribe_style_5(self, e2_node_id, ue_ids):
        metrics = [
            "DRB.UEThpDl", "DRB.UEThpUl", "DRB.RlcSduDelayDl",
            "DRB.RlcDelayUl", "DRB.AirIfDelayUl", "DRB.RlcPacketDropRateDl",
            "RRU.PrbTotDl", "RRU.PrbTotUl", "DRB.RlcSduTransmittedVolumeUL", "DRB.RlcSduTransmittedVolumeDL"
        ]
        self.e2sm_kpm.subscribe_report_service_style_5(
            e2_node_id, 1000, ue_ids, metrics, 1000, self.style_5_callback
        )

    @xAppBase.start_function
    def start(self, e2_node_id, *_):
        prb_metrics = ["RRU.PrbUsedDl", "RRU.PrbAvailDl", "RRU.PrbUsedUl", "RRU.PrbAvailUl","CQI", "RSRP", "RSRQ"]
        self.e2sm_kpm.subscribe_report_service_style_1(
            e2_node_id, 1000, prb_metrics, 1000, self.prb_callback)

        discovery_metric = ["DRB.UEThpDl"]
        matching_conds = [{
            'matchingCondChoice': (
                'testCondInfo',
                {'testType': ('ul-rSRP', 'true'), 'testExpr': 'lessthan', 'testValue': ('valueInt', 1000)}
            )
        }]
        self.e2sm_kpm.subscribe_report_service_style_3(
            e2_node_id, 1000, matching_conds, discovery_metric, 1000, self.ue_discovery_callback)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, default="")
    parser.add_argument("--http_server_port", type=int, default=8080)
    parser.add_argument("--rmr_port", type=int, default=4560)
    parser.add_argument("--e2_node_id", type=str, default="gnbd_001_001_00019b_0")
    parser.add_argument("--ran_func_id", type=int, default=2)

    args = parser.parse_args()
    xapp = MyXapp(args.config, args.http_server_port, args.rmr_port)
    xapp.e2sm_kpm.set_ran_func_id(args.ran_func_id)

    signal.signal(signal.SIGINT, xapp.signal_handler)
    signal.signal(signal.SIGTERM, xapp.signal_handler)

    xapp.start(args.e2_node_id)
