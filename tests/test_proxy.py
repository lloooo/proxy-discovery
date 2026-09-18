import sys
import winreg

import pytest

from lan_proxy_switcher.proxy import (
    INTERNET_SETTINGS_SUBKEY,
    ProxyError,
    ProxyState,
    RegistryProxyService,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows 注册表")

TEST_SUBKEY = r"Software\LANProxySwitcher\TestSettings"


@pytest.fixture
def service():
    calls = []
    svc = RegistryProxyService(subkey=TEST_SUBKEY, notify=lambda: calls.append("notify"))
    svc.notify_calls = calls
    yield svc
    for key in (TEST_SUBKEY, r"Software\LANProxySwitcher"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            pass


def raw_value(name):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_SUBKEY) as key:
        return winreg.QueryValueEx(key, name)[0]


def test_default_subkey_is_the_real_internet_settings():
    """这条常量一旦被改错，整个程序会写错地方。"""
    assert INTERNET_SETTINGS_SUBKEY == (
        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    )


def test_tests_never_use_the_real_subkey():
    assert TEST_SUBKEY != INTERNET_SETTINGS_SUBKEY


def test_read_of_missing_key_returns_empty_state(service):
    assert service.read() == ProxyState(False, None, None, None)


def test_apply_writes_enable_and_server(service):
    service.apply("172.20.10.1:7890")

    assert raw_value("ProxyEnable") == 1
    assert raw_value("ProxyServer") == "172.20.10.1:7890"
    assert service.read() == ProxyState(True, "172.20.10.1:7890", None, None)


def test_apply_notifies_windows(service):
    service.apply("172.20.10.1:7890")

    assert service.notify_calls == ["notify"]


def test_apply_removes_auto_config_url(service):
    """PAC 存在时 Windows 优先走 PAC，ProxyServer 会静默失效。"""
    service.restore(ProxyState(False, None, None, "http://example.local/pac"))
    assert service.read().auto_config_url == "http://example.local/pac"

    service.apply("10.0.0.20:7890")

    assert service.read().auto_config_url is None


def test_apply_keeps_existing_override(service):
    service.restore(ProxyState(False, None, "<local>;10.*", None))

    service.apply("10.0.0.20:7890")

    assert service.read().override == "<local>;10.*"


def test_disable_keeps_server_value(service):
    service.apply("10.0.0.20:7890")

    service.disable()

    state = service.read()
    assert state.enable is False
    assert state.server == "10.0.0.20:7890"


def test_restore_puts_every_field_back(service):
    service.apply("10.0.0.20:7890")
    snapshot = ProxyState(False, "192.168.1.1:8080", "<local>", "http://old/pac")

    service.restore(snapshot)

    assert service.read() == snapshot


def test_restore_deletes_fields_that_were_absent(service):
    service.restore(ProxyState(True, "1.2.3.4:80", "<local>", "http://pac"))

    service.restore(ProxyState(False, None, None, None))

    assert service.read() == ProxyState(False, None, None, None)


@pytest.mark.parametrize(
    "server",
    ["", "no-colon", "10.0.0.1:", ":7890", "10.0.0.1:0", "10.0.0.1:70000", "10.0.0.1:abc"],
)
def test_apply_rejects_malformed_server(service, server):
    with pytest.raises(ProxyError):
        service.apply(server)


def test_apply_raises_when_readback_disagrees(service, monkeypatch):
    """写后必须读回校验，不许只记『设置成功』。"""
    monkeypatch.setattr(service, "read", lambda: ProxyState(False, None, None, None))

    with pytest.raises(ProxyError):
        service.apply("10.0.0.20:7890")
