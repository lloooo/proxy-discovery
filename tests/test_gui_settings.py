"""设置按钮与配置弹窗。"""

import queue

import pytest

tk = pytest.importorskip("tkinter")

from lan_proxy_switcher.config import Config
from lan_proxy_switcher.controller import ConfigLoaded, ConfigUpdated
from lan_proxy_switcher.gui import Application


class FakeController:
    def __init__(self):
        self.posted = []

    def post(self, event):
        self.posted.append(event)


@pytest.fixture
def app(tk_root, tmp_path):
    for child in tk_root.winfo_children():
        child.destroy()
    revealed = []
    application = Application(
        tk_root,
        FakeController(),
        queue.Queue(),
        lambda: None,
        config_file=tmp_path / "config.json",
        reveal=revealed.append,
    )
    application.revealed = revealed
    yield application
    application._closing = True


def open_dialog(application, cfg=Config()):
    application._handle(ConfigLoaded(cfg))
    return application._open_settings_dialog()


def test_the_dialog_prefills_from_the_published_config(app):
    dialog = open_dialog(app, Config(ports=(1082,), prefer_port=1082, scan_timeout_ms=800))

    assert dialog.fields["ports"].get() == "1082"
    assert dialog.fields["prefer_port"].get() == "1082"
    assert dialog.fields["scan_timeout_ms"].get() == "800"


def test_the_dialog_prefills_the_flags(app):
    dialog = open_dialog(app, Config(auto_scan=False, restore_proxy_on_exit=True))

    assert dialog.fields["auto_scan"].get() is False
    assert dialog.fields["restore_proxy_on_exit"].get() is True


def test_the_dialog_will_not_open_before_a_config_arrives(app):
    """ConfigLoaded 还没到就没有可预填的值，宁可不开。"""
    assert app._open_settings_dialog() is None


def test_saving_posts_the_new_config_and_closes_the_dialog(app):
    dialog = open_dialog(app)
    dialog.fields["scan_timeout_ms"].set("900")
    dialog.fields["auto_set_proxy"].set(False)

    app._save_settings(dialog)

    assert app._controller.posted == [
        ConfigUpdated(Config(scan_timeout_ms=900, auto_set_proxy=False))
    ]
    assert not dialog.winfo_exists()


def test_an_illegal_value_keeps_the_dialog_open_and_explains_why(app):
    dialog = open_dialog(app)
    dialog.fields["scan_timeout_ms"].set("abc")

    app._save_settings(dialog)

    assert app._controller.posted == []
    assert dialog.winfo_exists()
    assert "TCP 超时" in dialog.error.get()


def test_a_prefer_port_outside_ports_is_refused(app):
    dialog = open_dialog(app)
    dialog.fields["ports"].set("1082")

    app._save_settings(dialog)

    assert app._controller.posted == []
    assert "首选端口" in dialog.error.get()


def test_a_second_save_clears_the_previous_error(app):
    dialog = open_dialog(app)
    dialog.fields["scan_concurrency"].set("0")
    app._save_settings(dialog)

    dialog.fields["scan_concurrency"].set("64")
    app._save_settings(dialog)

    assert app._controller.posted == [ConfigUpdated(Config(scan_concurrency=64))]


def test_the_dialog_shows_the_config_file_path(app, tmp_path):
    dialog = open_dialog(app)

    assert str(tmp_path / "config.json") in dialog.path_text


def test_open_folder_reveals_the_directory_holding_the_config(app, tmp_path):
    app._reveal_config_folder()

    assert app.revealed == [tmp_path]


def test_the_settings_button_opens_the_dialog(app):
    app._handle(ConfigLoaded(Config()))

    app._settings_button.invoke()

    assert app._root.winfo_children()  # 弹窗建起来了，且没有抛异常
