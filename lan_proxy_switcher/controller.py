"""唯一的状态机。串行消费一个 inbox 队列，因此全程无需任何锁。

耗时操作（扫描、网卡切换）交给临时线程，完成后把结果投回 inbox。
本模块不导入 tkinter。
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from . import scanner
from .config import Config
from .network import Adapter, current_identity, select_active
from .proxy import ProxyState
from .scanner import DiscoveryResult, ScanHit


class State(Enum):
    IDLE = "空闲"
    SCANNING = "扫描中"
    PROXY_ACTIVE = "代理生效"
    NO_PROXY = "未使用代理"
    SWITCHING = "切换网卡中"


# ---------- inbox 消息 ----------

@dataclass(frozen=True)
class Start:
    pass


@dataclass(frozen=True)
class ScanRequested:
    adapter_index: int | None = None


@dataclass(frozen=True)
class ScanFinished:
    result: DiscoveryResult
    adapter: Adapter
    token: object


@dataclass(frozen=True)
class UseHitRequested:
    hit: ScanHit


@dataclass(frozen=True)
class DisableProxyRequested:
    pass


@dataclass(frozen=True)
class RefreshAdaptersRequested:
    pass


@dataclass(frozen=True)
class Shutdown:
    pass


# ---------- UI 事件 ----------

@dataclass(frozen=True)
class LogLine:
    text: str


@dataclass(frozen=True)
class AdaptersUpdated:
    adapters: tuple[Adapter, ...]
    active_index: int | None


@dataclass(frozen=True)
class ScanHitFound:
    hit: ScanHit


@dataclass(frozen=True)
class ScanResults:
    hits: tuple[ScanHit, ...]


@dataclass(frozen=True)
class ProxyStatus:
    server: str | None
    status: str


@dataclass(frozen=True)
class StateChanged:
    state: State


class Controller:
    def __init__(
        self,
        cfg: Config,
        network,
        proxy,
        monitor,
        ui: "queue.Queue[object]",
        spawn: Callable[[Callable[[], None]], None],
        *,
        identity: Callable[[], str] = current_identity,
        discover=scanner.discover,
        scan=scanner.scan,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._cfg = cfg
        self._network = network
        self._proxy = proxy
        self.monitor = monitor
        self._ui = ui
        self._spawn = spawn
        self._identity = identity
        self._discover = discover
        self._scan = scan
        self._sleep = sleep

        self.inbox: "queue.Queue[object]" = queue.Queue()
        self.state = State.IDLE
        self.adapters: tuple[Adapter, ...] = ()
        self.hits: tuple[ScanHit, ...] = ()
        self.current_server: str | None = None
        self.startup_proxy: ProxyState | None = None

        self._scan_token: object | None = None
        self._cancel = threading.Event()

        self._handlers: dict[type, Callable[[object], None]] = {
            Start: self._on_start,
            ScanRequested: self._on_scan_requested,
            ScanFinished: self._on_scan_finished,
            UseHitRequested: self._on_use_hit,
            DisableProxyRequested: self._on_disable_proxy,
            RefreshAdaptersRequested: self._on_refresh,
            Shutdown: self._on_shutdown,
        }

    # ---------- 队列 ----------

    def post(self, message: object) -> None:
        self.inbox.put(message)

    def run(self) -> None:
        while True:
            message = self.inbox.get()
            self._dispatch(message)
            if isinstance(message, Shutdown):
                return

    def process_pending(self, limit: int = 100) -> None:
        """测试与退出收尾用：把当前排队的消息处理完。"""
        for _ in range(limit):
            try:
                message = self.inbox.get_nowait()
            except queue.Empty:
                return
            self._dispatch(message)

    def _dispatch(self, message: object) -> None:
        handler = self._handlers.get(type(message))
        if handler is None:
            self._log(f"忽略未知消息：{type(message).__name__}")
            return
        try:
            handler(message)
        except Exception as exc:  # worker 的异常也会经这里落到日志
            self._log(f"处理 {type(message).__name__} 出错：{exc}")
            self._set_state(State.NO_PROXY)

    # ---------- 工具 ----------

    def _log(self, text: str) -> None:
        self._ui.put(LogLine(f"{time.strftime('%H:%M:%S')} {text}"))

    def _set_state(self, state: State) -> None:
        self.state = state
        self._ui.put(StateChanged(state))

    def _publish_adapters(self) -> list[Adapter]:
        self.adapters = tuple(self._network.list_adapters())
        active = select_active(self.adapters)
        self._ui.put(AdaptersUpdated(self.adapters, active.index if active else None))
        return list(self.adapters)

    def _cancel_scan(self) -> None:
        self._cancel.set()
        self._scan_token = None

    def _apply_hit(self, hit: ScanHit) -> None:
        server = f"{hit.ip}:{hit.port}"
        self._proxy.apply(server)
        self.current_server = server
        self.monitor.set_proxy(server)
        self._log(f"设置 Windows 系统代理成功，已校验：{server}")
        self._ui.put(ProxyStatus(server, "TCP 连接正常"))
        self._set_state(State.PROXY_ACTIVE)

    def _turn_proxy_off(self) -> None:
        self._proxy.disable()
        self.current_server = None
        self.monitor.set_proxy(None)
        self._log("已关闭 Windows 系统代理")
        self._ui.put(ProxyStatus(None, "未使用代理"))
        self._set_state(State.NO_PROXY)

    # ---------- 处理器 ----------

    def _on_start(self, _message: object) -> None:
        try:
            self._log(f"运行账户：{self._identity()}")
        except Exception as exc:
            self._log(f"无法获取运行账户：{exc}")

        self.startup_proxy = self._proxy.read()
        if self.startup_proxy.enable and self.startup_proxy.server:
            self._log(f"启动前系统代理：已启用 {self.startup_proxy.server}")
        else:
            self._log("启动前系统代理：未启用")

        self._publish_adapters()
        if self._cfg.auto_scan:
            self._begin_scan(None)
        else:
            self._log("autoScan=false，等待手动扫描")
            self._set_state(State.NO_PROXY)

    def _on_scan_requested(self, message: ScanRequested) -> None:
        self._begin_scan(message.adapter_index)

    def _begin_scan(self, adapter_index: int | None) -> None:
        self._cancel_scan()
        adapters = self._publish_adapters()

        if adapter_index is None:
            target = select_active(adapters)
        else:
            target = next((a for a in adapters if a.index == adapter_index), None)

        if target is None:
            self._log("没有可用于扫描的网卡")
            self._set_state(State.NO_PROXY)
            return

        cancel = threading.Event()
        token = object()
        self._cancel = cancel
        self._scan_token = token
        self.hits = ()
        self._ui.put(ScanResults(()))
        self._set_state(State.SCANNING)

        self._log(f"当前网卡：{target.name}（{target.description}）")
        if target.ipv4:
            self._log(f"IP：{target.ipv4}/{target.prefix_length}")
        if target.gateway:
            self._log(f"优先检测 Gateway：{target.gateway}")

        cfg = self._cfg
        ui = self._ui
        scan = self._scan

        def job() -> None:
            def scan_fn(targets):
                return scan(
                    targets,
                    cfg.scan_timeout_ms,
                    cfg.scan_concurrency,
                    cancel,
                    on_hit=lambda hit: ui.put(ScanHitFound(hit)),
                )

            try:
                result = self._discover(target, cfg.ports, scan_fn, cancel)
            except Exception as exc:
                result = DiscoveryResult((), "error", f"扫描异常：{exc}")
            self.post(ScanFinished(result, target, token))

        self._spawn(job)

    def _on_scan_finished(self, message: ScanFinished) -> None:
        if message.token is not self._scan_token:
            return  # 已被新一轮扫描取代，丢弃

        self.hits = message.result.hits
        self._ui.put(ScanResults(self.hits))
        self._log(message.result.message)

        if message.result.phase == "cancelled":
            return

        best = scanner.select_best(self.hits, self._cfg.prefer_port, self._cfg.ports)
        if best is None:
            self._log("未发现可用代理")
            if self._cfg.disable_proxy_when_unavailable:
                self._turn_proxy_off()
            else:
                self._set_state(State.NO_PROXY)
            return

        if self._cfg.auto_set_proxy:
            self._apply_hit(best)
        else:
            self._log("autoSetProxy=false，已列出扫描结果，等待手动选择")
            self._set_state(State.NO_PROXY)

    def _on_use_hit(self, message: UseHitRequested) -> None:
        self._apply_hit(message.hit)

    def _on_disable_proxy(self, _message: object) -> None:
        self._cancel_scan()
        self._turn_proxy_off()

    def _on_refresh(self, _message: object) -> None:
        self._publish_adapters()

    def _on_shutdown(self, _message: object) -> None:
        self._cancel_scan()
        if self._cfg.restore_proxy_on_exit and self.startup_proxy is not None:
            self._proxy.restore(self.startup_proxy)
            self._log("已还原启动前的代理状态")
