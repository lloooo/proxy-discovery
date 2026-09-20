"""Tkinter 前端：只在主线程更新控件，通过队列与控制器通信。"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk
from typing import Callable

from .controller import (
    AdaptersUpdated,
    DisableProxyRequested,
    LogLine,
    ProxyStatus,
    ScanHitFound,
    ScanRequested,
    ScanResults,
    Shutdown,
    State,
    StateChanged,
    SwitchRequested,
    UseHitRequested,
)
from .network import Adapter, AdapterKind
from .scanner import ScanHit


class Application:
    """控制器的薄 UI 外壳，不包含任何网络或注册表操作。"""

    def __init__(
        self,
        root: tk.Tk,
        controller,
        ui_events: "queue.Queue[object]",
        stop: Callable[[], None],
    ) -> None:
        self._root = root
        self._controller = controller
        self._events = ui_events
        self._stop = stop
        self._adapters: dict[int, Adapter] = {}
        self._hits: dict[str, ScanHit] = {}
        self._closing = False

        self._state = tk.StringVar(value=State.IDLE.value)
        self._proxy = tk.StringVar(value="未使用代理")
        self._build()
        self._root.protocol("WM_DELETE_WINDOW", self.close)
        self._root.after(100, self._drain_events)

    def _build(self) -> None:
        self._root.title("LANProxySwitcher")
        self._root.geometry("1000x720")
        self._root.minsize(760, 560)
        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(3, weight=1)

        adapters_frame = ttk.LabelFrame(self._root, text="网络适配器", padding=10)
        adapters_frame.grid(row=0, column=0, padx=12, pady=(12, 6), sticky="nsew")
        adapters_frame.columnconfigure(0, weight=1)
        self._adapters_view = ttk.Treeview(
            adapters_frame,
            columns=("kind", "name", "description", "status", "ipv4"),
            show="headings",
            height=5,
        )
        headings = (("kind", "类型", 80), ("name", "名称", 150), ("description", "说明", 330),
                    ("status", "状态", 100), ("ipv4", "IPv4", 150))
        for key, title, width in headings:
            self._adapters_view.heading(key, text=title)
            self._adapters_view.column(key, width=width, anchor="w")
        self._adapters_view.grid(row=0, column=0, sticky="nsew")

        controls = ttk.Frame(adapters_frame)
        controls.grid(row=1, column=0, pady=(8, 0), sticky="w")
        self._wired_button = ttk.Button(controls, text="切换到有线", command=lambda: self._switch(AdapterKind.WIRED))
        self._wired_button.grid(row=0, column=0, padx=(0, 6))
        self._wireless_button = ttk.Button(controls, text="切换到 Wi-Fi", command=lambda: self._switch(AdapterKind.WIRELESS))
        self._wireless_button.grid(row=0, column=1)

        proxy_frame = ttk.LabelFrame(self._root, text="HTTP Proxy", padding=10)
        proxy_frame.grid(row=1, column=0, padx=12, pady=6, sticky="ew")
        proxy_frame.columnconfigure(0, weight=1)
        ttk.Label(proxy_frame, textvariable=self._proxy).grid(row=0, column=0, sticky="w")
        ttk.Label(proxy_frame, textvariable=self._state).grid(row=0, column=1, sticky="e")
        proxy_controls = ttk.Frame(proxy_frame)
        proxy_controls.grid(row=1, column=0, columnspan=2, pady=(8, 0), sticky="w")
        ttk.Button(proxy_controls, text="扫描代理", command=lambda: self._controller.post(ScanRequested())).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(proxy_controls, text="使用选中代理", command=self._use_selected).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(proxy_controls, text="关闭代理", command=lambda: self._controller.post(DisableProxyRequested())).grid(row=0, column=2)

        results_frame = ttk.LabelFrame(self._root, text="扫描结果", padding=10)
        results_frame.grid(row=2, column=0, padx=12, pady=6, sticky="ew")
        self._results_view = ttk.Treeview(results_frame, columns=("ip", "port", "latency"), show="headings", height=6)
        for key, title, width in (("ip", "IP", 250), ("port", "端口", 120), ("latency", "TCP Connect", 160)):
            self._results_view.heading(key, text=title)
            self._results_view.column(key, width=width, anchor="w")
        self._results_view.pack(fill="x")

        log_frame = ttk.LabelFrame(self._root, text="日志", padding=10)
        log_frame.grid(row=3, column=0, padx=12, pady=(6, 12), sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self._log = tk.Text(log_frame, height=10, state="disabled", wrap="word")
        scroll = ttk.Scrollbar(log_frame, command=self._log.yview)
        self._log.configure(yscrollcommand=scroll.set)
        self._log.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        log_controls = ttk.Frame(log_frame)
        log_controls.grid(row=1, column=0, columnspan=2, pady=(8, 0), sticky="w")
        ttk.Button(log_controls, text="清空日志", command=self._clear_log).grid(row=0, column=0)

    def _switch(self, kind: AdapterKind) -> None:
        adapter = next((item for item in self._adapters.values() if item.kind is kind), None)
        if adapter is not None:
            self._controller.post(SwitchRequested(adapter.index))

    def _use_selected(self) -> None:
        selection = self._results_view.selection()
        if selection:
            hit = self._hits.get(selection[0])
            if hit is not None:
                self._controller.post(UseHitRequested(hit))

    def _drain_events(self) -> None:
        for _ in range(200):
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            self._handle(event)
        if not self._closing:
            self._root.after(100, self._drain_events)

    def _handle(self, event: object) -> None:
        if isinstance(event, AdaptersUpdated):
            self._adapters = {adapter.index: adapter for adapter in event.adapters}
            self._replace_adapters(event.adapters, event.active_index)
        elif isinstance(event, ScanHitFound):
            self._insert_hit(event.hit)
        elif isinstance(event, ScanResults):
            self._replace_hits(event.hits)
        elif isinstance(event, ProxyStatus):
            value = event.server or "未使用代理"
            self._proxy.set(f"当前代理：{value}    状态：{event.status}")
        elif isinstance(event, StateChanged):
            self._state.set(event.state.value)
        elif isinstance(event, LogLine):
            self._append_log(event.text)

    def _replace_adapters(self, adapters: tuple[Adapter, ...], active_index: int | None) -> None:
        existing = self._adapters_view.get_children()
        if existing:
            self._adapters_view.delete(*existing)
        for adapter in adapters:
            marker = "● " if adapter.index == active_index else ""
            self._adapters_view.insert("", "end", iid=str(adapter.index), values=(
                adapter.kind.value, marker + adapter.name, adapter.description,
                adapter.status.value, adapter.ipv4 or "-",
            ))
        self._wired_button.state(("!disabled" if any(a.kind is AdapterKind.WIRED for a in adapters) else "disabled",))
        self._wireless_button.state(("!disabled" if any(a.kind is AdapterKind.WIRELESS for a in adapters) else "disabled",))

    def _replace_hits(self, hits: tuple[ScanHit, ...]) -> None:
        self._hits.clear()
        existing = self._results_view.get_children()
        if existing:
            self._results_view.delete(*existing)
        for hit in hits:
            self._insert_hit(hit)

    def _insert_hit(self, hit: ScanHit) -> None:
        key = f"{hit.ip}:{hit.port}"
        self._hits[key] = hit
        values = (hit.ip, hit.port, f"{hit.latency_ms:.0f} ms")
        if self._results_view.exists(key):
            self._results_view.item(key, values=values)
        else:
            self._results_view.insert("", "end", iid=key, values=values)

    def _append_log(self, line: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", line + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _clear_log(self) -> None:
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._controller.post(Shutdown())
        self._stop()
        self._root.destroy()
