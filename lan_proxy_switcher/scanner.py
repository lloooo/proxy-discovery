"""局域网扫描：目标枚举、并发 TCP Connect、结果选择。

本模块不含任何 Windows 相关代码，可在任意平台单测。
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Sequence

PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


class ScanRefused(Exception):
    """目标不在允许扫描的私有网段内（规格第 20 节安全边界）。"""


@dataclass(frozen=True)
class ScanHit:
    ip: str
    port: int
    latency_ms: float


def _require_private(ipv4: str) -> ipaddress.IPv4Address:
    try:
        address = ipaddress.IPv4Address(ipv4)
    except ipaddress.AddressValueError as exc:
        raise ScanRefused(f"{ipv4} 不是合法的 IPv4 地址") from exc
    if not any(address in network for network in PRIVATE_NETWORKS):
        raise ScanRefused(f"{ipv4} 不在私有网段（10/8、172.16/12、192.168/16）内，拒绝扫描")
    return address


def gateway_targets(gateway: str | None, ports: Sequence[int]) -> list[tuple[str, int]]:
    if not gateway:
        return []
    _require_private(gateway)
    return [(gateway, port) for port in ports]


def enumerate_targets(
    ipv4: str,
    prefix_length: int,
    ports: Sequence[int],
    max_hosts_prefix: int = 24,
) -> list[tuple[str, int]]:
    """展开待扫描的 (ip, port)。

    掩码 >= /24 用真实掩码（iPhone 热点是 /28）；< /24 收窄到本机所在的 /24。
    剔除网络地址、广播地址与本机自身 IP。
    """
    address = _require_private(ipv4)
    prefix = max(prefix_length, max_hosts_prefix)
    network = ipaddress.ip_network(f"{ipv4}/{prefix}", strict=False)
    return [
        (str(host), port)
        for host in network.hosts()
        if host != address
        for port in ports
    ]


def select_best(
    hits: Sequence[ScanHit], prefer_port: int, ports: Sequence[int]
) -> ScanHit | None:
    """7890 > 1080，同端口取 TCP 建连耗时最低者。"""
    order = [prefer_port] + [port for port in ports if port != prefer_port]
    for port in order:
        candidates = [hit for hit in hits if hit.port == port]
        if candidates:
            return min(candidates, key=lambda hit: hit.latency_ms)
    return None
