import pytest

from lan_proxy_switcher import network


def test_enable_script_targets_the_interface_index():
    script = network.switch_script(23, True)

    assert "Enable-NetAdapter" in script
    assert "Get-NetAdapter -InterfaceIndex 23" in script
    assert "Enable-NetAdapter -InputObject $adapter" in script


def test_disable_script_targets_the_interface_index():
    script = network.switch_script(25, False)

    assert "Disable-NetAdapter" in script
    assert "Get-NetAdapter -InterfaceIndex 25" in script
    assert "Disable-NetAdapter -InputObject $adapter" in script


def test_script_never_prompts():
    """-NonInteractive 的子进程里任何确认提示都会挂死。"""
    for enabled in (True, False):
        script = network.switch_script(1, enabled)
        assert "-Confirm:$false" in script


def test_script_stops_on_error():
    """默认 non-terminating error 会让退出码保持 0，失败就被吞掉了。"""
    assert "-ErrorAction Stop" in network.switch_script(1, True)


def test_index_must_be_an_integer():
    """防注入：index 直接拼进命令串，必须是整数。"""
    with pytest.raises((TypeError, ValueError)):
        network.switch_script("23; Remove-Item C:\\", True)


def test_set_adapter_enabled_invokes_the_script(monkeypatch):
    seen = []
    monkeypatch.setattr(network, "run_powershell", lambda script, **kw: seen.append(script) or "")

    network.PowerShellNetworkService().set_adapter_enabled(23, True)

    assert len(seen) == 1
    assert "Enable-NetAdapter" in seen[0]
    assert "Get-NetAdapter -InterfaceIndex 23" in seen[0]
    assert "Enable-NetAdapter -InputObject $adapter" in seen[0]


def test_set_adapter_enabled_propagates_failure(monkeypatch):
    def boom(script, **kw):
        raise network.NetworkError("拒绝访问")

    monkeypatch.setattr(network, "run_powershell", boom)

    with pytest.raises(network.NetworkError):
        network.PowerShellNetworkService().set_adapter_enabled(23, False)
