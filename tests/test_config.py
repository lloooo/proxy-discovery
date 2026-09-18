import json

from lan_proxy_switcher import config


def test_missing_file_writes_defaults(tmp_path):
    path = tmp_path / "config.json"
    cfg = config.load(path)

    assert cfg.ports == (7890, 1080)
    assert cfg.prefer_port == 7890
    assert cfg.scan_timeout_ms == 500
    assert cfg.scan_concurrency == 100
    assert cfg.monitor_interval_s == 30
    assert cfg.proxy_check_failures == 3
    assert cfg.auto_scan is True
    assert cfg.auto_set_proxy is True
    assert cfg.disable_proxy_when_unavailable is False
    assert cfg.restore_proxy_on_exit is False

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["preferPort"] == 7890
    assert written["ports"] == [7890, 1080]
    assert written["restoreProxyOnExit"] is False


def test_partial_file_fills_missing_fields(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"monitorInterval": 10}), encoding="utf-8")

    cfg = config.load(path)

    assert cfg.monitor_interval_s == 10
    assert cfg.scan_timeout_ms == 500


def test_invalid_value_falls_back_and_logs(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"scanConcurrency": 0, "autoScan": "yes"}), encoding="utf-8")
    lines = []

    cfg = config.load(path, lines.append)

    assert cfg.scan_concurrency == 100
    assert cfg.auto_scan is True
    assert any("scanConcurrency" in line for line in lines)
    assert any("autoScan" in line for line in lines)


def test_unparsable_file_uses_all_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ 这不是 json", encoding="utf-8")
    lines = []

    cfg = config.load(path, lines.append)

    assert cfg == config.Config()
    assert any("无法解析" in line for line in lines)


def test_prefer_port_outside_ports_falls_back_to_first(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"ports": [1080], "preferPort": 7890}), encoding="utf-8")
    lines = []

    cfg = config.load(path, lines.append)

    assert cfg.ports == (1080,)
    assert cfg.prefer_port == 1080
    assert any("preferPort" in line for line in lines)


def test_bool_is_not_accepted_as_int(tmp_path):
    """True 在 Python 里是 int 的子类，必须显式挡掉。"""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"scanTimeout": True}), encoding="utf-8")

    cfg = config.load(path, lambda _line: None)

    assert cfg.scan_timeout_ms == 500
