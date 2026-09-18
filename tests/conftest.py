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
    def __init__(self, adapters):
        self.adapters = list(adapters)
        self.switches = []
        self.fail_on = set()

    def list_adapters(self):
        return list(self.adapters)

    def set_adapter_enabled(self, index, enabled):
        if index in self.fail_on:
            raise RuntimeError(f"网卡 {index} 拒绝操作")
        self.switches.append((index, enabled))
        self.adapters = [
            replace(a, status=AdapterStatus.UP if enabled else AdapterStatus.DISABLED)
            if a.index == index
            else a
            for a in self.adapters
        ]


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
