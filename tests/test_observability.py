"""Configuração opcional e segura da exportação de logs OTLP."""

import logging
import unittest
from unittest.mock import patch

from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter

from app.observability import configure_observability


class ObservabilityTests(unittest.TestCase):
    def test_missing_configuration_keeps_only_local_logging(self):
        with patch("app.observability.OTLPLogExporter") as exporter:
            lifecycle = configure_observability({})
        self.assertFalse(lifecycle.enabled)
        exporter.assert_not_called()

    def test_partial_or_invalid_configuration_is_sanitized_and_optional(self):
        secret = "credencial-que-nao-pode-aparecer"
        with self.assertLogs("app.observability", level="WARNING") as logs:
            partial = configure_observability(
                {"OTEL_EXPORTER_OTLP_HEADERS": f"Authorization=Basic%20{secret}"}
            )
            invalid = configure_observability(
                {
                    "OTEL_EXPORTER_OTLP_ENDPOINT": "endpoint-invalido",
                    "OTEL_EXPORTER_OTLP_HEADERS": f"Authorization=Basic%20{secret}",
                }
            )
        self.assertFalse(partial.enabled)
        self.assertFalse(invalid.enabled)
        self.assertNotIn(secret, " ".join(logs.output))

    def test_logs_are_batched_with_resource_and_decoded_authorization(self):
        exporter = InMemoryLogRecordExporter()
        environment = {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://otlp.example.test/otlp",
            "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Basic%20abc123",
        }
        with patch(
            "app.observability.OTLPLogExporter", return_value=exporter
        ) as exporter_factory:
            lifecycle = configure_observability(environment)
            logging.getLogger("test.observability").warning(
                "registro de teste", extra={"operation": "unit_test", "status": "ok"}
            )
            lifecycle.shutdown()

        self.assertTrue(exporter_factory.called)
        self.assertEqual(
            exporter_factory.call_args.kwargs["endpoint"],
            "https://otlp.example.test/otlp/v1/logs",
        )
        self.assertEqual(
            exporter_factory.call_args.kwargs["headers"],
            {"Authorization": "Basic abc123"},
        )
        records = exporter.get_finished_logs()
        record = next(item for item in records if item.log_record.body == "registro de teste")
        self.assertEqual(record.log_record.severity_text, "WARN")
        self.assertEqual(record.log_record.attributes["operation"], "unit_test")
        self.assertEqual(record.resource.attributes["service.name"], "data-integration-rpa")


if __name__ == "__main__":
    unittest.main()
