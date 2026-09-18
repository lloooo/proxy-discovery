import queue
import time

from lan_proxy_switcher.monitor import (
    MonitorError,
    MonitorState,
    MonitorThread,
    NetworkChanged,
    ProxyLost,
    ProxyOk,
)
from lan_proxy_switcher.network import Adapter, AdapterKind, AdapterStatus
from lan_proxy_switcher.scanner import ScanHit


def adapter(name="Wi-Fi", ipv4=None, status=AdapterStatus.DISCONNECTED):
    return Adapter(name, 23, "desc", AdapterKind.WIRELESS, status, ipv4, 24 if ipv4 else None, None, None)


def hit_probe(latency=12.0):
    return lambda ip, port, timeout_s: (ScanHit(ip, port, latency), None)


def fail_probe(ip, port, timeout_s):
    return None, 10061


def build(list_adapters=lambda: [], probe=fail_probe, **kwargs):
    options = dict(
        monitor_interval_s=30,
        scan_timeout_ms=500,
        proxy_check_failures=3,
        network_poll_interval_s=5.0,
    )
    options.update(kwargs)
    return MonitorState(list_adapters=list_adapters, probe=probe, **options)


# ---------- 网络变化去抖动 ----------

def test_first_poll_only_establishes_a_baseline():
    state = build(list_adapters=lambda: [adapter()])

    assert state.tick(0.0) == []


def test_change_is_reported_after_two_identical_polls():
    state = build(list_adapters=lambda: [adapter()])

    state.tick(0.0)
    events = state.tick(5.0)

    assert events == [NetworkChanged((adapter(),))]


def test_stable_network_is_reported_only_once():
    state = build(list_adapters=lambda: [adapter()])

    state.tick(0.0)
    state.tick(5.0)

    assert state.tick(10.0) == []


def test_mid_flight_change_is_not_reported_until_it_settles():
    """Wi-Fi 重连过程中会连抛好几次变化，不能每次都触发重扫。"""
    current = [adapter()]
    state = build(list_adapters=lambda: list(current))
    state.tick(0.0)
    state.tick(5.0)  # 基线已上报

    current[:] = [adapter(ipv4="192.168.1.5", status=AdapterStatus.UP)]
    assert state.tick(10.0) == []  # 变了，但还不稳定

    events = state.tick(15.0)
    assert events == [NetworkChanged((adapter(ipv4="192.168.1.5", status=AdapterStatus.UP),))]


def test_network_is_not_polled_more_often_than_the_interval():
    calls = []
    state = build(list_adapters=lambda: calls.append(1) or [adapter()])

    state.tick(0.0)
    state.tick(1.0)
    state.tick(2.0)

    assert len(calls) == 1


def test_adapter_query_failure_becomes_an_error_event():
    def boom():
        raise RuntimeError("PowerShell 挂了")

    state = build(list_adapters=boom)

    events = state.tick(0.0)

    assert len(events) == 1
    assert isinstance(events[0], MonitorError)
    assert "PowerShell 挂了" in events[0].message


# ---------- 代理探测 ----------

def test_no_proxy_means_no_probing():
    calls = []
    state = build(probe=lambda *args: calls.append(args) or (None, 10061), network_poll_interval_s=10_000.0)

    state.tick(0.0)
    state.tick(30.0)
    state.tick(60.0)

    assert calls == []


def test_successful_check_emits_proxy_ok():
    state = build(probe=hit_probe(12.5), network_poll_interval_s=10_000.0)
    state.set_proxy("172.20.10.1:7890")

    state.tick(0.0)
    events = state.tick(30.0)

    assert events == [ProxyOk("172.20.10.1:7890", 12.5)]


def test_probe_receives_ip_port_and_timeout_in_seconds():
    calls = []

    def recording_probe(ip, port, timeout_s):
        calls.append((ip, port, timeout_s))
        return ScanHit(ip, port, 1.0), None

    state = build(probe=recording_probe, scan_timeout_ms=500, network_poll_interval_s=10_000.0)
    state.set_proxy("172.20.10.1:7890")
    state.tick(0.0)
    state.tick(30.0)

    assert calls == [("172.20.10.1", 7890, 0.5)]


def test_proxy_lost_only_after_the_configured_number_of_failures():
    state = build(probe=fail_probe, proxy_check_failures=3, network_poll_interval_s=10_000.0)
    state.set_proxy("10.0.0.20:7890")
    state.tick(0.0)

    assert state.tick(30.0) == []
    assert state.tick(60.0) == []
    assert state.tick(90.0) == [ProxyLost("10.0.0.20:7890", 3)]


def test_a_success_resets_the_failure_counter():
    outcomes = [(None, 10061), (None, 10061), (ScanHit("10.0.0.20", 7890, 9.0), None), (None, 10061)]
    state = build(probe=lambda *args: outcomes.pop(0), proxy_check_failures=3, network_poll_interval_s=10_000.0)
    state.set_proxy("10.0.0.20:7890")
    state.tick(0.0)

    state.tick(30.0)
    state.tick(60.0)
    state.tick(90.0)

    assert state.tick(120.0) == []  # 计数已清零，这才是第 1 次失败


def test_changing_the_proxy_resets_the_failure_counter():
    state = build(probe=fail_probe, proxy_check_failures=2, network_poll_interval_s=10_000.0)
    state.set_proxy("10.0.0.20:7890")
    state.tick(0.0)
    state.tick(30.0)

    state.set_proxy("10.0.0.30:7890")
    state.tick(60.0)

    assert state.tick(90.0) == []


def test_both_timers_can_fire_in_one_tick():
    """一次 tick 可能同时产出网络变化与代理探测两类事件，顺序为网络在前。"""
    state = build(
        list_adapters=lambda: [adapter()],
        probe=hit_probe(12.5),
        network_poll_interval_s=5.0,
    )
    state.set_proxy("172.20.10.1:7890")
    state.tick(0.0)  # 建立基线，不上报

    events = state.tick(30.0)

    assert events == [
        NetworkChanged((adapter(),)),
        ProxyOk("172.20.10.1:7890", 12.5),
    ]


# ---------- 线程外壳 ----------

def test_thread_pumps_events_into_the_inbox_and_stops():
    inbox = queue.Queue()
    state = build(list_adapters=lambda: [adapter()], network_poll_interval_s=0.0)
    thread = MonitorThread(state, inbox, tick_interval=0.01)

    thread.start()
    event = inbox.get(timeout=2.0)
    thread.stop()

    assert isinstance(event, NetworkChanged)
    assert not thread.is_alive()


def test_thread_survives_a_tick_exception():
    inbox = queue.Queue()

    class Exploding:
        def tick(self, now):
            raise RuntimeError("tick 炸了")

    thread = MonitorThread(Exploding(), inbox, tick_interval=0.01)
    thread.start()
    event = inbox.get(timeout=2.0)
    thread.stop()

    assert isinstance(event, MonitorError)
    assert "tick 炸了" in event.message
