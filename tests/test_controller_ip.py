"""静态 / 动态 IP：控制器侧的编排、档位记忆与失败处理。"""

from dataclasses import replace

from conftest import FakeClock, FakeMonitor, FakeNetwork, FakeProxy, WIFI, drain, make_adapter
from lan_proxy_switcher.controller import (
    Controller,
    LogLine,
    SetDhcpRequested,
    SetStaticIpRequested,
    Start,
    StaticIpProfilesUpdated,
    State,
)
from lan_proxy_switcher.network import StaticIpProfile
from lan_proxy_switcher.scanner import DiscoveryResult

ETHERNET = make_adapter()
PROFILE = StaticIpProfile("192.168.1.50", 24, "192.168.1.1", ("8.8.8.8",))


def build(cfg, ui, adapters=None, ip_on_dhcp=None, on_scan=None):
    clock = FakeClock()
    network = FakeNetwork(
        adapters if adapters is not None else [ETHERNET, WIFI], ip_on_dhcp=ip_on_dhcp
    )
    saved = []

    def fake_discover(adapter, ports, scan_fn, cancel, max_hosts_prefix=24):
        if on_scan is not None:
            on_scan(adapter)
        return DiscoveryResult((), "subnet", "命中 0 个")

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
        save_config=saved.append,
    )
    return controller, network, saved


def run_all(controller, *messages):
    for message in messages:
        controller.post(message)
    controller.process_pending()


def logs(ui):
    return [event.text for event in drain(ui) if isinstance(event, LogLine)]


# ---------- 静态 IP ----------

def test_static_ip_is_applied_to_the_selected_adapter(cfg, ui):
    controller, network, _ = build(cfg, ui)

    run_all(controller, Start(), SetStaticIpRequested(32, PROFILE))

    assert network.ip_calls == [("static", 32, PROFILE)]


def test_static_ip_leaves_other_adapters_alone(cfg, ui):
    controller, network, _ = build(cfg, ui)

    run_all(controller, Start(), SetStaticIpRequested(32, PROFILE))

    assert network.switches == []


def test_static_ip_profile_is_remembered_under_the_adapter_name(cfg, ui):
    controller, _, saved = build(cfg, ui)

    run_all(controller, Start(), SetStaticIpRequested(32, PROFILE))

    assert saved[-1].static_ip_profiles == {ETHERNET.name: PROFILE}


def test_static_ip_rescans_the_adapter_afterwards(cfg, ui):
    """网段变了，原来的扫描结果全部作废。"""
    seen = []
    controller, _, _ = build(cfg, ui, on_scan=lambda adapter: seen.append(adapter.ipv4))

    run_all(controller, Start(), SetStaticIpRequested(32, PROFILE))

    assert seen == ["192.168.1.50"]


def test_static_ip_ends_in_a_settled_state(cfg, ui):
    controller, _, _ = build(cfg, ui)

    run_all(controller, Start(), SetStaticIpRequested(32, PROFILE))

    assert controller.state is not State.SWITCHING


def test_static_ip_failure_is_logged_and_nothing_is_saved(cfg, ui):
    controller, network, saved = build(cfg, ui)
    network.fail_on.add(32)

    run_all(controller, Start(), SetStaticIpRequested(32, PROFILE))

    assert any("拒绝操作" in line for line in logs(ui))
    assert controller.state is State.NO_PROXY
    assert saved == []


def test_static_ip_on_an_unknown_index_touches_nothing(cfg, ui):
    controller, network, _ = build(cfg, ui)

    run_all(controller, Start(), SetStaticIpRequested(999, PROFILE))

    assert network.ip_calls == []
    assert any("999" in line for line in logs(ui))


# ---------- 动态 IP ----------

def test_dhcp_is_applied_to_the_selected_adapter(cfg, ui):
    controller, network, _ = build(cfg, ui, ip_on_dhcp={32: ("172.20.10.7", 28)})

    run_all(controller, Start(), SetDhcpRequested(32))

    assert network.ip_calls == [("dhcp", 32, None)]


def test_dhcp_rescans_once_the_lease_arrives(cfg, ui):
    seen = []
    controller, _, _ = build(
        cfg, ui, ip_on_dhcp={32: ("10.1.2.3", 24)}, on_scan=lambda adapter: seen.append(adapter.ipv4)
    )

    run_all(controller, Start(), SetDhcpRequested(32))

    assert seen == ["10.1.2.3"]


def test_dhcp_times_out_when_no_lease_arrives(cfg, ui):
    controller, _, _ = build(cfg, ui, ip_on_dhcp={32: (None, None)})

    run_all(controller, Start(), SetDhcpRequested(32))

    assert any("超时" in line for line in logs(ui))
    assert controller.state is State.NO_PROXY


def test_dhcp_keeps_the_saved_static_profile(cfg, ui):
    """切回动态只是换寻址方式，用户填过的档位还要留着下次用。"""
    controller, _, saved = build(cfg, ui, ip_on_dhcp={32: ("172.20.10.7", 28)})

    run_all(controller, Start(), SetStaticIpRequested(32, PROFILE))
    run_all(controller, SetDhcpRequested(32))

    assert saved[-1].static_ip_profiles == {ETHERNET.name: PROFILE}


def test_dhcp_failure_is_logged(cfg, ui):
    controller, network, _ = build(cfg, ui)
    network.fail_on.add(32)

    run_all(controller, Start(), SetDhcpRequested(32))

    assert any("拒绝操作" in line for line in logs(ui))
    assert controller.state is State.NO_PROXY


# ---------- 档位广播给 UI（弹窗预填要用）----------

def test_saved_profiles_are_published_at_startup(cfg, ui):
    controller, _, _ = build(replace(cfg, static_ip_profiles={ETHERNET.name: PROFILE}), ui)

    run_all(controller, Start())

    published = [e for e in drain(ui) if isinstance(e, StaticIpProfilesUpdated)]
    assert published[-1].profiles == {ETHERNET.name: PROFILE}


def test_the_new_profile_is_published_after_a_successful_apply(cfg, ui):
    controller, _, _ = build(cfg, ui)

    run_all(controller, Start(), SetStaticIpRequested(32, PROFILE))

    published = [e for e in drain(ui) if isinstance(e, StaticIpProfilesUpdated)]
    assert published[-1].profiles == {ETHERNET.name: PROFILE}
