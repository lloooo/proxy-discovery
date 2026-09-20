import json

from lan_proxy_switcher import config, network


def test_missing_file_writes_defaults(tmp_path):
    path = tmp_path / "config.json"
    cfg = config.load(path)

    assert cfg.ports == (7890, 1082)
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
    assert written["ports"] == [7890, 1082]
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
    path.write_text(json.dumps({"ports": [1082], "preferPort": 7890}), encoding="utf-8")
    lines = []

    cfg = config.load(path, lines.append)

    assert cfg.ports == (1082,)
    assert cfg.prefer_port == 1082
    assert any("preferPort" in line for line in lines)


def test_bool_is_not_accepted_as_int(tmp_path):
    """True 在 Python 里是 int 的子类，必须显式挡掉。"""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"scanTimeout": True}), encoding="utf-8")

    cfg = config.load(path, lambda _line: None)

    assert cfg.scan_timeout_ms == 500


# ---------- 静态 IP 档位 ----------

def test_static_ip_profiles_default_to_empty(tmp_path):
    cfg = config.load(tmp_path / "config.json")

    assert cfg.static_ip_profiles == {}


def test_static_ip_profile_is_loaded(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"staticIpProfiles": {
        "Wi-Fi": {"ip": "192.168.1.50", "prefix": 24,
                  "gateway": "192.168.1.1", "dns": ["8.8.8.8", "1.1.1.1"]}
    }}), encoding="utf-8")

    profile = config.load(path).static_ip_profiles["Wi-Fi"]

    assert profile.ip == "192.168.1.50"
    assert profile.prefix_length == 24
    assert profile.gateway == "192.168.1.1"
    assert profile.dns == ("8.8.8.8", "1.1.1.1")


def test_static_ip_profile_without_gateway_or_dns_is_accepted(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"staticIpProfiles": {
        "Ethernet": {"ip": "10.0.0.2", "prefix": 8}
    }}), encoding="utf-8")

    profile = config.load(path).static_ip_profiles["Ethernet"]

    assert profile.gateway is None
    assert profile.dns == ()


def test_static_ip_profile_survives_a_save_load_round_trip(tmp_path):
    path = tmp_path / "config.json"
    cfg = config.Config(static_ip_profiles={
        "Wi-Fi": network.StaticIpProfile("192.168.1.50", 24, "192.168.1.1", ("8.8.8.8",))
    })

    config.save(path, cfg)

    assert config.load(path).static_ip_profiles == cfg.static_ip_profiles


def test_illegal_static_ip_profile_is_dropped_and_logged(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"staticIpProfiles": {
        "Wi-Fi": {"ip": "192.168.1.999", "prefix": 24},
        "Ethernet": {"ip": "10.0.0.2", "prefix": 8},
    }}), encoding="utf-8")
    lines = []

    cfg = config.load(path, lines.append)

    assert "Wi-Fi" not in cfg.static_ip_profiles
    assert "Ethernet" in cfg.static_ip_profiles
    assert any("Wi-Fi" in line for line in lines)


def test_static_ip_profiles_of_the_wrong_shape_fall_back_to_empty(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"staticIpProfiles": ["Wi-Fi"]}), encoding="utf-8")
    lines = []

    cfg = config.load(path, lines.append)

    assert cfg.static_ip_profiles == {}
    assert any("staticIpProfiles" in line for line in lines)
