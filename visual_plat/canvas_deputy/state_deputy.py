import os
import pickle
import time
from enum import Enum
from dataclasses import dataclass
from copy import deepcopy

from PySide6.QtCore import QMutex

from visual_plat.render_layer.layer_base import LayerBase
from visual_plat.global_proxy.config_proxy import ConfigProxy
from visual_plat.global_proxy.async_proxy import AsyncProxy
from visual_plat.shared.utility.status_bar import StatusBar


class RecordType(Enum):
    reload = 0
    adjust = 1


@dataclass
class RecordUnit:
    layer_tag: str
    record_type: RecordType
    record_data: any


@dataclass
class Record:
    initial: list[RecordUnit]
    updates: list[list[RecordUnit]]
    comp_idx: int  # 兼容编号


class StateDeputy:
    record_path = ""

    def __init__(self, layers: dict[str, LayerBase], status_bar: StatusBar):
        super().__init__()
        self.layers = layers
        self.status_bar = status_bar
        self.record: Record = Record([], [], 0)
        self.record_len = 0
        self.pausing = False  # 播放暂停
        self.blocked = False  # 异步线程阻塞
        self.suspended = False  # 拒绝接受更新
        self.recording = False
        self.replaying = False
        self.replay_name = ""
        self.replay_index = 0
        self.replay_range = None
        self.replay_record = None
        self.replay_mutex = QMutex()
        self.comp_idx = ConfigProxy.record("compatible_index")
        StateDeputy.record_path = ConfigProxy.path("record")
        if not os.path.exists(StateDeputy.record_path):
            os.mkdir(StateDeputy.record_path)

    def block(self):
        """切换阻塞状态"""
        self.blocked = not self.blocked
        if self.blocked:
            self.status_bar.set("Blocked")
        else:
            self.status_bar.reset("Blocked")

    def append_record(self, record_type: RecordType, layer_tag: str, data, new_step: bool):
        """追加记录"""
        record = RecordUnit(layer_tag, record_type, data)
        if new_step:
            self.record.updates.append([])
            self.record_len += 1
        self.record.updates[-1].append(record)
        self.status_bar.set("Recording", str(self.record_len))

    def reload(self, layer_tag: str, data=None, new_step=True, deep_copy=True):
        """重载某个图层"""
        rcd_data = deepcopy(data) if deep_copy else data  # 深拷贝，以防出错
        if layer_tag in self.layers.keys():
            self.layers[layer_tag].on_reload(rcd_data)
            if self.recording:
                self.append_record(RecordType.reload, layer_tag, rcd_data, new_step)

    def adjust(self, layer_tag: str, data=None, new_step=True, deep_copy=True):
        """调整某个图层"""
        rcd_data = deepcopy(data) if deep_copy else data  # 深拷贝，以防出错
        if layer_tag in self.layers.keys():
            self.layers[layer_tag].on_adjust(rcd_data)
            if self.recording:
                self.append_record(RecordType.adjust, layer_tag, rcd_data, new_step)

    @staticmethod
    def load_record(path):
        """解析记录"""
        with open(path, 'rb') as rcd:
            res = pickle.load(rcd)
        return res

    @staticmethod
    def save_record(record: Record):
        """保存记录"""
        rcd_name = time.strftime(
            '%y%m%d-%H%M%S',
            time.localtime(time.time())
        ) + f"-{len(record.updates) + 1}"
        path = StateDeputy.record_path + rcd_name + ".rcd"
        with open(path, 'wb') as rcd:
            pickle.dump(record, rcd)
        print(f"Record saved as {rcd_name}.rcd")
        return path, rcd_name

    def snapshot(self):
        """截图并保存"""
        record = Record([], [], self.comp_idx)
        for tag, layer in self.layers.items():
            record.initial.append(RecordUnit(tag, RecordType.reload, layer.data))
        path, self.replay_name = self.save_record(record)
        return path, self.replay_name

    def start_record(self):
        """开始录制"""
        if not self.recording:
            self.record = Record([], [], self.comp_idx)
            self.record_len = 1
            for tag, layer in self.layers.items():
                self.record.initial.append(RecordUnit(tag, RecordType.reload, layer.data))
            self.recording = True
            self.status_bar.set("Recording")
            print("Recording started.")

    def start_replay(self, record: Record, name=""):
        """开始播放"""
        if self.replaying:
            self.terminate()
        self.replay_name = name
        self.replay_index = 0
        self.replay_range = len(record.updates)
        self.replay_record = record
        print("Replaying [%s] started." % self.replay_name)
        self.replaying = True
        self.suspended = True  # 不再接受外部更新
        self.replay_by_index()
        self.status_bar.set("Suspended")
        if record.updates:
            self.replay_index = 1
            AsyncProxy.run(self.async_replay)

    def apply_snapshot(self, record: Record):
        """应用快照"""
        for unit in record.initial:
            self.reload(unit.layer_tag, unit.record_data, False)
        print("Snapshot applied.")

    def replay_by_index(self):
        """播放某一帧"""
        if self.replaying:
            if self.replay_index == 0:
                for r in self.replay_record.initial:
                    self.reload(r.layer_tag, r.record_data, deep_copy=False)
                    self.status_bar.set(
                        "Replaying", f"[{self.replay_name}] 1/{self.replay_range + 1}"
                    )
            else:
                updates = self.replay_record.updates
                if 0 < self.replay_index <= self.replay_range:
                    self.status_bar.set(
                        "Replaying", f"[{self.replay_name}] "
                                     f"{self.replay_index + 1}/{self.replay_range + 1}"
                    )
                    for upd in updates[self.replay_index - 1]:
                        if upd.record_type == RecordType.reload:
                            self.reload(upd.layer_tag, upd.record_data, deep_copy=False)
                        elif upd.record_type == RecordType.adjust:
                            self.adjust(upd.layer_tag, upd.record_data, deep_copy=False)
                            print("Warning: Adjustment not yet well supported.")

    def async_replay(self):
        """异步播放控制"""
        self.replay_mutex.lock()  # 保证同时只有一个播放线程在运行
        while self.replaying:
            while self.pausing:
                time.sleep(0.5)
            time.sleep(1)
            self.replay_by_index()
            self.replay_index += 1
            if self.replay_index > self.replay_range:
                self.replay_index = 0
        self.replay_mutex.unlock()

    def fast_forward(self):
        """快进"""
        if self.replaying and self.pausing:
            if self.replay_index + 1 <= self.replay_range:
                self.replay_index += 1
            else:
                self.replay_index = 0
            self.replay_by_index()

    def back_forward(self):
        """倒退"""''
        if self.replaying and self.pausing:
            if self.replay_index - 1 >= 0:
                self.replay_index -= 1
            else:
                self.replay_index = self.replay_range
            self.replay_by_index()

    def pause(self):
        """暂停"""
        if self.replaying:
            self.pausing = not self.pausing
            if self.pausing:
                self.status_bar.set("Paused")
            else:
                self.status_bar.reset("Paused")
        else:
            self.suspended = not self.suspended
            if self.suspended:
                self.status_bar.set("Suspended")
            else:
                self.status_bar.reset("Suspended")

    def stop_record(self):
        """停止录制"""
        if self.recording:
            self.recording = False
            self.status_bar.reset("Recording")
            self.save_record(self.record)
            print("Recording terminated.")

    def stop_replay(self):
        """停止播放"""
        if self.replaying:
            self.replaying = False
            self.suspended = False
            self.status_bar.reset("Replaying")
            self.status_bar.reset("Suspended")
            self.replay_index = self.replay_range = 0
            print("Replaying [%s] terminated." % self.replay_name)

    def terminate(self):
        """终止（优先终止播放，再次触发会终止录制）"""
        if self.replaying:
            self.stop_replay()
        elif self.recording:
            self.stop_record()
