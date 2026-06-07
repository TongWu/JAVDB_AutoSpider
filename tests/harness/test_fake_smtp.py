# tests/harness/test_fake_smtp.py
from tests.harness.fake_smtp import FakeSMTP


def test_fake_smtp_captures_and_reports_success():
    smtp = FakeSMTP()
    assert smtp.send_email("Subject", "Body", ["report.txt"], False) is True
    assert len(smtp.sent) == 1
    assert smtp.sent[0].subject == "Subject"
    assert smtp.sent[0].attachments == ("report.txt",)
    assert smtp.sent[0].dry_run is False


def test_fake_smtp_can_simulate_failure():
    smtp = FakeSMTP(succeed=False)
    assert smtp.send_email("S", "B") is False
    assert smtp.sent[0].attachments == ()
