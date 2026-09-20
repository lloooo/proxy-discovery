"""设置弹窗保存后的热更新：写盘、替换配置、同步监控线程。"""

from dataclasses import replace

from conftest import FakeMonitor, FakeNetwork, FakeProxy, WIFI, drain, make_adapter
from lan_proxy_switcher.config import Config
from lan_proxy_switcher.controller import (
    ConfigLoaded,
    ConfigUpdated,
    Controller,
    LogLine,
    Start,
)
from lan_proxy_switcher.network import StaticIpProfile
from lan_proxy_switcher.scanner import DiscoveryResult

ETHERNET = make_adapter()
PROFILE = StaticIpProfile("192.168.1.50", 24, "192.168.1.1", ("8.8.8.8",))


def build(cfg, ui):
    saved = []
    controller = Controller(
        replace(cfg, auto_scan=False),
        FakeNetwork([ETHERNET, WIFI]),
        FakeProxy(),
        FakeMonitor(),
        ui,
        spawn=lambda fn: fn(),
        identity=lambda: r"DESKTOP\llooo S-1-5-21-1",
        discover=lambda *a, **k: DiscoveryResult((), "subnet", "命中 0 个"),
        scan=lambda *a, **k: None,
        save_config=saved.append,
    )
    return controller, saved


def run_all(controller, *messages):
    for message in messages:
        controller.post(message)
    controller.process_pending()


def loaded(ui):
    return [event.cfg for event in drain(ui) if isinstance(event, ConfigLoaded)]


def test_start_publishes_the_current_config(cfg, ui):
    """GUI 没有别的途径知道初始配置，只能靠这条事件预填设置弹窗。"""
    controller, _saved = build(cfg, ui)

    run_all(controller, Start())

    assert loaded(ui)[-1].scan_timeout_ms == 500


def test_config_updated_replaces_the_controller_config(cfg, ui):
    controller, _saved = build(cfg, ui)
    run_all(controller, Start())

    run_all(controller, ConfigUpdated(replace(cfg, scan_timeout_ms=900, prefer_port=1082)))

    assert controller._cfg.scan_timeout_ms == 900
    assert controller._cfg.prefer_port == 1082


def test_config_updated_writes_the_file(cfg, ui):
    controller, saved = build(cfg, ui)
    run_all(controller, Start())

    run_all(controller, ConfigUpdated(replace(cfg, scan_concurrency=64)))

    assert [written.scan_concurrency for written in saved] == [64]


def test_config_updated_syncs_the_monitor(cfg, ui):
    controller, _saved = build(cfg, ui)
    run_all(controller, Start())

    run_all(
        controller,
        ConfigUpdated(replace(cfg, monitor_interval_s=15, scan_timeout_ms=900, proxy_check_failures=5)),
    )

    assert controller.monitor.updates == [(15, 900, 5)]


def test_config_updated_keeps_static_ip_profiles(cfg, ui):
    """弹窗拿的是快照；这期间 IP 弹窗可能刚写过档位，不能被覆盖掉。"""
    controller, saved = build(replace(cfg, static_ip_profiles={"Wi-Fi": PROFILE}), ui)
    run_all(controller, Start())

    run_all(controller, ConfigUpdated(Config(scan_timeout_ms=900)))

    assert controller._cfg.static_ip_profiles == {"Wi-Fi": PROFILE}
    assert saved[-1].static_ip_profiles == {"Wi-Fi": PROFILE}


def test_config_updated_republishes_and_logs(cfg, ui):
    controller, _saved = build(cfg, ui)
    run_all(controller, Start())
    drain(ui)

    run_all(controller, ConfigUpdated(replace(cfg, scan_timeout_ms=900)))
    events = drain(ui)

    assert [event.cfg.scan_timeout_ms for event in events if isinstance(event, ConfigLoaded)] == [900]
    assert any("配置已保存" in event.text for event in events if isinstance(event, LogLine))
