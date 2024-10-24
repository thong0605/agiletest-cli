import concurrent.futures
import pytest
import subprocess
import logging
import time
import concurrent
from agiletest_cli.agiletest_client import AgiletestHelper
from agiletest_cli.config import (
    AGILETEST_AUTH_BASE_URL,
    AGILETEST_BASE_URL,
    AGILETEST_CLIENT_ID,
    AGILETEST_CLIENT_SECRET,
    DEBUG_LOG_FORMAT,
    DEFAULT_TIMEOUT,
)

FAILED_THRESHOLD = 20
RATE_LIMIT = 100
OVERHEAD = 50
TOTAL_REQUESTS = RATE_LIMIT + OVERHEAD
MAX_THREADS = 20
TEST_FRAMEWORK = "junit"
TEST_PROJECT_KEY = "TC"
TEST_EXECUTION_KEY = "TC-202"
TEST_FILE_PATH = "tests/junit-test-data.xml"
TEST_FILE_DATA = open(TEST_FILE_PATH, "r").read()


logging.basicConfig(
    level=logging.DEBUG,
    format=DEBUG_LOG_FORMAT,
    datefmt="%Y-%m-%d %H:%M:%S",
)


def send_request(test_agiletest_object: AgiletestHelper) -> bool | dict:
    return test_agiletest_object.upload_test_execution_text_xml(
        TEST_FRAMEWORK, TEST_PROJECT_KEY, TEST_FILE_DATA, TEST_EXECUTION_KEY
    )


def is_throttled(caplog: pytest.LogCaptureFixture) -> bool:
    records = caplog.get_records("call")
    throttle_hit = False
    for record in reversed(records):
        if "429 Too Many Requests" in record.message:
            throttle_hit = True
            break
    return throttle_hit


@pytest.mark.rate_limit
def test_request_under_rate_limit():
    test_agiletest_object = AgiletestHelper(
        client_id=AGILETEST_CLIENT_ID,
        client_secret=AGILETEST_CLIENT_SECRET,
        base_url=AGILETEST_BASE_URL,
        base_auth_url=AGILETEST_AUTH_BASE_URL,
        timeout=DEFAULT_TIMEOUT,
    )
    result = send_request(test_agiletest_object)
    assert result, f"Command returned failed"


@pytest.mark.rate_limit
def test_api_throttling(caplog: pytest.LogCaptureFixture):
    start_time = time.time()
    time_ran = 0.0
    with caplog.at_level(logging.ERROR):
        test_agiletest_object = AgiletestHelper(
            client_id=AGILETEST_CLIENT_ID,
            client_secret=AGILETEST_CLIENT_SECRET,
            base_url=AGILETEST_BASE_URL,
            base_auth_url=AGILETEST_AUTH_BASE_URL,
            timeout=DEFAULT_TIMEOUT,
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            futures = {
                executor.submit(send_request, test_agiletest_object)
                for i in range(TOTAL_REQUESTS)
            }

            for future in concurrent.futures.as_completed(futures):
                time_ran = time.time() - start_time
                if time_ran >= 60:
                    break
                if future.result():
                    break

    assert time_ran < 60, "Test exceeded 60 seconds, stopped"
    assert is_throttled(caplog), "Rate limit not hit, something else went wrong"
