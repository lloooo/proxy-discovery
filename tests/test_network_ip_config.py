"""静态 / 动态 IP 的值对象与 PowerShell 脚本生成。"""

import pytest

from lan_proxy_switcher import network
from lan_proxy_switcher.network import StaticIpProfile

PROFILE = StaticIpProfile(
    ip="192.168.1.50",
    prefix_length=24,
    gateway="192.168.1.1",
    dns=("8.8.8.8", "1.1.1.1"),
)


# ---------- StaticIpProfile 校验（值一旦拼进命令串就来不及校验了）----------

def test_profile_rejects_a_malformed_ip():
    with pytest.raises(ValueError):
        StaticIpProfile(ip="192.168.1.999", prefix_length=24, gateway=None, dns=())


def test_profile_rejects_a_command_injection_attempt():
    with pytest.raises(ValueError):
        StaticIpProfile(ip="1.1.1.1'; Stop-Computer #", prefix_length=24, gateway=None, dns=())


def test_profile_rejects_a_malformed_gateway():
    with pytest.raises(ValueError):
        StaticIpProfile(ip="192.168.1.50", prefix_length=24, gateway="不是网关", dns=())


def test_profile_rejects_a_malformed_dns_server():
    with pytest.raises(ValueError):
        StaticIpProfile(ip="192.168.1.50", prefix_length=24, gateway=None, dns=("8.8.8.8", "x"))


def test_profile_rejects_prefix_length_out_of_range():
    for prefix in (0, 33):
        with pytest.raises(ValueError):
            StaticIpProfile(ip="192.168.1.50", prefix_length=prefix, gateway=None, dns=())


def test_profile_rejects_a_bool_prefix_length():
    """True 是 int 的子类，会变成 PrefixLength 1。"""
    with pytest.raises(ValueError):
        StaticIpProfile(ip="192.168.1.50", prefix_length=True, gateway=None, dns=())


def test_profile_allows_no_gateway_and_no_dns():
    profile = StaticIpProfile(ip="192.168.1.50", prefix_length=24, gateway=None, dns=())

    assert profile.gateway is None
    assert profile.dns == ()


def test_profile_is_hashable():
    """Config 是 frozen dataclass，存进去的档位必须可哈希。"""
    assert len({PROFILE, PROFILE}) == 1


# ---------- 静态 IP 脚本 ----------

def test_static_script_sets_address_prefix_and_gateway():
    script = network.static_ip_script(23, PROFILE)

    assert "New-NetIPAddress" in script
    assert "-InterfaceIndex 23" in script
    assert "-IPAddress '192.168.1.50'" in script
    assert "-PrefixLength 24" in script
    assert "-DefaultGateway '192.168.1.1'" in script


def test_static_script_disables_dhcp_before_adding_the_address():
    """DHCP 仍开着时 New-NetIPAddress 会失败。"""
    script = network.static_ip_script(23, PROFILE)

    assert "-Dhcp Disabled" in script
    assert script.index("-Dhcp Disabled") < script.index("New-NetIPAddress")


def test_static_script_clears_the_old_address_and_default_route_first():
    script = network.static_ip_script(23, PROFILE)

    assert "Remove-NetIPAddress" in script
    assert "Remove-NetRoute" in script
    assert script.index("Remove-NetIPAddress") < script.index("New-NetIPAddress")


def test_static_script_tolerates_absent_old_address():
    """网卡本来就没有静态地址时 Remove-* 会报错，不能让整条脚本失败。"""
    script = network.static_ip_script(23, PROFILE)
    cleanup = script[: script.index("New-NetIPAddress")]

    assert cleanup.count("-ErrorAction SilentlyContinue") == 2


def test_static_script_sets_the_dns_servers():
    script = network.static_ip_script(23, PROFILE)

    assert "Set-DnsClientServerAddress" in script
    assert "-ServerAddresses @('8.8.8.8','1.1.1.1')" in script


def test_static_script_resets_dns_when_none_given():
    profile = StaticIpProfile(ip="192.168.1.50", prefix_length=24, gateway=None, dns=())

    script = network.static_ip_script(23, profile)

    assert "-ResetServerAddresses" in script
    assert "-ServerAddresses" not in script


def test_static_script_omits_the_gateway_when_none_given():
    profile = StaticIpProfile(ip="192.168.1.50", prefix_length=24, gateway=None, dns=())

    assert "-DefaultGateway" not in network.static_ip_script(23, profile)


def test_static_script_rejects_a_non_integer_index():
    with pytest.raises((TypeError, ValueError)):
        network.static_ip_script("23; Remove-Item C:\\", PROFILE)


def test_static_script_never_prompts():
    """-NonInteractive 的子进程里任何确认提示都会挂死。"""
    script = network.static_ip_script(23, PROFILE)

    assert script.count("-Confirm:$false") == 2


def test_static_script_stops_on_error():
    script = network.static_ip_script(23, PROFILE)

    assert "New-NetIPAddress" in script
    for line in script.split(";"):
        if "New-NetIPAddress" in line or "Set-NetIPInterface" in line:
            assert "-ErrorAction Stop" in line


# ---------- 动态 IP 脚本 ----------

def test_dhcp_script_enables_dhcp():
    script = network.dhcp_script(23)

    assert "Set-NetIPInterface" in script
    assert "-InterfaceIndex 23" in script
    assert "-Dhcp Enabled" in script


def test_dhcp_script_does_not_clear_the_address_first():
    """预先删地址正是 DORA 不被触发的原因。

    _clear_ip_script 是为反方向写的（DHCP 还开着时 New-NetIPAddress 会失败）。
    用在这个方向上，等于把 static→DHCP 的迁移悄悄做完了，再标 Dhcp Enabled 就成了
    一次什么都不触发的空状态变更，网卡停在 APIPA 上。不删地址，Windows 自己在这
    次状态变化里走完 DORA——实测拿到 PrefixOrigin=Dhcp 的真实地址。
    """
    script = network.dhcp_script(23)

    assert "Remove-NetIPAddress" not in script
    assert "Remove-NetRoute" not in script


def test_dhcp_script_does_not_restart_the_adapter():
    """重启网卡会拆掉 802.11 关联，Wi-Fi 不一定自动连得回来。"""
    script = network.dhcp_script(23)

    assert "Restart-NetAdapter" not in script
    assert "RenewDHCPLease" not in script


def test_dhcp_script_still_checks_that_the_adapter_exists():
    assert "Get-NetAdapter -InterfaceIndex 23" in network.dhcp_script(23)


def test_dhcp_script_rejects_a_non_integer_index():
    with pytest.raises((TypeError, ValueError)):
        network.dhcp_script("23; Remove-Item C:\\")


def test_dhcp_script_never_prompts():
    """-NonInteractive 的子进程里任何确认提示都会挂死。现在一个会提示的 cmdlet 都不用。"""
    script = network.dhcp_script(23)

    for cmdlet in ("Remove-NetIPAddress", "Remove-NetRoute", "Restart-NetAdapter"):
        assert cmdlet not in script


# ---------- 服务层 ----------

def test_set_static_ip_invokes_the_script(monkeypatch):
    seen = []
    monkeypatch.setattr(network, "run_powershell", lambda script, **kw: seen.append(script) or "")

    network.PowerShellNetworkService().set_static_ip(23, PROFILE)

    assert len(seen) == 1
    assert "New-NetIPAddress" in seen[0]
    assert "-IPAddress '192.168.1.50'" in seen[0]


def test_set_dhcp_invokes_the_script(monkeypatch):
    seen = []
    monkeypatch.setattr(network, "run_powershell", lambda script, **kw: seen.append(script) or "")

    network.PowerShellNetworkService().set_dhcp(23)

    assert len(seen) == 1
    assert "-Dhcp Enabled" in seen[0]


def test_set_static_ip_propagates_failure(monkeypatch):
    def boom(script, **kw):
        raise network.NetworkError("拒绝访问")

    monkeypatch.setattr(network, "run_powershell", boom)

    with pytest.raises(network.NetworkError):
        network.PowerShellNetworkService().set_static_ip(23, PROFILE)
