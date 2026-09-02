import logging

from serversense.logging import SuccessfulAccessFilter


def access_record(status_code: object) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (("127.0.0.1", 1234), "GET", "/api/health", "1.1", status_code),
        None,
    )


def test_successful_access_filter_suppresses_successful_requests() -> None:
    access_filter = SuccessfulAccessFilter()

    assert not access_filter.filter(access_record(200))
    assert not access_filter.filter(access_record(204))
    assert not access_filter.filter(access_record(302))


def test_successful_access_filter_retains_failed_requests() -> None:
    access_filter = SuccessfulAccessFilter()

    assert access_filter.filter(access_record(404))
    assert access_filter.filter(access_record(500))


def test_successful_access_filter_keeps_unrecognized_records() -> None:
    access_filter = SuccessfulAccessFilter()
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "message", (), None)

    assert access_filter.filter(record)
    assert access_filter.filter(access_record(object()))
