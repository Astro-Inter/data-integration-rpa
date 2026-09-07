"""Um alerta SMTP por execução, com diagnóstico controlado e sem dados brutos."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
import logging
import smtplib
import ssl

from app.config.settings import EmailSettings


@dataclass
class FailureReport:
    run_id: str
    system: str = "Aplicação"
    stage: str = "Configuração inicial"
    dry_run: bool | None = None
    committed: bool = False
    is_test: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    omitted: int = 0
    email_settings: EmailSettings | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def set_stage(self, system: str, stage: str) -> None:
        self.system, self.stage = system, stage

    def add(self, detail: str, *, system: str | None = None, stage: str | None = None) -> None:
        # Chamadores fornecem somente mensagens controladas, nunca exceções de SDK/SQL.
        if len(self.issues) >= 50:
            self.omitted += 1
            return
        self.issues.append(f"[{system or self.system}] {stage or self.stage}: {detail}")

    def validation(self, error) -> None:
        ids = f"funcionario_id={error.legacy_id}; empresa_id={error.empresa_id}"
        self.add(f"{ids}; " + "; ".join(error.details), system="PostgreSQL legado", stage="Validação dos dados")


def configure_email(report: FailureReport, settings=None) -> None:
    # Independente do Firebase/PostgreSQL: erros na configuração deles também alertam.
    try:
        report.email_settings = EmailSettings(**settings.model_dump(), _env_file=None) if settings is not None else EmailSettings()
    except Exception:
        logging.getLogger(__name__).warning("Configuração de alerta por e-mail inválida; verifique as variáveis SMTP/EMAIL.")


def build_message(report: FailureReport, code: int, duration: float) -> EmailMessage:
    settings = report.email_settings
    message = EmailMessage()
    message["From"] = str(settings.email_from)
    message["To"] = str(settings.email_to)
    category = "Teste de alerta" if report.is_test else ("Falha" if code else "Alerta de histórico")
    message["Subject"] = f"[RPA] {category} na execução {report.run_id}"
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    # ID estável identifica a execução sem repetir tentativas automáticas de envio.
    message["Message-ID"] = f"<rpa-{report.run_id}@{str(settings.email_from).split('@')[1]}>"
    mode = "não determinado" if report.dry_run is None else ("simulação" if report.dry_run else "gravação")
    outcome = (
        "A transação do destino já terminou. Uma falha posterior não desfaz um commit concluído."
        if report.committed and not report.dry_run else
        "Não houve confirmação de sucesso nesta execução. Transações abertas são revertidas ao sair dos contextos."
    )
    details = report.issues or [f"[{report.system}] {report.stage}: execução encerrada sem sucesso; consulte os logs."]
    message.set_content("\n".join([
        "Teste de entrega: nenhuma sincronização foi executada." if report.is_test else "O RPA encontrou falhas durante a execução.",
        f"Execução: {report.run_id}", f"Início UTC: {report.started_at.isoformat()}",
        f"Modo: {mode}", f"Código de saída: {code}", f"Duração: {duration:.3f} segundos",
        "", "Diagnóstico:", *[f"- {item}" for item in details],
        f"Outros diagnósticos omitidos pelo limite de 50: {report.omitted}",
        "Contagens, quando disponíveis: " + (", ".join(f"{key}={value}" for key, value in report.counts.items()) or "processamento não concluído"),
        "", outcome,
        "Contas já criadas no Firebase podem permanecer; a próxima tentativa deve reaproveitá-las.",
        "Em simulação, cadastros e controle PostgreSQL não são gravados.",
        "", f"Histórico, se disponível: Firestore / rpa_execucoes / {report.run_id}",
        "Confira também o terminal ou o job do GitHub Actions com esse run_id.",
        "Corrija a causa informada e execute novamente, começando pela simulação.",
        "Este alerta não inclui valores de CPF/CNPJ, e-mails de usuários, senhas, SQL ou traceback.",
    ]))
    return message


def send_failure_alert(report: FailureReport, code: int, duration: float) -> str:
    settings = report.email_settings
    logger = logging.getLogger(__name__)
    if (code == 0 and not report.issues) or settings is None or not settings.email_alerts_enabled:
        return "ignorado"
    if not settings.smtp_password.get_secret_value().strip():
        logger.warning("Alerta não enviado: configure SMTP_PASSWORD com a senha de app do remetente. run_id=%s", report.run_id)
        return "sem_credencial"
    try:
        message = build_message(report, code, duration)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(str(settings.smtp_user), settings.smtp_password.get_secret_value())
            smtp.send_message(message)
        logger.info("Alerta aceito pelo servidor SMTP. run_id=%s", report.run_id)
        return "enviado"
    except Exception:
        # Inclusive autenticação/timeout: não mascarar o erro do RPA nem gerar alerta recursivo.
        logger.error("Falha ao enviar alerta por e-mail; verifique SMTP, senha de app e rede. run_id=%s", report.run_id)
        return "falha_envio"


if __name__ == "__main__":
    import argparse
    from uuid import uuid4
    parser = argparse.ArgumentParser(description="Verificar o envio SMTP sem executar a sincronização.")
    parser.add_argument("--test", action="store_true", required=True, help="Envia um e-mail identificado como teste.")
    parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    report = FailureReport("teste-" + uuid4().hex, is_test=True)
    configure_email(report)
    report.add("Verificação do envio de alertas solicitada pelo operador.", stage="Teste SMTP")
    raise SystemExit(0 if send_failure_alert(report, 1, 0) == "enviado" else 1)
