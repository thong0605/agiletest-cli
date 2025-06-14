import json
import logging
import os
import time
from typing import Generator

import httpx
import jwt
from config import (
    AGILETEST_AUTH_BASE_URL,
    AGILETEST_BASE_URL,
    DEFAULT_TIMEOUT,
    FRAMEWORK_RESULT_FILETYPE_MAPPING,
    MIME_TYPE_MAPPING,
    TEST_EXECUTION_TYPES,
    AGILETEST_DC_TOKEN,
)
from httpx import Request, Response

LOG_LEVEL = os.getenv("LOG_LEVEL", logging.INFO)


class AgiletestAuth(httpx.Auth):
    requires_response_body = True

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_auth_url: str = AGILETEST_AUTH_BASE_URL,
        data_center: bool = False,
        data_center_token: str = AGILETEST_DC_TOKEN,
    ):
        self.logger = logging.getLogger(__name__)
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_auth_url
        self.token = ""
        self.data_center = data_center
        self.data_center_token = data_center_token

        if not self.data_center and (not self.client_id or not self.client_secret):
            raise ValueError("Client ID and Client Secret are required for Cloud version")
        if self.data_center and not self.data_center_token:
            raise ValueError("AGILETEST_DC_TOKEN is required in Data Center mode")

    def _check_valid_token(self) -> bool:
        if not self.token:
            return False
        try:
            claims = jwt.decode(self.token, verify=False)
        except jwt.DecodeError:
            return False
        expiration_timestamp = claims.get("exp", 0)
        return expiration_timestamp > int(time.time())

    def build_refresh_request(self) -> Request:
        self.logger.debug(f"Building refresh request for client id {self.client_id}")
        return httpx.Request(
            method="POST",
            url=f"{self.base_url}/api/apikeys/authenticate",
            json={"clientId": self.client_id, "clientSecret": self.client_secret},
        )

    def update_token(self, response: Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as err:
            self.logger.error(
                f"Failed to refresh token: {response.status_code} - {response.text}"
            )
            raise err
        self.logger.debug(f"New token: {response.text}")
        self.token = str(response.text).strip()

    def auth_flow(self, request: httpx.Request) -> Generator[Request, Response, None]:
        if self.data_center:
            request.headers["Authorization"] = f"Bearer {self.data_center_token}"
            response = yield request
        else:
            if not self._check_valid_token():
                self.logger.debug("Refreshing token")
                refresh_res = yield self.build_refresh_request()
                self.update_token(refresh_res)

            request.headers["Authorization"] = f"JWT {self.token}"
            response = yield request

            if response.status_code == 401:
                refresh_res = yield self.build_refresh_request()
                self.update_token(refresh_res)
                request.headers["Authorization"] = f"JWT {self.token}"
                yield request


class AgiletestHelper:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str = AGILETEST_BASE_URL,
        base_auth_url: str = AGILETEST_AUTH_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
        data_center: bool = False,
        data_center_token: str = AGILETEST_DC_TOKEN,
    ):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(LOG_LEVEL)
        self.base_url = base_url
        self.timeout = timeout
        self.auth = AgiletestAuth(
            client_id=client_id,
            client_secret=client_secret,
            base_auth_url=base_auth_url,
            data_center=data_center,
            data_center_token=data_center_token,
        )
        self.client = self._get_client()
        self.data_center = data_center

    def _get_client(self) -> httpx.Client:
        return httpx.Client(
            auth=self.auth, base_url=self.base_url, timeout=self.timeout
        )

    def _check_response(self, response: Response, json_check: bool = True) -> bool:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as err:
            self.logger.error(
                f"Request Error: {err} - {response.status_code} - {response.text}"
            )
            return False
        if json_check:
            try:
                response.json()
            except json.decoder.JSONDecodeError as err:
                self.logger.error(
                    f"Response invalid JSON response: {err} - {response.text}"
                )
                return False
        return True

    @staticmethod
    def _check_auto_test_framework_type(framework_type: str) -> str:
        framework_type = framework_type.lower()
        if framework_type not in TEST_EXECUTION_TYPES:
            raise ValueError(
                f"Invalid test execution type: {framework_type}. Supported frameworks: {TEST_EXECUTION_TYPES}"
            )
        return framework_type

    def upload_test_execution_text_data(
        self,
        framework_type: str,
        project_key: str,
        test_data: str,
        test_execution_key: str = "",
    ) -> bool | dict:
        """Upload test execution to Agiletest.

        Args:
            framework_type (str): framework type
            project_key (str): project key
            test_data (str): test execution data
            test_execution_key (str, optional): test execution jira issue key to import to. Defaults to "".

        Raises:
            ValueError: framework type not supported

        Returns:
            bool | dict: false if failed, dict with response if success
        """
        framework_type = self._check_auto_test_framework_type(framework_type)

        if self.data_center:
            apiPath = f"/rest/agiletest/1.0/test-executions/automation/{framework_type}"
        else:
            apiPath = f"/ds/test-executions/{framework_type}"

        params = {"projectKey": project_key}
        if test_execution_key:
            params["testExecutionKey"] = test_execution_key

        _, mime_type = self._get_file_type_from_test_framework(framework_type)
        headers = {"Content-Type": mime_type}
        res = self.client.post(
            apiPath,
            params=params,
            headers=headers,
            content=test_data,
        )
        result = self._check_response(res)
        if not result:
            return result
        self.logger.info(f"Test execution uploaded successfully: '{res.text}'")
        res_json: dict = res.json()
        test_execution_key = res_json.get("key", "")
        test_execution_url = res_json.get("url", "")
        missed_cases = res_json.get("missedCases", [])
        if missed_cases:
            self.logger.warning(
                f"Test execution {test_execution_key} with missed test cases: {missed_cases}"
            )
        self.logger.info(
            f"Test Execution issue updated: {test_execution_key} {test_execution_url}"
        )
        return res_json

    @staticmethod
    def _get_file_type_from_test_framework(framework_type: str) -> tuple[str, str]:
        """Get file extension and mime type from test framework type.

        Args:
            framework_type (str): test framework name

        Raises:
            ValueError: if framework type or mime type is not found

        Returns:
            tuple[str, str]: extension, mime type
        """
        extension = FRAMEWORK_RESULT_FILETYPE_MAPPING.get(framework_type, None)
        if extension is None:
            raise ValueError(f"Extension not found for framework type {framework_type}")
        mime_type = MIME_TYPE_MAPPING.get(extension, None)
        if mime_type is None:
            raise ValueError(f"Mime type not found for extension {extension}")
        return extension, mime_type

    def upload_test_execution_multipart(
        self,
        framework_type: str,
        test_results: str,
        test_execution_info: str,
    ) -> bool | dict:
        framework_type = self._check_auto_test_framework_type(framework_type)
        tr_extension, tr_mime_type = self._get_file_type_from_test_framework(
            framework_type
        )
        files = {
            "results": (
                f"results.{tr_extension}",
                test_results,
                tr_mime_type,
            ),
            "testExecution": (
                "info.json",  # currently only json is supported
                test_execution_info,
                MIME_TYPE_MAPPING["json"],
            ),
        }
     
        if self.data_center:
            apiPath = f"/plugins/servlet/agiletest/automation/multipart/{framework_type}"
        else:
            apiPath = f"/ds/test-executions/{framework_type}/multipart"
        res = self.client.post(
            apiPath,
            files=files,
        )
        result = self._check_response(res)
        if not result:
            return result

        self.logger.info(f"Test execution uploaded successfully: '{res.text}'")
        res_json: dict = res.json()
        test_execution_key = res_json.get("key", "")
        test_execution_url = res_json.get("url", "")
        missed_cases = res_json.get("missedCases", [])
        if missed_cases:
            self.logger.warning(
                f"Test execution {test_execution_key} with missed test cases: {missed_cases}"
            )
        self.logger.info(
            f"Test Execution issue updated: {test_execution_key} {test_execution_url}"
        )
        return res_json
