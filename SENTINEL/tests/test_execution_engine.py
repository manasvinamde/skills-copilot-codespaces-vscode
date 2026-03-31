import importlib
import types


def test_dhan_engine_forces_mock_when_live_not_allowed(monkeypatch):
    # Ensure live is not allowed
    monkeypatch.delenv('ENABLE_LIVE', raising=False)

    import execution
    importlib.reload(execution)

    # Request live mode but guard should force mock
    engine = execution.DhanExecutionEngine(mock_mode=False)
    assert engine.mock_mode is True


def test_dhan_init_retries_and_succeeds(monkeypatch):
    import execution

    # Make execution believe live is allowed for this test
    monkeypatch.setenv('ENABLE_LIVE', '1')
    # reload config in execution context
    import config
    importlib.reload(config)
    execution.is_live_allowed = lambda *_: True

    # Ensure dhanhq is considered available and provide a fake constructor
    monkeypatch.setattr(execution, 'DHANHQ_AVAILABLE', True)

    call_count = {'n': 0}

    def fake_dhanhq(api_key, access_token):
        call_count['n'] += 1
        if call_count['n'] < 3:
            raise Exception('transient init error')
        return types.SimpleNamespace(client='ok')

    monkeypatch.setattr(execution, 'dhanhq', fake_dhanhq)

    # Provide minimal credentials on the config object used by execution
    execution.DhanAPIConfig.api_key = 'k'
    execution.DhanAPIConfig.access_token = 't'
    execution.DhanAPIConfig.account_id = 'acct'

    engine = execution.DhanExecutionEngine(mock_mode=False)
    # after retries, client should be set
    assert engine.dhan_client is not None
    assert call_count['n'] >= 3
