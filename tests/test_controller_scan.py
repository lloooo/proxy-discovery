import threading
from dataclasses import replace

import pytest

from conftest import FakeMonitor, FakeNetwork, FakeProxy, WIFI, drain, make_adapter
from lan_proxy_switcher.controller import (
    AdaptersUpdated,
    Controller,
    DisableProxyRequested,
    LogLine,
    ProxyStatus,
    RefreshAdaptersRequested,
    ScanHitFound,
    ScanRequested,
    ScanResults,
    Shutdown,
    Start,
    State,
    StateChanged,
    UseHitRequested,
)
from lan_proxy_switcher.proxy import ProxyState
from lan_proxy_switcher.scanner import DiscoveryResult, ScanHit


def build(cfg, ui, adapters=None, discovery=None, proxy=None, on_scan=None):
    """装一个全 Fake 的控制器；spawn 同步执行，扫描结果预设。"""
    network = FakeNetwork(adapters if adapters is not None else [make_adapter(), WIFI])
    result = discovery if discovery is not None else DiscoveryResult((), "subnet", "命中 0 个")

    def fake_discover(adapter, ports, scan_fn, cancel, max_hosts_prefix=24):
        if on_scan is not None:
            on_scan(adapter, scan_fn, cancel)
        return result

    controller = Controller(
        cfg,
        network,
        proxy or FakeProxy(),
        FakeMonitor(),
        ui,
        spawn=lambda fn: fn(),
        identity=lambda: "DESKTOP\\llooo S-1-5-21-1",
        discover=fake_discover,
        scan=lambda *args, **kwargs: None,
    )
    return controller, network


def run_all(controller, *messages):
    for message in messages:
        controller.post(message)
    controller.process_pending()


HIT_7890 = ScanHit("172.20.10.1", 7890, 12.0)
HIT_1082 = ScanHit("172.20.10.1", 1082, 15.0)


# ---------- 启动 ----------

def test_start_logs_the_running_account(cfg, ui):
    controller, _ = build(cfg, ui)

    run_all(controller, Start())

    logs = [e.text for e in drain(ui) if isinstance(e, LogLine)]
    assert any("S-1-5-21-1" in line for line in logs)


def test_start_snapshots_the_existing_proxy(cfg, ui):
    before = ProxyState(True, "1.2.3.4:8080", "<local>", None)
    controller, _ = build(cfg, ui, proxy=FakeProxy(before))

    run_all(controller, Start())

    assert controller.startup_proxy == before


def test_start_publishes_the_adapter_list(cfg, ui):
    controller, _ = build(cfg, ui)

    run_all(controller, Start())

    updates = [e for e in drain(ui) if isinstance(e, AdaptersUpdated)]
    assert updates
    assert [a.name for a in updates[0].adapters] == ["Ethernet 10", "Wi-Fi"]
    assert updates[0].active_index == 32


def test_auto_scan_false_goes_straight_to_no_proxy(cfg, ui):
    controller, _ = build(replace(cfg, auto_scan=False), ui)

    run_all(controller, Start())

    assert controller.state is State.NO_PROXY


# ---------- 扫描 → 设代理 ----------

def test_a_hit_is_applied_as_the_system_proxy(cfg, ui):
    proxy = FakeProxy()
    controller, _ = build(
        cfg, ui, proxy=proxy, discovery=DiscoveryResult((HIT_7890,), "gateway", "网关命中")
    )

    run_all(controller, Start())

    assert proxy.applied == ["172.20.10.1:7890"]
    assert controller.state is State.PROXY_ACTIVE


def test_prefer_port_decides_which_hit_is_applied(cfg, ui):
    proxy = FakeProxy()
    controller, _ = build(
        cfg, ui, proxy=proxy, discovery=DiscoveryResult((HIT_1082, HIT_7890), "subnet", "命中 2 个")
    )

    run_all(controller, Start())

    assert proxy.applied == ["172.20.10.1:7890"]


def test_monitor_is_told_about_the_new_proxy(cfg, ui):
    controller, _ = build(cfg, ui, discovery=DiscoveryResult((HIT_7890,), "gateway", "网关命中"))

    run_all(controller, Start())

    assert controller.monitor.servers == ["172.20.10.1:7890"]


def test_scan_results_reach_the_ui(cfg, ui):
    controller, _ = build(
        cfg, ui, discovery=DiscoveryResult((HIT_7890, HIT_1082), "subnet", "命中 2 个")
    )

    run_all(controller, Start())

    results = [e for e in drain(ui) if isinstance(e, ScanResults)]
    assert results[-1].hits == (HIT_7890, HIT_1082)


def test_hits_are_streamed_while_scanning(cfg, ui):
    """命中要即时推给 GUI，不能等整轮扫完。"""

    def emit_hit(adapter, scan_fn, cancel):
        scan_fn([(HIT_7890.ip, HIT_7890.port)])

    controller, _ = build(
        cfg,
        ui,
        discovery=DiscoveryResult((HIT_7890,), "subnet", "命中 1 个"),
        on_scan=emit_hit,
    )
    # scan_fn 内部会调用注入的 scan；这里换成会回调 on_hit 的假实现
    controller._scan = lambda targets, timeout, conc, cancel, on_hit=None: (
        on_hit(HIT_7890) if on_hit else None
    )

    run_all(controller, Start())

    assert [e.hit for e in drain(ui) if isinstance(e, ScanHitFound)] == [HIT_7890]


def test_no_hit_leaves_proxy_untouched_by_default(cfg, ui):
    proxy = FakeProxy()
    controller, _ = build(cfg, ui, proxy=proxy)

    run_all(controller, Start())

    assert proxy.applied == []
    assert proxy.disabled == 0
    assert controller.state is State.NO_PROXY


def test_disable_proxy_when_unavailable_turns_it_off(cfg, ui):
    proxy = FakeProxy(ProxyState(True, "1.2.3.4:80", None, None))
    controller, _ = build(replace(cfg, disable_proxy_when_unavailable=True), ui, proxy=proxy)

    run_all(controller, Start())

    assert proxy.disabled == 1
    assert controller.state is State.NO_PROXY


def test_auto_set_proxy_false_lists_results_without_applying(cfg, ui):
    proxy = FakeProxy()
    controller, _ = build(
        replace(cfg, auto_set_proxy=False),
        ui,
        proxy=proxy,
        discovery=DiscoveryResult((HIT_7890,), "gateway", "网关命中"),
    )

    run_all(controller, Start())

    assert proxy.applied == []
    assert controller.state is State.NO_PROXY
    assert [e for e in drain(ui) if isinstance(e, ScanResults)][-1].hits == (HIT_7890,)


def test_scan_of_a_specific_adapter_overrides_auto_selection(cfg, ui):
    seen = []
    controller, _ = build(
        replace(cfg, auto_scan=False), ui, on_scan=lambda a, s, c: seen.append(a.name)
    )

    run_all(controller, Start(), ScanRequested(adapter_index=23))

    assert seen == ["Wi-Fi"]


def test_scan_without_any_usable_adapter_reports_it(cfg, ui):
    controller, _ = build(replace(cfg, auto_scan=False), ui, adapters=[WIFI])

    run_all(controller, Start(), ScanRequested())

    logs = [e.text for e in drain(ui) if isinstance(e, LogLine)]
    assert any("没有可用于扫描的网卡" in line for line in logs)
    assert controller.state is State.NO_PROXY


def test_stale_scan_results_are_discarded(cfg, ui):
    """网卡切换/网络变化会掐掉在途扫描，晚到的结果不许覆盖新状态。"""
    from lan_proxy_switcher.controller import ScanFinished

    proxy = FakeProxy()
    controller, _ = build(replace(cfg, auto_scan=False), ui, proxy=proxy)
    stale = ScanFinished(
        DiscoveryResult((HIT_7890,), "subnet", "过期结果"), make_adapter(), object()
    )

    run_all(controller, Start(), stale)

    assert proxy.applied == []


def test_scan_state_is_published(cfg, ui):
    controller, _ = build(cfg, ui, discovery=DiscoveryResult((HIT_7890,), "gateway", "网关命中"))

    run_all(controller, Start())

    states = [e.state for e in drain(ui) if isinstance(e, StateChanged)]
    assert State.SCANNING in states
    assert states[-1] is State.PROXY_ACTIVE


# ---------- 手动操作 ----------

def test_use_hit_applies_the_chosen_result(cfg, ui):
    proxy = FakeProxy()
    controller, _ = build(replace(cfg, auto_scan=False), ui, proxy=proxy)

    run_all(controller, Start(), UseHitRequested(HIT_1082))

    assert proxy.applied == ["172.20.10.1:1082"]
    assert controller.state is State.PROXY_ACTIVE


def test_manual_pick_is_not_overwritten_by_an_in_flight_scan(cfg, ui):
    """用户手动选定代理后，在途扫描的结果不许在背后把它覆盖掉。"""
    proxy = FakeProxy()
    controller, _ = build(
        cfg, ui, proxy=proxy, discovery=DiscoveryResult((HIT_7890,), "gateway", "网关命中")
    )

    run_all(controller, Start(), UseHitRequested(HIT_1082))

    assert proxy.applied == ["172.20.10.1:1082"]
    assert controller.state is State.PROXY_ACTIVE


def test_disable_proxy_request_turns_it_off(cfg, ui):
    proxy = FakeProxy()
    controller, _ = build(
        cfg, ui, proxy=proxy, discovery=DiscoveryResult((HIT_7890,), "gateway", "网关命中")
    )

    run_all(controller, Start(), DisableProxyRequested())

    assert proxy.disabled == 1
    assert controller.state is State.NO_PROXY
    assert controller.monitor.servers[-1] is None
    assert [e for e in drain(ui) if isinstance(e, ProxyStatus)][-1].server is None


def test_refresh_republishes_the_adapter_list(cfg, ui):
    controller, network = build(replace(cfg, auto_scan=False), ui)
    run_all(controller, Start())
    drain(ui)
    network.adapters = [WIFI]

    run_all(controller, RefreshAdaptersRequested())

    updates = [e for e in drain(ui) if isinstance(e, AdaptersUpdated)]
    assert [a.name for a in updates[-1].adapters] == ["Wi-Fi"]


# ---------- 异常与退出 ----------

def test_a_failing_handler_logs_and_falls_back_to_no_proxy(cfg, ui):
    class ExplodingProxy(FakeProxy):
        def apply(self, server):
            raise RuntimeError("拒绝访问注册表")

    controller, _ = build(replace(cfg, auto_scan=False), ui, proxy=ExplodingProxy())

    run_all(controller, Start(), UseHitRequested(HIT_7890))

    logs = [e.text for e in drain(ui) if isinstance(e, LogLine)]
    assert any("拒绝访问注册表" in line for line in logs)
    assert controller.state is State.NO_PROXY


def test_shutdown_restores_the_startup_proxy_when_configured(cfg, ui):
    before = ProxyState(True, "1.2.3.4:8080", None, None)
    proxy = FakeProxy(before)
    controller, _ = build(replace(cfg, restore_proxy_on_exit=True), ui, proxy=proxy)

    run_all(controller, Start(), Shutdown())

    assert proxy.restored == [before]


def test_shutdown_leaves_the_proxy_alone_by_default(cfg, ui):
    proxy = FakeProxy(ProxyState(True, "1.2.3.4:8080", None, None))
    controller, _ = build(cfg, ui, proxy=proxy)

    run_all(controller, Start(), Shutdown())

    assert proxy.restored == []
