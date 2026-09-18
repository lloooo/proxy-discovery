import queue
from dataclasses import replace

import pytest

from lan_proxy_switcher.config import Config
from lan_proxy_switcher.network import Adapter, AdapterKind, AdapterStatus
from lan_proxy_switcher.proxy import ProxyState


def make_adapter(
    name="Ethernet 10",
    index=32,
    kind=AdapterKind.WIRED,
    status=AdapterStatus.UP,
    ipv4="172.20.10.7",
    prefix_length=28,
    gateway="172.20.10.1",
    metric=25,
):
    return Adapter(name, index, f"{name} 描述", kind, status, ipv4, prefix_length, gateway, metric)


WIFI = make_adapter(
    name="Wi-Fi",
    index=23,
    kind=AdapterKind.WIRELESS,
    status=AdapterStatus.DISCONNECTED,
    ipv4=None,
    prefix_length=None,
    gateway=None,
    metric=None,
)


class FakeNetwork:
    def __init__(self, adapters, ip_on_enable=None):
        self.adapters = list(adapters)
        self.switches = []
        self.fail_on = set()
        # 启用某张网卡后它应当拿到的 (ipv4, prefix_length)；不给就保持原样
        self.ip_on_enable = ip_on_enable or {}

    def list_adapters(self):
        return list(self.adapters)

    def set_adapter_enabled(self, index, enabled):
        if index in self.fail_on:
            raise RuntimeError(f"网卡 {index} 拒绝操作")
        self.switches.append((index, enabled))
        updated = []
        for adapter in self.adapters:
            if adapter.index != index:
                updated.append(adapter)
                continue
            if enabled:
                ipv4, prefix = self.ip_on_enable.get(index, (adapter.ipv4, adapter.prefix_length))
                updated.append(
                    replace(adapter, status=AdapterStatus.UP, ipv4=ipv4, prefix_length=prefix)
                )
            else:
                updated.append(
                    replace(adapter, status=AdapterStatus.DISABLED, ipv4=None, prefix_length=None)
                )
        self.adapters = updated


class FakeProxy:
    def __init__(self, state=None):
        self.state = state or ProxyState(False, None, None, None)
        self.applied = []
        self.disabled = 0
        self.restored = []

    def read(self):
        return self.state

    def apply(self, server):
        self.applied.append(server)
        self.state = ProxyState(True, server, self.state.override, None)

    def disable(self):
        self.disabled += 1
        self.state = replace(self.state, enable=False)

    def restore(self, state):
        self.restored.append(state)
        self.state = state


class FakeMonitor:
    def __init__(self):
        self.servers = []

    def set_proxy(self, server):
        self.servers.append(server)


@pytest.fixture
def cfg():
    return Config()


@pytest.fixture
def ui():
    return queue.Queue()


def drain(ui_queue):
    events = []
    while not ui_queue.empty():
        events.append(ui_queue.get_nowait())
    return events


def events_of(ui_queue, kind):
    return [event for event in drain(ui_queue) if isinstance(event, kind)]


class FakeClock:
    """可控时钟：sleep 直接推进时间，等待循环因而瞬间收敛。"""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
