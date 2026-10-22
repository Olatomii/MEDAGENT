import io
import json
import urllib.error
from unittest.mock import patch
import pytest
import reminders
from hospital import reminder_job


@pytest.fixture
def brevo(monkeypatch):
    monkeypatch.setenv('EMAIL_PROVIDER', 'brevo')
    monkeypatch.setenv('BREVO_API_KEY', 'test-key')
    monkeypatch.setenv('REMINDER_FROM_EMAIL', 'sender@example.com')


def test_brevo_request_and_receipt(brevo):
    with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"messageId":"receipt"}')) as send:
        assert reminders.send_email('patient@example.com', 'Test body', 'claim') == 'receipt'
    request = send.call_args.args[0]
    assert request.full_url == 'https://api.brevo.com/v3/smtp/email'
    assert request.get_header('Api-key') == 'test-key'
    assert json.loads(request.data)['to'] == [{'email': 'patient@example.com'}]
    assert json.loads(request.data)['textContent'] == 'Test body'
    assert send.call_args.kwargs['timeout'] == 15


@pytest.mark.parametrize('failure', [TimeoutError(), urllib.error.HTTPError('url',429,'limit',{},None)])
def test_failure_is_not_retried(brevo, failure):
    with patch('urllib.request.urlopen', side_effect=failure) as send:
        with pytest.raises(type(failure)):
            reminders.send_email('patient@example.com', 'body', 'claim')
        assert send.call_count == 1


def test_missing_receipt(brevo):
    with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{}')):
        with pytest.raises(ValueError, match='receipt'):
            reminders.send_email('patient@example.com', 'body', 'claim')


def test_job_requires_explicit_activation(brevo, monkeypatch):
    monkeypatch.delenv('MEDAGENT_V2_REMINDERS_ENABLED', raising=False)
    with patch.object(reminder_job, 'dispatch') as send:
        with pytest.raises(SystemExit):
            reminder_job.main()
        send.assert_not_called()


def test_job_hides_connection_secrets(brevo, monkeypatch, capsys):
    monkeypatch.setenv('MEDAGENT_V2_REMINDERS_ENABLED', 'true')
    monkeypatch.setenv('MEDAGENT_DATABASE_URL', 'postgresql://secret')
    with patch.object(reminder_job, 'dispatch', side_effect=RuntimeError('secret')):
        with pytest.raises(SystemExit) as failure:
            reminder_job.main()
    assert 'secret' not in str(failure.value)
    assert 'secret' not in capsys.readouterr().out
