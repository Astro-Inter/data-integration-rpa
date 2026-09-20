"""Configura exportação opcional dos logs para um endpoint OTLP/HTTP."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import unquote, urlsplit, urlunsplit

from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource


SERVICE_NAME = "data-integration-rpa"
JOB_NAME = "user-sync"

_logger = logging.getLogger(__name__)


@dataclass
class Observability:
    """Mantém o handler ativo e entrega os lotes pendentes no encerramento."""

    provider: LoggerProvider | None = None
    handler: logging.Handler | None = None
    root_logger: logging.Logger | None = None

    @property
    def enabled(self) -> bool:
        return self.provider is not None

    def shutdown(self) -> None:
        if self.handler is not None and self.root_logger is not None:
            self.root_logger.removeHandler(self.handler)
        if self.provider is not None:
            try:
                self.provider.shutdown()
            except Exception:
                # Erros do provedor podem conter detalhes de transporte; não os exponha.
                _logger.warning(
                    "Falha ao finalizar a exportação OTLP; os logs locais foram preservados."
                )
        self.provider = None
        self.handler = None


def _logs_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("endpoint OTLP inválido")
    if parsed.query or parsed.fragment:
        raise ValueError("endpoint OTLP não pode conter query ou fragmento")
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1/logs"):
        path += "/v1/logs"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _headers(raw_headers: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in raw_headers.split(","):
        name, separator, value = item.partition("=")
        name = unquote(name).strip()
        value = unquote(value).strip()
        if not separator or not name or not value:
            raise ValueError("header OTLP inválido")
        if "\r" in name or "\n" in name or "\r" in value or "\n" in value:
            raise ValueError("header OTLP inválido")
        parsed[name] = value
    return parsed


def configure_observability(
    environ: Mapping[str, str] | None = None,
    root_logger: logging.Logger | None = None,
) -> Observability:
    """Adiciona OTLP ao logging existente somente quando endpoint e headers existem."""

    values = os.environ if environ is None else environ
    root = logging.getLogger() if root_logger is None else root_logger
    endpoint = values.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    raw_headers = values.get("OTEL_EXPORTER_OTLP_HEADERS", "").strip()

    if values.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return Observability()
    if not endpoint and not raw_headers:
        return Observability()
    if not endpoint or not raw_headers:
        _logger.warning(
            "Exportação OTLP desativada: configure endpoint e headers em conjunto."
        )
        return Observability()

    provider = None
    exporter = None
    try:
        resource_attributes = {
            "service.name": SERVICE_NAME,
            "job.name": JOB_NAME,
            "worker.name": SERVICE_NAME,
        }

        exporter = OTLPLogExporter(
            endpoint=_logs_endpoint(endpoint),
            headers=_headers(raw_headers),
        )
        provider = LoggerProvider(
            resource=Resource.create(resource_attributes),
            shutdown_on_exit=False,
        )
        provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
        handler = LoggingHandler(level=logging.NOTSET, logger_provider=provider)
        root.addHandler(handler)
        lifecycle = Observability(provider=provider, handler=handler, root_logger=root)
        _logger.info(
            "Exportação de logs via OTLP habilitada.",
            extra={"operation": "configure_observability", "status": "ok"},
        )
        return lifecycle
    except Exception:
        if provider is not None:
            provider.shutdown()
        elif exporter is not None:
            exporter.shutdown()
        _logger.warning(
            "Não foi possível inicializar a exportação OTLP; os logs continuarão no console."
        )
        return Observability()
