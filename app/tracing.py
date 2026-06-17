"""
OpenTelemetry 链路追踪初始化模块

功能：
    1. TracerProvider + OTLP gRPC 导出（对接 Jaeger / Grafana Tempo）
    2. FastAPI HTTP 中间件 — 为每个请求创建根 span
    3. 桥接已有 ContextVar trace_id，在 span 属性中保留应用级关联 ID

用法：
    from app.tracing import init_tracing, shutdown_tracing, get_tracer

    # server.py lifespan
    init_tracing(settings)
    ...
    await shutdown_tracing()

    # 业务代码中创建子 span
    tracer = get_tracer("agent")
    with tracer.start_as_current_span("run_agent") as span:
        span.set_attribute("session.id", thread_id)
        ...
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.config import Settings

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import StatusCode, Span, Status
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


# ── 模块级状态 ──

_tracer_provider: Optional[TracerProvider] = None
_tracer: Optional[trace.Tracer] = None
_propagator = TraceContextTextMapPropagator()


# ── 初始化与关闭 ──


def init_tracing(settings: "Settings") -> bool:
    """初始化 OpenTelemetry TracerProvider。

    当 settings.OTEL_ENABLED=False 时跳过初始化，所有后续调用均安全返回 no-op tracer。

    :param settings: 应用配置
    :return: 是否成功初始化
    """
    global _tracer_provider, _tracer

    if not settings.OTEL_ENABLED:
        # 使用全局 no-op provider — get_tracer() 仍可用，但不产生任何数据
        _tracer = trace.get_tracer(settings.OTEL_SERVICE_NAME)
        return False

    resource = Resource.create(
        {
            "service.name": settings.OTEL_SERVICE_NAME,
            "service.version": settings.APP_VERSION,
        }
    )

    _tracer_provider = TracerProvider(resource=resource)

    exporter = OTLPSpanExporter(
        endpoint=settings.OTEL_EXPORTER_ENDPOINT,
        insecure=settings.OTEL_EXPORTER_INSECURE,
    )
    _tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(_tracer_provider)

    _tracer = _tracer_provider.get_tracer(
        settings.OTEL_SERVICE_NAME,
        settings.APP_VERSION,
    )

    return True


async def shutdown_tracing() -> None:
    """关闭 TracerProvider，确保所有缓冲的 span 刷写到后端。"""
    global _tracer_provider
    if _tracer_provider is not None:
        _tracer_provider.shutdown()
        _tracer_provider = None


def get_tracer(name: str = "deepagents") -> trace.Tracer:
    """获取 tracer 实例。未初始化时返回全局 no-op tracer。"""
    if _tracer is not None:
        return _tracer
    return trace.get_tracer(name)


# ── FastAPI HTTP 中间件 ──


class TracingMiddleware(BaseHTTPMiddleware):
    """为每个 HTTP 请求创建根 span，记录方法、路径、状态码。

    如果请求头中包含 W3C Trace Context（traceparent），则自动关联上游链路。
    同时把应用级 trace_id 写入 span 属性，方便跨系统关联。
    """

    # 不追踪的健康检查/指标路径
    _SKIP_PATHS = frozenset({"/health", "/metrics", "/api/events/stream"})

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 跳过不需要追踪的路径
        if request.url.path in self._SKIP_PATHS:
            return await call_next(request)

        tracer = get_tracer("http")

        # 尝试从请求头提取上游 trace context（W3C 标准）
        ctx = _propagator.extract(dict(request.headers))

        span_name = f"{request.method} {request.url.path}"
        with tracer.start_as_current_span(
            span_name,
            context=ctx,
            kind=trace.SpanKind.SERVER,
        ) as span:
            # 标准 HTTP span 属性
            span.set_attribute("http.method", request.method)
            span.set_attribute("http.url", str(request.url))
            span.set_attribute("http.target", request.url.path)
            if request.client:
                span.set_attribute("http.client_ip", request.client.host)

            # 桥接应用级 trace_id（已在 trace_id_middleware 中生成）
            from app.api.context import get_trace_id, get_current_user_id

            app_trace_id = get_trace_id()
            if app_trace_id:
                span.set_attribute("app.trace_id", app_trace_id)

            response: Optional[Response] = None
            try:
                response = await call_next(request)
                span.set_attribute("http.status_code", response.status_code)
                if response.status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                elif response.status_code >= 400:
                    span.set_status(Status(StatusCode.UNSET))
                else:
                    span.set_status(Status(StatusCode.OK))
                return response
            except Exception as exc:
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
                raise
            finally:
                # 尝试在响应完成后再补充 user_id
                user_id = get_current_user_id()
                if user_id:
                    span.set_attribute("enduser.id", user_id)
