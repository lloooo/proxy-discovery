"""LANProxySwitcher 入口。"""

from __future__ import annotations

import tkinter as tk

from lan_proxy_switcher import __version__


def main() -> None:
    root = tk.Tk()
    root.title(f"LANProxySwitcher {__version__}")
    root.geometry("900x700")
    tk.Label(root, text="骨架窗口：打包验证用").pack(expand=True)
    root.mainloop()


if __name__ == "__main__":
    main()
