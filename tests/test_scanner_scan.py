import socket
import threading
from dataclasses import dataclass

import pytest

from lan_proxy_switcher.scanner import (
    DiscoveryResult,
    ScanHit,
    ScanReport,
    discover,
    scan,
)


@pytest.fixture
def open_port():
    """开一个真实监听 socket，返回其端口；测试结束自动关闭。"""
    opened = []

    def _open():
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(8)
        opened.append(sock)
        return sock.getsockname()[1]

    yield _open
    for sock in opened:
        sock.close()


def closed_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# ---------- scan 对真实 socket 的行为 ----------

def test_scan_finds_a_listening_port(open_port):
    port = open_port()

    report = scan([("127.0.0.1", port)], 500, 10, threading.Event())

    assert [(hit.ip, hit.port) for hit in report.hits] == [("127.0.0.1", port)]
    assert report.hits[0].latency_ms >= 0.0
    assert report.completed == 1


def test_scan_misses_a_closed_port():
    report = scan([("127.0.0.1", closed_port())], 500, 10, threading.Event())

    assert report.hits == ()
    assert report.completed == 1
    assert sum(report.errors.values()) == 1


def test_scan_calls_on_hit_for_each_hit(open_port):
    ports = [open_port(), open_port()]
    seen = []

    scan(
        [("127.0.0.1", p) for p in ports],
        500,
        10,
        threading.Event(),
        on_hit=seen.append,
    )

    assert sorted(hit.port for hit in seen) == sorted(ports)


def test_scan_sorts_hits_by_port_then_latency(open_port):
    ports = sorted([open_port(), open_port()])

    report = scan([("127.0.0.1", p) for p in ports], 500, 10, threading.Event())

    assert [hit.port for hit in report.hits] == ports


def test_scan_of_empty_target_list_is_a_noop():
    report = scan([], 500, 10, threading.Event())

    assert report == ScanReport((), 0, 0, {}, False)


def test_scan_reports_cancellation():
    cancel = threading.Event()
    cancel.set()

    report = scan([("127.0.0.1", closed_port())], 500, 10, cancel)

    assert report.cancelled is True


def test_scan_of_unroutable_address_yields_no_hit():
    """192.0.2.0/24 是 RFC5737 保留的 TEST-NET-1，保证不可路由。"""
    report = scan([("192.0.2.1", 7890)], 200, 10, threading.Event())

    assert report.hits == ()


# ---------- 两阶段发现 ----------

@dataclass(frozen=True)
class FakeAdapter:
    name: str
    ipv4: str | None
    prefix_length: int | None
    gateway: str | None


def recording_scan_fn(hits_by_call):
    """按调用次序依次返回预设结果，并记录每次收到的目标。"""
    calls = []

    def scan_fn(targets):
        calls.append(list(targets))
        hits = tuple(hits_by_call[len(calls) - 1])
        return ScanReport(hits, len(targets), len(targets), {}, False)

    return scan_fn, calls


def test_gateway_hit_short_circuits_full_scan():
    adapter = FakeAdapter("Ethernet 10", "172.20.10.7", 28, "172.20.10.1")
    hit = ScanHit("172.20.10.1", 7890, 12.0)
    scan_fn, calls = recording_scan_fn([[hit]])

    result = discover(adapter, (7890, 1080), scan_fn, threading.Event())

    assert result.phase == "gateway"
    assert result.hits == (hit,)
    assert len(calls) == 1
    assert calls[0] == [("172.20.10.1", 7890), ("172.20.10.1", 1080)]


def test_falls_through_to_subnet_scan_when_gateway_is_silent():
    adapter = FakeAdapter("Ethernet 10", "172.20.10.7", 28, "172.20.10.1")
    hit = ScanHit("172.20.10.3", 7890, 20.0)
    scan_fn, calls = recording_scan_fn([[], [hit]])

    result = discover(adapter, (7890,), scan_fn, threading.Event())

    assert result.phase == "subnet"
    assert result.hits == (hit,)
    assert len(calls) == 2
    assert ("172.20.10.3", 7890) in calls[1]


def test_scans_subnet_directly_when_there_is_no_gateway():
    """场景 B：对端没有默认网关，只能靠全网段扫描发现。"""
    adapter = FakeAdapter("Ethernet", "192.168.10.10", 24, None)
    hit = ScanHit("192.168.10.20", 7890, 8.0)
    scan_fn, calls = recording_scan_fn([[hit]])

    result = discover(adapter, (7890,), scan_fn, threading.Event())

    assert result.phase == "subnet"
    assert result.hits == (hit,)
    assert len(calls) == 1


def test_adapter_without_ipv4_is_refused():
    adapter = FakeAdapter("Wi-Fi", None, None, None)
    scan_fn, calls = recording_scan_fn([[]])

    result = discover(adapter, (7890,), scan_fn, threading.Event())

    assert result.phase == "refused"
    assert calls == []
    assert "Wi-Fi" in result.message


def test_public_ipv4_is_refused_with_reason():
    adapter = FakeAdapter("Ethernet", "8.8.8.8", 24, None)
    scan_fn, calls = recording_scan_fn([[]])

    result = discover(adapter, (7890,), scan_fn, threading.Event())

    assert result.phase == "refused"
    assert calls == []
    assert "私有网段" in result.message


def test_all_unreachable_is_reported_as_adapter_down():
    import errno

    adapter = FakeAdapter("Ethernet", "192.168.10.10", 24, None)

    def scan_fn(targets):
        return ScanReport((), len(targets), len(targets), {errno.ENETUNREACH: len(targets)}, False)

    result = discover(adapter, (7890,), scan_fn, threading.Event())

    assert result.phase == "unreachable"
    assert "掉线" in result.message


def test_cancel_between_phases_stops_discovery():
    adapter = FakeAdapter("Ethernet 10", "172.20.10.7", 28, "172.20.10.1")
    cancel = threading.Event()

    def scan_fn(targets):
        cancel.set()
        return ScanReport((), len(targets), len(targets), {}, True)

    result = discover(adapter, (7890,), scan_fn, cancel)

    assert result.phase == "cancelled"


def test_discovery_result_is_returned_even_with_zero_hits():
    adapter = FakeAdapter("Ethernet", "192.168.10.10", 24, None)
    scan_fn, _ = recording_scan_fn([[]])

    result = discover(adapter, (7890,), scan_fn, threading.Event())

    assert isinstance(result, DiscoveryResult)
    assert result.hits == ()
    assert result.phase == "subnet"
