"""后台监控：代理 TCP 健康探测 + 网络变化检测。

两种节奏跑在同一个线程里：网络快照每 5 秒一次，代理探测每 monitorInterval 一次。
仍然只做 TCP Connect，禁止通过代理访问任何 URL。
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from .network import Adapter
from .scanner import ScanHit

ProbeFn = Callable[[str, int, float], "tuple[ScanHit | None, int | None]"]


@dataclass(frozen=True)
class NetworkChanged:
    adapters: tuple[Adapter, ...]


@dataclass(frozen=True)
class ProxyOk:
    server: str
    latency_ms: float


@dataclass(frozen=True)
class ProxyLost:
    server: str
    failures: int


@dataclass(frozen=True)
class MonitorError:
    message: str


class MonitorState:
    def __init__(
        self,
        *,
        list_adapters: Callable[[], Sequence[Adapter]],
        probe: ProbeFn,
        monitor_interval_s: int,
        scan_timeout_ms: int,
        proxy_check_failures: int,
        network_poll_interval_s: float = 5.0,
    ) -> None:
        self._list_adapters = list_adapters
        self._probe = probe
        self._monitor_interval_s = monitor_interval_s
        self._scan_timeout_ms = scan_timeout_ms
        self._proxy_check_failures = proxy_check_failures
        self._network_poll_interval_s = network_poll_interval_s

        self._previous_poll: tuple[Adapter, ...] | None = None
        self._reported: tuple[Adapter, ...] | None = None
        self._next_network_poll: float | None = None
        self._next_proxy_check: float | None = None
        self._server: str | None = None
        self._failures = 0

    def update(
        self,
        *,
        monitor_interval_s: int,
        scan_timeout_ms: int,
        proxy_check_failures: int,
    ) -> None:
        """由控制器线程调用的配置热更新。只改几个 int，无需加锁。

        与 set_proxy 不同：代理和已累计的失败次数保持不变，只把下一次探测
        重新排期，好让缩短后的间隔立刻起效。
        """
        self._monitor_interval_s = monitor_interval_s
        self._scan_timeout_ms = scan_timeout_ms
        self._proxy_check_failures = proxy_check_failures
        self._next_proxy_check = None

    def set_proxy(self, server: str | None) -> None:
        self._server = server
        self._failures = 0
        self._next_proxy_check = None

    def tick(self, now: float) -> list[object]:
        events: list[object] = []

        if self._next_network_poll is None or now >= self._next_network_poll:
            events.extend(self._poll_network())
            self._next_network_poll = now + self._network_poll_interval_s

        if self._server is not None:
            if self._next_proxy_check is None:
                self._next_proxy_check = now + self._monitor_interval_s
            elif now >= self._next_proxy_check:
                events.extend(self._check_proxy())
                self._next_proxy_check = now + self._monitor_interval_s

        return events

    def _poll_network(self) -> list[object]:
        try:
            current = tuple(self._list_adapters())
        except Exception as exc:  # 查询失败不能让监控线程死掉
            return [MonitorError(f"网卡查询失败：{exc}")]

        events: list[object] = []
        # 连续两次快照一致才认为网络已稳定，避免 Wi-Fi 重连途中反复触发重扫
        if (
            self._previous_poll is not None
            and current == self._previous_poll
            and current != self._reported
        ):
            self._reported = current
            events.append(NetworkChanged(current))
        self._previous_poll = current
        return events

    def _check_proxy(self) -> list[object]:
        assert self._server is not None
        host, _, port = self._server.rpartition(":")
        try:
            hit, _code = self._probe(host, int(port), self._scan_timeout_ms / 1000.0)
        except Exception as exc:
            return [MonitorError(f"代理探测失败：{exc}")]

        if hit is not None:
            self._failures = 0
            return [ProxyOk(self._server, hit.latency_ms)]

        self._failures += 1
        if self._failures >= self._proxy_check_failures:
            failures = self._failures
            self._failures = 0
            return [ProxyLost(self._server, failures)]
        return []


class MonitorThread:
    """MonitorState 的线程外壳。1 秒粒度，退出最多 1 秒收尾。"""

    def __init__(
        self,
        state,
        inbox: "queue.Queue[object]",
        clock: Callable[[], float] = time.monotonic,
        tick_interval: float = 1.0,
    ) -> None:
        self._state = state
        self._inbox = inbox
        self._clock = clock
        self._tick_interval = tick_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop.wait(self._tick_interval):
            try:
                for event in self._state.tick(self._clock()):
                    self._inbox.put(event)
            except Exception as exc:
                self._inbox.put(MonitorError(f"监控线程异常：{exc}"))
