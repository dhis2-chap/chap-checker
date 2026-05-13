from datetime import UTC, datetime

from chap_checker.checks.base import CheckResult, Status
from chap_checker.runner import RunReport, VerifyReport


def _result(name: str, status: Status) -> CheckResult:
    return CheckResult(name=name, status=status, message="", duration_ms=1.0)


def test_summary_counts_by_status() -> None:
    report = RunReport(
        target_name="prod",
        target_url="https://prod.test",
        results=[
            _result("ping", Status.OK),
            _result("system-info", Status.OK),
            _result("chap-route", Status.FAIL),
            _result("chap-core", Status.SKIPPED),
            _result("modeling-app", Status.WARN),
        ],
    )
    assert report.summary.ok == 2
    assert report.summary.fail == 1
    assert report.summary.warn == 1
    assert report.summary.skipped == 1
    assert report.summary.error == 0
    assert report.ok is False


def test_verify_report_serializes_top_level_fields() -> None:
    started = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
    finished = datetime(2026, 5, 13, 12, 0, 5, tzinfo=UTC)
    report = VerifyReport(
        checker_version="0.1.0",
        started_at=started,
        finished_at=finished,
        runs=[
            RunReport(
                target_name="prod",
                target_url="https://prod.test",
                results=[_result("ping", Status.OK)],
            )
        ],
    )
    dumped = report.model_dump(mode="json")

    assert dumped["checker_version"] == "0.1.0"
    assert dumped["started_at"] == "2026-05-13T12:00:00Z"
    assert dumped["finished_at"] == "2026-05-13T12:00:05Z"
    assert dumped["ok"] is True
    assert dumped["runs"][0]["ok"] is True
    assert dumped["runs"][0]["summary"] == {
        "ok": 1,
        "warn": 0,
        "fail": 0,
        "error": 0,
        "skipped": 0,
    }
