from types import SimpleNamespace

import pytest

from app.campaign_agent import run_agent_step
from app.llm.client import ModelRunError


@pytest.mark.parametrize('recover', [True, False])
def test_disconnection_retries_only_once_before_any_tools_execute(monkeypatch, recover):
    monkeypatch.setattr('app.campaign_agent.time.sleep', lambda _: None)
    calls, closes = [], []
    def run(request):
        calls.append(request)
        if len(calls) == 1 or not recover:
            raise ModelRunError(str(len(calls)), 'provider_error')
        return 'model-selection'
    client = SimpleNamespace(run=run, close=lambda: closes.append(True))
    session = SimpleNamespace(get=lambda *a: SimpleNamespace(error={
        'root_cause': {'type': 'RemoteProtocolError'}}))
    if recover:
        assert run_agent_step(client, 'request', session) == 'model-selection'
    else:
        with pytest.raises(ModelRunError):
            run_agent_step(client, 'request', session)
    assert calls == ['request', 'request']
    assert closes == [True]


@pytest.mark.parametrize('status,root', [('timeout', 'ReadTimeout'),
    ('provider_error', 'APIStatusError'), ('validation_error', 'RemoteProtocolError')])
def test_other_errors_are_not_retried(status, root):
    calls = []
    def run(request):
        calls.append(request)
        raise ModelRunError('failed', status)
    client = SimpleNamespace(run=run)
    session = SimpleNamespace(get=lambda *a: SimpleNamespace(error={'root_cause': {'type': root}}))
    with pytest.raises(ModelRunError):
        run_agent_step(client, 'request', session)
    assert len(calls) == 1
