import concurrent.futures
import pytest
import subprocess
import logging
import time
import concurrent

RATE_LIMIT = 100
OVERHEAD = 50
TOTAL_REQUESTS = RATE_LIMIT + OVERHEAD
MAX_THREADS = 20
TEST_COMMAND = "agiletest test-execution import -t junit -p TC -te TC-202 tests/junit-test-data.xml"


logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def send_request():
    result = subprocess.run(
        TEST_COMMAND.split(" "),
        capture_output=True,
    )
    return result


def is_throttled(result) -> bool:
    logs = result.stderr.decode("utf-8")
    return logs.find("Client error '429 Too Many Requests'") != -1


@pytest.mark.rate_limit
def test_request_under_rate_limit():
    result = send_request()
    assert (
        result.returncode == 0
    ), f"Command failed with status code {result.returncode}"


@pytest.mark.rate_limit
def test_api_throttling():
    start_time = time.time()
    hit_rate_limit = False
    time_ran = 0.0
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {executor.submit(send_request) for i in range(TOTAL_REQUESTS)}

        for future in concurrent.futures.as_completed(futures):
            time_ran = time.time() - start_time
            if time_ran >= 60:
                logging.error("Test exceeded 60 seconds, stopping")
                break
            if is_throttled(future.result()):
                hit_rate_limit = True
                break

    assert time_ran < 60, "Test exceeded 60 seconds, stopped"
    assert hit_rate_limit, "Rate limit not hit"
