"""Validação dos dados e dos payloads sem consultar serviços externos."""

from dataclasses import replace
import unittest

from pydantic import ValidationError

from app.models.legacy_user import LegacyEmail
from app.models.prepared_user import TargetPolicy, normalize_document
from app.services.user_preparation_service import UserDataError, prepare_user
import test_change_tracking


class PreparationTests(unittest.TestCase):
    def test_normalizes_without_changing_spelling_or_raw_record(self):
        raw = replace(test_change_tracking.user(), nome="  Ana\tD'Ávila  ", cargo="  TI   Sênior ",
                      empresa_nome="  ACME   SA  ", departamento_nome="  Operação   Sul ",
                      cpf="529.982.247-25", empresa_cnpj="11.222.333/0001-81",
                      emails=(LegacyEmail(1, "  ANA@EXAMPLE.COM "), LegacyEmail(2, "ana@example.com"), LegacyEmail(3, None)))
        prepared = prepare_user(raw)
        self.assertEqual(prepared.usuario.nome, "Ana D'Ávila")
        self.assertEqual(prepared.cargo.nome, "TI Sênior")
        self.assertEqual(prepared.workspace.nome, "ACME SA")
        self.assertEqual(prepared.unidade.nome, "Operação Sul")
        self.assertEqual(prepared.usuario.cpf, "52998224725")
        self.assertEqual(prepared.workspace.cnpj, "11222333000181")
        self.assertEqual(prepared.usuario.email, "ana@example.com")
        self.assertEqual(raw.cpf, "529.982.247-25")

    def test_maps_relations_and_preserves_source_ids(self):
        prepared = prepare_user(replace(test_change_tracking.user(), id_funcionario=0, id_departamento=-1))
        self.assertEqual(prepared.legacy_id, 0)
        self.assertEqual(prepared.workspace.legacy_empresa_id, 1)
        self.assertEqual(prepared.unidade.legacy_departamento_id, -1)
        self.assertTrue(prepared.unidade.ativo)
        self.assertTrue(prepared.cargo.ativo)

    def test_rejects_missing_short_long_or_invalid_required_fields(self):
        for field in ("nome", "cargo", "empresa_nome", "departamento_nome"):
            for value in (None, " \t ", "A", "a" * 256, "Ana\x00Silva"):
                with self.subTest(field=field, value=value), self.assertRaises(UserDataError):
                    prepare_user(replace(test_change_tracking.user(), **{field: value}))

    def test_documents_validate_checksums_ascii_and_preserve_leading_zero(self):
        self.assertEqual(normalize_document("012.345.678-90", "cpf"), "01234567890")
        self.assertEqual(normalize_document("04.252.011/0001-10", "cnpj"), "04252011000110")
        for kind, invalid in (("cpf", (None, 52998224725, "11111111111", "52998224724", "CPF52998224725", "５２９９８２２４７２５")),
                              ("cnpj", (None, "00000000000000", "11222333000182", "11A22333000181"))):
            for value in invalid:
                with self.subTest(kind=kind, value=value), self.assertRaises(ValueError):
                    normalize_document(value, kind)

    def test_rejects_missing_or_inconsistent_relations(self):
        for values in ({"departamento_id_empresa": 2}, {"departamento_id_empresa": None},
                       {"empresa_nome": None}, {"empresa_cnpj": None}, {"departamento_nome": None}):
            with self.subTest(values=values), self.assertRaises(UserDataError):
                prepare_user(replace(test_change_tracking.user(), **values))

    def test_email_missing_ambiguous_invalid_and_display_name_rejected(self):
        for values in ((), (None, " "), ("ana@example.com", "outra@example.com"),
                       ("ana@example.com", "invalido"), ("Ana <ana@example.com>",),
                       ("ana @example.com",), ("ana\n@example.com",)):
            with self.subTest(values=values), self.assertRaises(UserDataError):
                prepare_user(replace(test_change_tracking.user(), emails=tuple(LegacyEmail(i, value) for i, value in enumerate(values))))

    def test_firebase_and_target_payloads_respect_constraints(self):
        prepared = prepare_user(test_change_tracking.user())
        self.assertEqual(prepared.firebase_payload(), {
            "email": "ana@example.com", "display_name": "Ana", "email_verified": False,
        })
        payload = prepared.target_insert_payload(firebase_uid="firebase-real-uid", cargo_id=17, unidade_id=23)
        self.assertEqual(payload["tipo"], "FUNCIONARIO")
        self.assertEqual(payload["status"], "PRE_CADASTRADO")
        self.assertEqual(payload["cargo_id"], 17)
        self.assertEqual(payload["unidade_id"], 23)
        self.assertNotIn("modalidade", payload)
        self.assertNotIn("criado_em", payload)
        self.assertNotIn("id_usuario", payload)
        self.assertNotIn("legacy_id", payload)
        self.assertNotIn("workspace_id", payload)
        self.assertEqual(prepared.workspace_payload(), {"nome": "Empresa A", "cnpj": "11222333000181"})
        self.assertEqual(prepared.unit_insert_payload(workspace_id=9), {"workspace_id": 9, "nome": "Operação", "ativo": True})
        self.assertEqual(prepared.job_insert_payload(workspace_id=9), {"workspace_id": 9, "nome": "Analista", "ativo": True})
        update = prepared.target_update_payload(firebase_uid="uid", cargo_id=17, unidade_id=23)
        for field in ("tipo", "status", "modalidade", "criado_em"):
            self.assertNotIn(field, update)

    def test_target_payload_needs_resolved_identifiers(self):
        prepared = prepare_user(test_change_tracking.user())
        for values in ({"firebase_uid": ""}, {"firebase_uid": "x" * 129}, {"cargo_id": 0}, {"unidade_id": "2"}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                prepared.target_insert_payload(**({"firebase_uid": "uid", "cargo_id": 1, "unidade_id": 2} | values))
        with self.assertRaises(ValidationError):
            TargetPolicy(tipo="GESTOR")

    def test_errors_do_not_expose_values(self):
        for values in ({"nome": "segredo-" * 100}, {"emails": (LegacyEmail(1, "segredo-invalido"),)}):
            with self.assertRaises(UserDataError) as caught:
                prepare_user(replace(test_change_tracking.user(), **values))
            self.assertNotIn("segredo", str(caught.exception))
