"""局域网扫描：目标枚举、并发 TCP Connect、结果选择。

本模块不含任何 Windows 相关代码，可在任意平台单测。
"""

from __future__ import annotations

import errno
import ipaddress
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

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


# 整段网络不可达时的 errno，用于区分「没扫到代理」与「网卡刚掉线」
UNREACHABLE_ERRNOS = frozenset({errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN})


@dataclass(frozen=True)
class ScanReport:
    hits: tuple[ScanHit, ...]
    attempted: int
    completed: int
    errors: dict[int, int]
    cancelled: bool

    def all_unreachable(self) -> bool:
        if self.completed == 0 or self.hits:
            return False
        unreachable = sum(
            count for code, count in self.errors.items() if code in UNREACHABLE_ERRNOS
        )
        return unreachable == self.completed


class _AdapterLike(Protocol):
    name: str
    ipv4: str | None
    prefix_length: int | None
    gateway: str | None


@dataclass(frozen=True)
class DiscoveryResult:
    hits: tuple[ScanHit, ...]
    phase: str
    message: str


def probe(ip: str, port: int, timeout_s: float) -> tuple[ScanHit | None, int | None]:
    """只做 TCP Connect。连上即算命中，任何错误都算未命中。"""
    started = time.perf_counter()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    try:
        sock.connect((ip, port))
    except socket.timeout:
        return None, errno.ETIMEDOUT
    except OSError as exc:
        return None, exc.errno
    finally:
        sock.close()
    return ScanHit(ip, port, (time.perf_counter() - started) * 1000.0), None


def scan(
    targets: Sequence[tuple[str, int]],
    timeout_ms: int,
    concurrency: int,
    cancel: threading.Event,
    on_hit: Callable[[ScanHit], None] | None = None,
) -> ScanReport:
    targets = list(targets)
    if not targets:
        return ScanReport((), 0, 0, {}, cancel.is_set())

    timeout_s = timeout_ms / 1000.0
    hits: list[ScanHit] = []
    errors: dict[int, int] = {}
    completed = 0

    cancelled = False
    with ThreadPoolExecutor(max_workers=min(concurrency, len(targets))) as pool:
        futures = [pool.submit(probe, ip, port, timeout_s) for ip, port in targets]
        for future in as_completed(futures):
            hit, code = future.result()
            completed += 1
            if hit is not None:
                hits.append(hit)
                if on_hit is not None:
                    on_hit(hit)
            elif code is not None:
                errors[code] = errors.get(code, 0) + 1
            if cancel.is_set():
                cancelled = True
                for pending in futures:
                    pending.cancel()
                break

    hits.sort(key=lambda hit: (hit.port, hit.latency_ms))
    return ScanReport(tuple(hits), len(targets), completed, errors, cancelled)


def discover(
    adapter: _AdapterLike,
    ports: Sequence[int],
    scan_fn: Callable[[list[tuple[str, int]]], ScanReport],
    cancel: threading.Event,
    max_hosts_prefix: int = 24,
) -> DiscoveryResult:
    """规格第 7.3 节的两阶段发现：先探网关，不中再扫全网段。"""
    if adapter.ipv4 is None or adapter.prefix_length is None:
        return DiscoveryResult((), "refused", f"{adapter.name} 无可用 IPv4，跳过扫描")

    try:
        first_stage = gateway_targets(adapter.gateway, ports)
    except ScanRefused:
        first_stage = []  # 网关不在私有段，跳过快路径，让全网段闸门给出统一说明

    if first_stage:
        report = scan_fn(first_stage)
        if report.hits:
            return DiscoveryResult(
                report.hits,
                "gateway",
                f"网关 {adapter.gateway} 命中 {len(report.hits)} 个端口，跳过全网段扫描",
            )

    if cancel.is_set():
        return DiscoveryResult((), "cancelled", "扫描已取消")

    try:
        targets = enumerate_targets(
            adapter.ipv4, adapter.prefix_length, ports, max_hosts_prefix
        )
    except ScanRefused as exc:
        return DiscoveryResult((), "refused", str(exc))

    if not targets:
        return DiscoveryResult(
            (),
            "refused",
            f"{adapter.ipv4}/{adapter.prefix_length} 没有可扫描的邻居地址",
        )

    report = scan_fn(targets)
    if report.cancelled:
        return DiscoveryResult((), "cancelled", "扫描已取消")
    if report.all_unreachable():
        return DiscoveryResult((), "unreachable", "全部目标网络不可达，网卡可能刚刚掉线")
    return DiscoveryResult(
        report.hits, "subnet", f"扫描 {len(targets)} 个目标，命中 {len(report.hits)} 个"
    )
