"""GUI 日志区的追加与清空行为。"""

import queue

import pytest

tk = pytest.importorskip("tkinter")

from lan_proxy_switcher.gui import Application


class FakeController:
    def __init__(self):
        self.posted = []

    def post(self, event):
        self.posted.append(event)


@pytest.fixture
def app(tk_root):
    for child in tk_root.winfo_children():
        child.destroy()
    application = Application(tk_root, FakeController(), queue.Queue(), lambda: None)
    yield application
    application._closing = True  # 停掉 after 轮询，但把 root 留给下一个用例


def log_text(application):
    return application._log.get("1.0", "end-1c")


def test_clear_log_empties_the_view(app):
    app._append_log("第一行")
    app._append_log("第二行")
    assert log_text(app) == "第一行\n第二行\n"

    app._clear_log()

    assert log_text(app) == ""


def test_clear_log_keeps_the_widget_read_only(app):
    app._append_log("第一行")

    app._clear_log()

    assert str(app._log.cget("state")) == "disabled"


def test_log_still_appends_after_clearing(app):
    app._append_log("清空前")
    app._clear_log()

    app._append_log("清空后")

    assert log_text(app) == "清空后\n"
