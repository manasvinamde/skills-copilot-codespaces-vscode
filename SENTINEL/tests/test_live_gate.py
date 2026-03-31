import os
import importlib


def test_is_live_allowed_env(monkeypatch):
    # Ensure environment toggles affect config.is_live_allowed()
    monkeypatch.setenv('ENABLE_LIVE', '1')
    monkeypatch.setenv('LIVE_APPROVAL_PHRASE', 'secret-phrase')

    import config
    importlib.reload(config)

    # no phrase provided -> allowed
    assert config.is_live_allowed() is True

    # correct phrase provided -> allowed
    assert config.is_live_allowed('secret-phrase') is True

    # wrong phrase -> not allowed
    assert config.is_live_allowed('bad') is False

