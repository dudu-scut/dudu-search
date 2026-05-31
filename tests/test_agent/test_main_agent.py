"""Agent 集成测试 — 构建 + 重试逻辑。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.agent.main_agent import _is_retryable_error, _retryable_llm_invoke


class TestIsRetryableError:
    """_is_retryable_error() 异常分类测试。"""

    def test_connection_error_is_retryable(self):
        """httpx.ConnectError 可重试。"""
        assert _is_retryable_error(httpx.ConnectError("连接失败")) is True

    def test_read_timeout_is_retryable(self):
        """httpx.ReadTimeout 可重试。"""
        assert _is_retryable_error(httpx.ReadTimeout("读取超时")) is True

    def test_remote_protocol_error_is_retryable(self):
        """httpx.RemoteProtocolError 可重试。"""
        assert _is_retryable_error(httpx.RemoteProtocolError("协议错误")) is True

    def test_builtin_connection_error_is_retryable(self):
        """内置 ConnectionError 可重试。"""
        assert _is_retryable_error(ConnectionError("连接被拒绝")) is True

    def test_builtin_timeout_error_is_retryable(self):
        """内置 TimeoutError 可重试。"""
        assert _is_retryable_error(TimeoutError("超时")) is True

    def test_http_429_rate_limit_is_retryable(self):
        """HTTP 429 状态码可重试。"""
        request = MagicMock()
        response = MagicMock()
        response.status_code = 429
        error = httpx.HTTPStatusError("限流", request=request, response=response)
        assert _is_retryable_error(error) is True

    def test_http_503_unavailable_is_retryable(self):
        """HTTP 503 可重试。"""
        request = MagicMock()
        response = MagicMock()
        response.status_code = 503
        error = httpx.HTTPStatusError("不可用", request=request, response=response)
        assert _is_retryable_error(error) is True

    def test_http_502_bad_gateway_is_retryable(self):
        """HTTP 502 可重试。"""
        request = MagicMock()
        response = MagicMock()
        response.status_code = 502
        error = httpx.HTTPStatusError("错误网关", request=request, response=response)
        assert _is_retryable_error(error) is True

    def test_http_401_auth_error_not_retryable(self):
        """HTTP 401 认证错误不可重试。"""
        request = MagicMock()
        response = MagicMock()
        response.status_code = 401
        error = httpx.HTTPStatusError("未授权", request=request, response=response)
        assert _is_retryable_error(error) is False

    def test_http_400_bad_request_not_retryable(self):
        """HTTP 400 不可重试。"""
        request = MagicMock()
        response = MagicMock()
        response.status_code = 400
        error = httpx.HTTPStatusError("错误请求", request=request, response=response)
        assert _is_retryable_error(error) is False

    def test_value_error_not_retryable(self):
        """普通 ValueError 不可重试。"""
        assert _is_retryable_error(ValueError("非网络错误")) is False

    def test_generic_exception_not_retryable(self):
        """普通 Exception 不可重试。"""
        assert _is_retryable_error(Exception("通用错误")) is False


class TestRetryableLLMInvoke:
    """_retryable_llm_invoke() 重试行为测试。"""

    async def test_successful_first_call(self):
        """第一次调用成功，直接返回结果。"""
        mock_model = AsyncMock()
        expected = MagicMock()
        expected.content = "成功回复"
        mock_model.ainvoke.return_value = expected

        result = await _retryable_llm_invoke(mock_model, [{"role": "user", "content": "hi"}])

        assert result is expected
        assert mock_model.ainvoke.call_count == 1

    async def test_retries_on_connection_error_then_succeeds(self):
        """连接错误时重试，第二次成功。"""
        mock_model = AsyncMock()
        expected = MagicMock()
        expected.content = "重试后成功"
        mock_model.ainvoke.side_effect = [
            httpx.ConnectError("首次连接失败"),
            expected,
        ]

        result = await _retryable_llm_invoke(mock_model, [{"role": "user", "content": "hi"}])

        assert result is expected
        assert mock_model.ainvoke.call_count == 2

    async def test_retries_on_429_then_succeeds(self):
        """429 限流时重试，第二次成功。"""
        mock_model = AsyncMock()
        expected = MagicMock()
        expected.content = "限流后成功"
        request = MagicMock()
        response = MagicMock()
        response.status_code = 429
        mock_model.ainvoke.side_effect = [
            httpx.HTTPStatusError("限流", request=request, response=response),
            expected,
        ]

        result = await _retryable_llm_invoke(mock_model, [{"role": "user", "content": "hi"}])

        assert result is expected
        assert mock_model.ainvoke.call_count == 2

    async def test_stops_after_three_failures(self):
        """三次全部失败后抛出错误。"""
        mock_model = AsyncMock()
        mock_model.ainvoke.side_effect = httpx.ConnectError("持续连接失败")

        with pytest.raises(Exception):
            await _retryable_llm_invoke(mock_model, [{"role": "user", "content": "hi"}])

        assert mock_model.ainvoke.call_count == 3

    async def test_no_retry_on_non_retryable_error(self):
        """不可重试的错误直接抛出，不重试。"""
        mock_model = AsyncMock()
        request = MagicMock()
        response = MagicMock()
        response.status_code = 401
        mock_model.ainvoke.side_effect = httpx.HTTPStatusError(
            "未授权", request=request, response=response
        )

        with pytest.raises(Exception):
            await _retryable_llm_invoke(mock_model, [{"role": "user", "content": "hi"}])

        # 不可重试错误 → 只调用一次
        assert mock_model.ainvoke.call_count == 1


class TestBuildMainAgent:
    """_build_main_agent() / create_deep_agent 构建测试。"""

    def test_build_main_agent_mocked(self):
        """使用 Mock 验证 _build_main_agent 能正常调用 create_deep_agent。"""
        with patch(
            "app.agent.main_agent.PostgresSaver"
        ) as mock_saver_cls:
            mock_saver = MagicMock()
            mock_saver_cls.from_conn_string.return_value = mock_saver

            with patch(
                "app.agent.main_agent.create_deep_agent"
            ) as mock_create:
                mock_agent = MagicMock()
                mock_create.return_value = mock_agent

                from app.agent.main_agent import _build_main_agent

                agent = _build_main_agent("2026年05月31日 08:00 (UTC+8)")

                # 验证返回的 agent 是 mock 对象
                assert agent is mock_agent

                # 验证 create_deep_agent 被调用
                mock_create.assert_called_once()
                call_kwargs = mock_create.call_args[1]
                assert "model" in call_kwargs
                assert "system_prompt" in call_kwargs
                assert "tools" in call_kwargs
                assert "checkpointer" in call_kwargs
                assert call_kwargs["checkpointer"] is mock_saver
                assert "subagents" in call_kwargs

    def test_build_without_tools(self):
        """验证 tools 参数来自 _BASE_TOOLS。"""
        with patch("app.agent.main_agent.PostgresSaver") as mock_saver_cls:
            mock_saver = MagicMock()
            mock_saver_cls.from_conn_string.return_value = mock_saver

            with patch("app.agent.main_agent.create_deep_agent") as mock_create:
                mock_create.return_value = MagicMock()

                from app.agent.main_agent import _build_main_agent, _BASE_TOOLS

                _build_main_agent("2026年05月31日 08:00 (UTC+8)")

                call_kwargs = mock_create.call_args[1]
                assert call_kwargs["tools"] is _BASE_TOOLS
                assert len(call_kwargs["tools"]) >= 3  # markdown, pdf, read_file
