from dataclasses import replace

from conftest import FakeClock, FakeMonitor, FakeNetwork, FakeProxy, WIFI, drain, make_adapter
from lan_proxy_switcher.controller import (
    AdaptersUpdated,
    Controller,
    LogLine,
    ProxyStatus,
    ScanRequested,
    Start,
    State,
    SwitchRequested,
)
from lan_proxy_switcher.monitor import MonitorError, NetworkChanged, ProxyLost, ProxyOk
from lan_proxy_switcher.network import AdapterStatus
from lan_proxy_switcher.scanner import DiscoveryResult, ScanHit

HIT = ScanHit("172.20.10.1", 7890, 12.0)
ETHERNET = make_adapter()


def build(cfg, ui, adapters=None, ip_on_enable=None, discovery=None, on_scan=None):
    clock = FakeClock()
    network = FakeNetwork(
        adapters if adapters is not None else [ETHERNET, WIFI], ip_on_enable=ip_on_enable
    )
    result = discovery if discovery is not None else DiscoveryResult((), "subnet", "命中 0 个")

    def fake_discover(adapter, ports, scan_fn, cancel, max_hosts_prefix=24):
        if on_scan is not None:
            on_scan(adapter, scan_fn, cancel)
        return result

    controller = Controller(
        replace(cfg, auto_scan=False),
        network,
        FakeProxy(),
        FakeMonitor(),
        ui,
        spawn=lambda fn: fn(),
        identity=lambda: "DESKTOP\\llooo S-1-5-21-1",
        discover=fake_discover,
        scan=lambda *args, **kwargs: None,
        clock=clock,
        sleep=clock.sleep,
    )
    return controller, network, clock


def run_all(controller, *messages):
    for message in messages:
        controller.post(message)
    controller.process_pending()


def logs(ui):
    return [event.text for event in drain(ui) if isinstance(event, LogLine)]


# ---------- 网卡切换 ----------

def test_switch_disables_others_before_enabling_the_target(cfg, ui):
    controller, network, _ = build(cfg, ui, ip_on_enable={23: ("192.168.1.5", 24)})

    run_all(controller, Start(), SwitchRequested(23))

    assert network.switches == [(32, False), (23, True)]


def test_switch_then_scans_the_newly_enabled_adapter(cfg, ui):
    seen = []
    controller, _, _ = build(
        cfg,
        ui,
        ip_on_enable={23: ("192.168.1.5", 24)},
        on_scan=lambda adapter, scan_fn, cancel: seen.append(adapter.name),
    )

    run_all(controller, Start(), SwitchRequested(23))

    assert seen == ["Wi-Fi"]


def test_switch_success_reaches_scanning_then_no_proxy(cfg, ui):
    controller, _, _ = build(cfg, ui, ip_on_enable={23: ("192.168.1.5", 24)})

    run_all(controller, Start(), SwitchRequested(23))

    assert controller.state is State.NO_PROXY  # 扫到 0 个，正常落到未使用代理


def test_switch_applies_the_proxy_found_afterwards(cfg, ui):
    controller, _, _ = build(
        cfg,
        ui,
        ip_on_enable={23: ("192.168.1.5", 24)},
        discovery=DiscoveryResult((HIT,), "gateway", "网关命中"),
    )

    run_all(controller, Start(), SwitchRequested(23))

    assert controller.state is State.PROXY_ACTIVE


def test_switch_times_out_waiting_for_an_address(cfg, ui):
    """目标网卡启用了但一直拿不到 IP（DHCP 失败 / Wi-Fi 连不上）。"""
    controller, _, clock = build(cfg, ui, ip_on_enable=None)

    run_all(controller, Start(), SwitchRequested(23))

    assert controller.state is State.NO_PROXY
    assert any("超时" in line for line in logs(ui))
    assert clock.now >= Controller.ENABLE_TIMEOUT_S


def test_timeout_message_says_which_step_got_stuck(cfg, ui):
    controller, _, _ = build(cfg, ui, ip_on_enable=None)

    run_all(controller, Start(), SwitchRequested(23))

    assert any("取得 IPv4" in line for line in logs(ui))


def test_switch_failure_is_reported_not_swallowed(cfg, ui):
    controller, network, _ = build(cfg, ui)
    network.fail_on = {32}

    run_all(controller, Start(), SwitchRequested(23))

    assert controller.state is State.NO_PROXY
    assert any("拒绝操作" in line for line in logs(ui))


def test_adapter_list_is_republished_after_a_switch(cfg, ui):
    controller, _, _ = build(cfg, ui, ip_on_enable={23: ("192.168.1.5", 24)})
    run_all(controller, Start())
    drain(ui)

    run_all(controller, SwitchRequested(23))

    updates = [event for event in drain(ui) if isinstance(event, AdaptersUpdated)]
    wifi = next(a for a in updates[-1].adapters if a.index == 23)
    assert wifi.status is AdapterStatus.UP
    assert wifi.ipv4 == "192.168.1.5"


def test_switch_to_an_unknown_index_is_reported(cfg, ui):
    controller, _, _ = build(cfg, ui)

    run_all(controller, Start(), SwitchRequested(999))

    assert any("999" in line for line in logs(ui))
    assert controller.state is State.NO_PROXY


# ---------- 网络变化 ----------

def test_network_change_triggers_a_rescan(cfg, ui):
    seen = []
    controller, _, _ = build(cfg, ui, on_scan=lambda a, s, c: seen.append(a.name))
    run_all(controller, Start())

    run_all(controller, NetworkChanged((ETHERNET,)))

    assert seen == ["Ethernet 10"]


def test_network_change_is_ignored_while_switching(cfg, ui):
    """切换过程中自己触发的网络变化不许打断自己。"""
    seen = []

    def during_switch(adapter, scan_fn, cancel):
        seen.append(adapter.name)

    controller, _, _ = build(cfg, ui, ip_on_enable={23: ("192.168.1.5", 24)}, on_scan=during_switch)
    run_all(controller, Start())
    controller.state = State.SWITCHING

    run_all(controller, NetworkChanged((ETHERNET,)))

    assert seen == []
    assert any("忽略网络变化" in line for line in logs(ui))


def test_network_change_without_a_usable_adapter_stops_at_no_proxy(cfg, ui):
    controller, network, _ = build(cfg, ui)
    run_all(controller, Start())
    network.adapters = [WIFI]

    run_all(controller, NetworkChanged((WIFI,)))

    assert controller.state is State.NO_PROXY
    assert any("等待网络恢复" in line for line in logs(ui))


def test_network_change_republishes_adapters(cfg, ui):
    controller, _, _ = build(cfg, ui)
    run_all(controller, Start())
    drain(ui)

    run_all(controller, NetworkChanged((ETHERNET,)))

    assert [event for event in drain(ui) if isinstance(event, AdaptersUpdated)]


# ---------- 代理监控事件 ----------

def test_proxy_ok_updates_the_status_line(cfg, ui):
    controller, _, _ = build(cfg, ui)
    run_all(controller, Start())
    drain(ui)

    run_all(controller, ProxyOk("172.20.10.1:7890", 12.4))

    status = [event for event in drain(ui) if isinstance(event, ProxyStatus)][-1]
    assert status.server == "172.20.10.1:7890"
    assert "12" in status.status


def test_proxy_lost_triggers_a_rescan(cfg, ui):
    seen = []
    controller, _, _ = build(cfg, ui, on_scan=lambda a, s, c: seen.append(a.name))
    run_all(controller, Start())

    run_all(controller, ProxyLost("172.20.10.1:7890", 3))

    assert seen == ["Ethernet 10"]
    assert any("判定失效" in line for line in logs(ui))


def test_proxy_lost_reports_the_failure_count(cfg, ui):
    controller, _, _ = build(cfg, ui)
    run_all(controller, Start())
    drain(ui)

    run_all(controller, ProxyLost("172.20.10.1:7890", 3))

    assert any("连续 3 次" in line for line in logs(ui))


def test_monitor_error_is_logged_without_changing_state(cfg, ui):
    controller, _, _ = build(cfg, ui)
    run_all(controller, Start(), ScanRequested())
    before = controller.state
    drain(ui)

    run_all(controller, MonitorError("网卡查询失败：PowerShell 退出码 1"))

    assert controller.state is before
    assert any("监控异常" in line for line in logs(ui))
