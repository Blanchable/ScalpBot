from app.config.settings import AppSettings


def test_settings_defaults():
    s = AppSettings()
    assert s.global_settings.broker_mode == "paper"
    assert "15m" in s.mode_settings
    assert "1h" in s.mode_settings
