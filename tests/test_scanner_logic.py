import pytest

from lan_proxy_switcher.scanner import (
    ScanHit,
    ScanRefused,
    enumerate_targets,
    gateway_targets,
    select_best,
)


# ---------- 安全闸门（规格第 7.2 节第 1 条）----------

@pytest.mark.parametrize("ipv4", ["8.8.8.8", "1.1.1.1", "203.0.113.5"])
def test_refuses_public_addresses(ipv4):
    with pytest.raises(ScanRefused):
        enumerate_targets(ipv4, 24, (7890,))


def test_refuses_apipa():
    with pytest.raises(ScanRefused):
        enumerate_targets("169.254.25.234", 16, (7890,))


def test_refuses_loopback():
    with pytest.raises(ScanRefused):
        enumerate_targets("127.0.0.1", 8, (7890,))


@pytest.mark.parametrize(
    "ipv4,prefix",
    [("10.0.0.2", 24), ("172.20.10.7", 28), ("192.168.10.10", 24)],
)
def test_accepts_private_addresses(ipv4, prefix):
    assert enumerate_targets(ipv4, prefix, (7890,))


# ---------- 网段计算（规格第 7.2 节第 2、3 条）----------

def test_iphone_28_uses_real_mask():
    """iPhone 共享网络实测是 /28，不能按 /24 扫。"""
    targets = enumerate_targets("172.20.10.7", 28, (7890, 1080))

    ips = sorted({ip for ip, _ in targets}, key=lambda s: int(s.rsplit(".", 1)[1]))
    assert ips == [f"172.20.10.{i}" for i in range(1, 15) if i != 7]
    assert len(targets) == 13 * 2


def test_prefix_16_is_clamped_to_24():
    targets = enumerate_targets("10.1.2.3", 16, (7890,))

    assert len(targets) == 253  # 254 个主机地址减去本机
    assert all(ip.startswith("10.1.2.") for ip, _ in targets)


def test_own_ip_is_excluded():
    targets = enumerate_targets("192.168.10.10", 24, (7890,))

    assert ("192.168.10.10", 7890) not in targets


def test_network_and_broadcast_excluded():
    targets = enumerate_targets("192.168.10.10", 24, (7890,))
    ips = {ip for ip, _ in targets}

    assert "192.168.10.0" not in ips
    assert "192.168.10.255" not in ips


def test_prefix_32_yields_no_targets():
    assert enumerate_targets("192.168.1.5", 32, (7890,)) == []


def test_prefix_30_yields_only_the_peer():
    targets = enumerate_targets("192.168.1.5", 30, (7890, 1080))

    assert targets == [("192.168.1.6", 7890), ("192.168.1.6", 1080)]


def test_ports_are_expanded_per_ip_in_order():
    targets = enumerate_targets("192.168.1.5", 30, (7890, 1080))

    assert [port for _, port in targets] == [7890, 1080]


# ---------- 网关快路径（规格第 7.3 节阶段一）----------

def test_gateway_targets_expands_ports():
    assert gateway_targets("172.20.10.1", (7890, 1080)) == [
        ("172.20.10.1", 7890),
        ("172.20.10.1", 1080),
    ]


def test_gateway_targets_empty_when_no_gateway():
    assert gateway_targets(None, (7890, 1080)) == []


def test_gateway_targets_refuses_public_gateway():
    with pytest.raises(ScanRefused):
        gateway_targets("8.8.8.8", (7890,))


# ---------- 选择策略（规格第 7.4 节）----------

def test_prefer_port_wins_over_lower_latency_on_other_port():
    hits = [ScanHit("10.0.0.30", 1080, 5.0), ScanHit("10.0.0.20", 7890, 35.0)]

    assert select_best(hits, 7890, (7890, 1080)) == ScanHit("10.0.0.20", 7890, 35.0)


def test_lowest_latency_wins_within_the_same_port():
    hits = [ScanHit("10.0.0.30", 7890, 35.0), ScanHit("10.0.0.20", 7890, 10.0)]

    assert select_best(hits, 7890, (7890, 1080)).ip == "10.0.0.20"


def test_falls_back_to_next_port_when_prefer_port_absent():
    hits = [ScanHit("10.0.0.30", 1080, 15.0)]

    assert select_best(hits, 7890, (7890, 1080)).port == 1080


def test_select_best_returns_none_for_no_hits():
    assert select_best([], 7890, (7890, 1080)) is None
