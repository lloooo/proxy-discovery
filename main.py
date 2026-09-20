"""LANProxySwitcher 入口。"""

from __future__ import annotations

import tkinter as tk
import queue
import threading

from lan_proxy_switcher import __version__
from lan_proxy_switcher.config import config_path, load, save
from lan_proxy_switcher.controller import Controller, LogLine, Start
from lan_proxy_switcher.gui import Application
from lan_proxy_switcher.monitor import MonitorState, MonitorThread
from lan_proxy_switcher.network import PowerShellNetworkService
from lan_proxy_switcher.proxy import RegistryProxyService
from lan_proxy_switcher.scanner import probe


def main() -> None:
    startup_logs: list[str] = []
    cfg = load(config_path(), startup_logs.append)
    network = PowerShellNetworkService()
    proxy = RegistryProxyService()
    ui: "queue.Queue[object]" = queue.Queue()

    def spawn(job) -> None:
        threading.Thread(target=job, daemon=True).start()

    monitor_state = MonitorState(
        list_adapters=network.list_adapters,
        probe=probe,
        monitor_interval_s=cfg.monitor_interval_s,
        scan_timeout_ms=cfg.scan_timeout_ms,
        proxy_check_failures=cfg.proxy_check_failures,
    )
    def save_config(updated) -> None:
        # 写盘失败不该影响已经生效的网络设置，只记日志
        try:
            save(config_path(), updated)
        except OSError as exc:
            ui.put(LogLine(f"配置写回失败：{exc}"))

    controller = Controller(
        cfg, network, proxy, monitor_state, ui, spawn, save_config=save_config
    )
    controller_thread = threading.Thread(target=controller.run, name="controller", daemon=True)
    monitor_thread = MonitorThread(monitor_state, controller.inbox)

    root = tk.Tk()
    root.title(f"LANProxySwitcher {__version__}")

    def stop() -> None:
        monitor_thread.stop()
        controller_thread.join(2.0)

    Application(root, controller, ui, stop)
    for line in startup_logs:
        ui.put(LogLine(line))
    controller_thread.start()
    monitor_thread.start()
    controller.post(Start())
    root.mainloop()


if __name__ == "__main__":
    main()
