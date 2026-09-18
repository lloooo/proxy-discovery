import json
import pathlib

import pytest

from lan_proxy_switcher.network import (
    Adapter,
    AdapterKind,
    AdapterStatus,
    parse_snapshot,
    select_active,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "adapters_snapshot.json"


@pytest.fixture
def snapshot():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def adapters(snapshot):
    return parse_snapshot(snapshot)


def by_name(adapters, name):
    return next(adapter for adapter in adapters if adapter.name == name)


# ---------- 过滤（规格第 4 节规则 1）----------

def test_only_physical_adapters_survive(adapters):
    assert sorted(adapter.name for adapter in adapters) == [
        "Ethernet",
        "Ethernet 10",
        "Wi-Fi",
    ]


def test_virtual_adapters_are_dropped(adapters):
    names = {adapter.name for adapter in adapters}
    assert "Tailscale" not in names
    assert "VMware Network Adapter VMnet8" not in names
    assert "vEthernet (Default Switch)" not in names


# ---------- 类型判定（规格第 10 节：不依据中文名称）----------

def test_wifi_is_wireless(adapters):
    assert by_name(adapters, "Wi-Fi").kind is AdapterKind.WIRELESS


def test_realtek_is_wired(adapters):
    assert by_name(adapters, "Ethernet").kind is AdapterKind.WIRED


def test_iphone_usb_tether_counts_as_wired(adapters):
    """Apple Mobile Device Ethernet 的 PhysicalMediaType 是 Unspecified。"""
    assert by_name(adapters, "Ethernet 10").kind is AdapterKind.WIRED


# ---------- 状态映射 ----------

def test_status_mapping(adapters):
    assert by_name(adapters, "Ethernet 10").status is AdapterStatus.UP
    assert by_name(adapters, "Wi-Fi").status is AdapterStatus.DISCONNECTED


def test_disabled_status_is_recognised(snapshot):
    snapshot["adapters"][2]["Status"] = "Disabled"

    assert by_name(parse_snapshot(snapshot), "Wi-Fi").status is AdapterStatus.DISABLED


def test_unknown_status_degrades_to_disconnected(snapshot):
    snapshot["adapters"][2]["Status"] = "Not Present"

    assert by_name(parse_snapshot(snapshot), "Wi-Fi").status is AdapterStatus.DISCONNECTED


# ---------- 地址（规格第 4 节规则 2、3）----------

def test_apipa_address_is_treated_as_no_ipv4(adapters):
    wifi = by_name(adapters, "Wi-Fi")

    assert wifi.ipv4 is None
    assert wifi.prefix_length is None


def test_iphone_prefix_is_28_not_24(adapters):
    iphone = by_name(adapters, "Ethernet 10")

    assert iphone.ipv4 == "172.20.10.7"
    assert iphone.prefix_length == 28


def test_adapter_without_any_address_has_none(snapshot):
    snapshot["addresses"] = [a for a in snapshot["addresses"] if a["InterfaceIndex"] != 25]

    assert by_name(parse_snapshot(snapshot), "Ethernet").ipv4 is None


# ---------- 网关 ----------

def test_gateway_and_metric_come_from_default_route(adapters):
    iphone = by_name(adapters, "Ethernet 10")

    assert iphone.gateway == "172.20.10.1"
    assert iphone.metric == 25


def test_adapter_without_default_route_has_no_gateway(adapters):
    """场景 B 的对端就是这样：没有默认网关。"""
    assert by_name(adapters, "Ethernet").gateway is None


def test_onlink_next_hop_is_not_a_gateway(snapshot):
    snapshot["routes"] = [
        {"InterfaceIndex": 25, "NextHop": "0.0.0.0", "RouteMetric": 0, "InterfaceMetric": 5}
    ]

    assert by_name(parse_snapshot(snapshot), "Ethernet").gateway is None


def test_lowest_interface_metric_wins_when_several_routes(snapshot):
    snapshot["routes"].append(
        {"InterfaceIndex": 32, "NextHop": "172.20.10.1", "RouteMetric": 0, "InterfaceMetric": 5}
    )

    assert by_name(parse_snapshot(snapshot), "Ethernet 10").metric == 5


# ---------- 活动网卡选择（规格第 7.1 节）----------

def test_active_adapter_is_the_one_with_a_gateway(adapters):
    assert select_active(adapters).name == "Ethernet 10"


def test_active_falls_back_to_first_usable_ipv4_when_no_gateway(snapshot):
    snapshot["routes"] = []

    active = select_active(parse_snapshot(snapshot))

    assert active.name == "Ethernet 10"
    assert active.gateway is None


def test_active_prefers_lowest_metric_among_gateways(snapshot):
    snapshot["routes"] = [
        {"InterfaceIndex": 32, "NextHop": "172.20.10.1", "RouteMetric": 0, "InterfaceMetric": 25},
        {"InterfaceIndex": 25, "NextHop": "10.0.0.1", "RouteMetric": 0, "InterfaceMetric": 5},
    ]

    assert select_active(parse_snapshot(snapshot)).name == "Ethernet"


def test_active_is_none_when_nothing_has_an_address(snapshot):
    snapshot["addresses"] = []

    assert select_active(parse_snapshot(snapshot)) is None


def test_adapter_is_hashable_and_comparable():
    """monitor 用快照相等性做去抖动，Adapter 必须可比较。"""
    one = Adapter("Wi-Fi", 23, "d", AdapterKind.WIRELESS, AdapterStatus.UP, None, None, None, None)
    two = Adapter("Wi-Fi", 23, "d", AdapterKind.WIRELESS, AdapterStatus.UP, None, None, None, None)

    assert one == two
    assert len({one, two}) == 1
