import logfire
from fastapi import FastAPI
from openinference.instrumentation import get_attributes_from_context
from openinference.instrumentation.pydantic_ai import OpenInferenceSpanProcessor
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span


class ContextualOpenInferenceSpanProcessor(OpenInferenceSpanProcessor):
    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        span.set_attributes(dict(get_attributes_from_context()))


def configure_tracing(app: FastAPI, endpoint: str) -> None:
    logfire.configure(
        service_name="hackathon-backend",
        resource_attributes={"openinference.project.name": "hackathon-2026"},
        send_to_logfire=False,
        console=False,
        metrics=False,
        additional_span_processors=[
            ContextualOpenInferenceSpanProcessor(),
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)),
        ],
    )
    logfire.instrument_pydantic_ai()
    logfire.instrument_fastapi(app, excluded_urls=".*/api/health")
