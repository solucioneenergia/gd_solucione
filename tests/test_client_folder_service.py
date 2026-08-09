"""Testes para client_folder_service — normalização, fuzzy match, busca por protocolo."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.client_folder_service import (
    normalize_name,
    safe_folder_name,
    find_client_folder,
    build_destination_folder,
    build_destination_pdf_path,
    resolve_archive_destination_folder,
    archive_pdf_to_client_folder,
    _pending_folder,
    _relative_depth,
    _should_skip_dir,
    SKIP_DIR_NAMES,
)
from src.models import ClientFolderMatch


@pytest.fixture(autouse=True)
def disable_persistent_client_folder_cache(monkeypatch, tmp_path: Path):
    settings = SimpleNamespace(
        CACHE_CLIENT_FOLDER_LOOKUP=False,
        client_folder_cache_path=tmp_path / "client_folder_cache.json",
        clientes_root_path=tmp_path,
        FUZZY_AUTO_MATCH_SCORE=90,
        FUZZY_REVIEW_MATCH_SCORE=75,
        ARCHIVE_FALLBACK_MODE="pending",
        ARCHIVE_ENTRY_FOLDER_WHEN_MULTIPLE="latest",
        ARCHIVE_ALLOW_EXISTING_LEGACY_GD=False,
    )
    monkeypatch.setattr("src.client_folder_service.get_settings", lambda: settings)


class TestNormalizeName:
    def test_removes_accents(self):
        assert normalize_name("João Sérgio") == "JOAO SERGIO"
        assert normalize_name("ÇÁÉÍÓÚ") == "CAEIOU"

    def test_removes_special_chars(self):
        assert normalize_name("Silva & Cia. Ltda.") == "SILVA CIA LTDA"
        assert normalize_name("Teste@123") == "TESTE 123"

    def test_collapses_whitespace(self):
        assert normalize_name("  João   da   Silva  ") == "JOAO DA SILVA"

    def test_empty_string(self):
        assert normalize_name("") == ""


class TestSafeFolderName:
    def test_removes_invalid_chars(self):
        name = safe_folder_name('test<>:"/\\|?*name')
        for char in '<>:"/\\|?*':
            assert char not in name

    def test_preserves_valid_name(self):
        assert safe_folder_name("Cliente Teste 123") == "Cliente Teste 123"

    def test_truncates_long_names(self):
        long_name = "A" * 200
        result = safe_folder_name(long_name)
        assert len(result) <= 120

    def test_empty_returns_default(self):
        assert safe_folder_name("") == "SEM_NOME"


class TestFindClientFolder:
    def test_nonexistent_root(self, tmp_path: Path):
        root = tmp_path / "nonexistent"
        match = find_client_folder(root, "123456", "Cliente Teste")
        assert match.match_type == "pending_review"
        assert match.confidence == 0.0
        assert "não existe" in match.reason.lower()

    def test_fuzzy_name_match(self, tmp_path: Path):
        root = tmp_path / "clientes"
        root.mkdir()
        (root / "CLIENTE SINTETICO 008 LTDA Souza").mkdir()
        match = find_client_folder(root, "999999", "CLIENTE SINTETICO 008 LTDA Souza")
        assert match.match_type == "fuzzy_name"
        assert match.confidence >= 90.0

    def test_protocol_match_in_folder_name(self, tmp_path: Path):
        root = tmp_path / "clientes"
        root.mkdir()
        protocol = "2600001097"
        (root / f"Cliente X {protocol}").mkdir()
        match = find_client_folder(root, protocol, "Cliente Desconhecido")
        assert match.match_type == "protocol"
        assert match.confidence == 100.0

    def test_protocol_match_in_file_name(self, tmp_path: Path):
        root = tmp_path / "clientes"
        root.mkdir()
        protocol = "2600001097"
        # Usar um nome que não faz fuzzy match com o cliente buscado
        client_dir = root / "Pasta Sem Nome Relacionado"
        client_dir.mkdir()
        (client_dir / f"doc_{protocol}.pdf").touch()
        match = find_client_folder(root, protocol, "ZZZ Ninguem Conhecido")
        assert match.match_type == "protocol"
        assert match.found_by == "protocol_file"

    def test_no_match_returns_pending(self, tmp_path: Path):
        root = tmp_path / "clientes"
        root.mkdir()
        (root / "Outro Cliente").mkdir()
        match = find_client_folder(root, "000000", "Zzz Ninguém")
        assert match.match_type == "pending_review"


    def test_uses_client_folder_cache_when_path_exists(self, tmp_path: Path):
        root = tmp_path / "clientes"
        cached_folder = root / "Cliente Teste"
        cached_folder.mkdir(parents=True)
        cache_path = tmp_path / "cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "2601": {
                        "client_name": "Cliente Teste",
                        "matched_path": str(cached_folder),
                        "match_type": "fuzzy_name",
                        "confidence": 100.0,
                    }
                }
            ),
            encoding="utf-8",
        )
        settings = SimpleNamespace(
            CACHE_CLIENT_FOLDER_LOOKUP=True,
            client_folder_cache_path=cache_path,
            clientes_root_path=root,
            FUZZY_AUTO_MATCH_SCORE=90,
            FUZZY_REVIEW_MATCH_SCORE=75,
            ARCHIVE_FALLBACK_MODE="pending",
            ARCHIVE_ENTRY_FOLDER_WHEN_MULTIPLE="latest",
            ARCHIVE_ALLOW_EXISTING_LEGACY_GD=False,
        )

        with patch("src.client_folder_service.get_settings", return_value=settings):
            match = find_client_folder(root, "2601", "Cliente Teste")

        assert match.cache_hit is True
        assert match.cache_key == "2601"
        assert match.matched_path == str(cached_folder)

    def test_ignores_client_folder_cache_when_path_is_missing(self, tmp_path: Path):
        root = tmp_path / "clientes"
        real_folder = root / "Cliente Teste"
        real_folder.mkdir(parents=True)
        cache_path = tmp_path / "cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "2601": {
                        "client_name": "Cliente Teste",
                        "matched_path": str(root / "Antiga"),
                        "match_type": "fuzzy_name",
                        "confidence": 100.0,
                    }
                }
            ),
            encoding="utf-8",
        )
        settings = SimpleNamespace(
            CACHE_CLIENT_FOLDER_LOOKUP=True,
            client_folder_cache_path=cache_path,
            clientes_root_path=root,
            FUZZY_AUTO_MATCH_SCORE=90,
            FUZZY_REVIEW_MATCH_SCORE=75,
            ARCHIVE_FALLBACK_MODE="pending",
            ARCHIVE_ENTRY_FOLDER_WHEN_MULTIPLE="latest",
            ARCHIVE_ALLOW_EXISTING_LEGACY_GD=False,
        )

        with patch("src.client_folder_service.get_settings", return_value=settings):
            match = find_client_folder(root, "2601", "Cliente Teste")

        assert match.cache_hit is False
        assert match.match_type == "fuzzy_name"
        assert match.matched_path == str(real_folder)


class TestBuildDestinationFolder:
    def test_protocol_match_uses_pending_by_default_without_safe_destination(self, tmp_path: Path):
        match = ClientFolderMatch(
            protocol="123",
            client_name="CLIENTE SINTETICO LTDA",
            matched_path=str(tmp_path),
            match_type="protocol",
            confidence=100.0,
            reason="found",
        )
        dest = build_destination_folder(tmp_path, match, "123", "Teste")
        assert dest == tmp_path / "_PENDENTES_CONFERENCIA_GD" / "123 - Teste"

    def test_pending_review(self, tmp_path: Path):
        match = ClientFolderMatch(
            protocol="123",
            client_name="CLIENTE SINTETICO LTDA",
            matched_path=str(tmp_path / "_PENDENTES_CONFERENCIA_GD" / "123 - Teste"),
            match_type="pending_review",
            confidence=50.0,
            reason="not found",
        )
        dest = build_destination_folder(tmp_path, match, "123", "Teste")
        assert dest == tmp_path / "_PENDENTES_CONFERENCIA_GD" / "123 - Teste"


class TestResolveArchiveDestinationFolder:
    def test_uses_entry_date_folder(self, tmp_path: Path):
        client = tmp_path / "Cliente"
        entry = client / "Entrada 14-07-2026"
        entry.mkdir(parents=True)
        result = resolve_archive_destination_folder(client, "2600001104", "2026-07-14", "Cliente", tmp_path)
        assert result.destination_folder == entry
        assert result.match_type == "entrada_date_folder"
        assert result.should_create_folder is False

    def test_uses_lowercase_entry_date_folder(self, tmp_path: Path):
        client = tmp_path / "Cliente"
        entry = client / "entrada 14-07-2026"
        entry.mkdir(parents=True)
        result = resolve_archive_destination_folder(client, "2600001104", "2026-07-14", "Cliente", tmp_path)
        assert result.destination_folder == entry

    def test_uses_underscore_entry_date_folder(self, tmp_path: Path):
        client = tmp_path / "Cliente"
        entry = client / "Entrada 14_07_2026"
        entry.mkdir(parents=True)
        result = resolve_archive_destination_folder(client, "2600001104", "2026-07-14", "Cliente", tmp_path)
        assert result.destination_folder == entry

    def test_uses_protocol_folder_when_no_entry(self, tmp_path: Path):
        client = tmp_path / "Cliente"
        folder = client / "list_2600001104"
        folder.mkdir(parents=True)
        result = resolve_archive_destination_folder(client, "2600001104", "2026-07-14", "Cliente", tmp_path)
        assert result.destination_folder == folder
        assert result.match_type == "protocol_folder"

    def test_uses_client_folder_itself_when_it_contains_protocol(self, tmp_path: Path):
        folder = tmp_path / "Cliente 2600001104"
        folder.mkdir()
        result = resolve_archive_destination_folder(folder, "2600001104", "2026-07-14", "Cliente", tmp_path)
        assert result.destination_folder == folder
        assert result.match_type == "protocol_folder"

    def test_uses_protocol_file_parent_when_no_entry(self, tmp_path: Path):
        client = tmp_path / "Cliente"
        parent = client / "Documentos"
        parent.mkdir(parents=True)
        (parent / "list_2600001104.pdf").touch()
        result = resolve_archive_destination_folder(client, "2600001104", "2026-07-14", "Cliente", tmp_path)
        assert result.destination_folder == parent
        assert result.match_type == "protocol_file_parent"

    def test_multiple_entry_folders_latest(self, tmp_path: Path):
        client = tmp_path / "Cliente"
        old = client / "Entrada 01-01-2026"
        latest = client / "Entrada 14-07-2026"
        old.mkdir(parents=True)
        latest.mkdir()
        result = resolve_archive_destination_folder(client, "2600001104", None, "Cliente", tmp_path)
        assert result.destination_folder == latest
        assert result.match_type == "entrada_date_folder"

    def test_multiple_entry_folders_pending(self, tmp_path: Path, monkeypatch):
        settings = SimpleNamespace(
            CACHE_CLIENT_FOLDER_LOOKUP=False,
            client_folder_cache_path=tmp_path / "cache.json",
            clientes_root_path=tmp_path,
            FUZZY_AUTO_MATCH_SCORE=90,
            FUZZY_REVIEW_MATCH_SCORE=75,
            ARCHIVE_FALLBACK_MODE="pending",
            ARCHIVE_ENTRY_FOLDER_WHEN_MULTIPLE="pending",
            ARCHIVE_ALLOW_EXISTING_LEGACY_GD=False,
        )
        monkeypatch.setattr("src.client_folder_service.get_settings", lambda: settings)
        client = tmp_path / "Cliente"
        (client / "Entrada 01-01-2026").mkdir(parents=True)
        (client / "Entrada 14-07-2026").mkdir()
        result = resolve_archive_destination_folder(client, "2600001104", None, "Cliente", tmp_path)
        assert result.match_type == "pending_manual_review"
        assert "_PENDENTES_CONFERENCIA_GD" in str(result.destination_folder)

    def test_fallback_pending(self, tmp_path: Path):
        client = tmp_path / "Cliente"
        client.mkdir()
        result = resolve_archive_destination_folder(client, "2600001104", "2026-07-14", "Cliente", tmp_path)
        assert result.match_type == "pending_manual_review"
        assert result.should_create_folder is True
        assert "_PENDENTES_CONFERENCIA_GD" in str(result.destination_folder)

    def test_fallback_legacy_gd(self, tmp_path: Path, monkeypatch):
        settings = SimpleNamespace(
            CACHE_CLIENT_FOLDER_LOOKUP=False,
            client_folder_cache_path=tmp_path / "cache.json",
            clientes_root_path=tmp_path,
            FUZZY_AUTO_MATCH_SCORE=90,
            FUZZY_REVIEW_MATCH_SCORE=75,
            ARCHIVE_FALLBACK_MODE="legacy_gd",
            ARCHIVE_ENTRY_FOLDER_WHEN_MULTIPLE="latest",
            ARCHIVE_ALLOW_EXISTING_LEGACY_GD=False,
        )
        monkeypatch.setattr("src.client_folder_service.get_settings", lambda: settings)
        client = tmp_path / "Cliente"
        client.mkdir()
        result = resolve_archive_destination_folder(client, "2600001104", "2026-07-14", "Cliente", tmp_path)
        assert result.destination_folder == client / "GD Neoenergia" / "2600001104"
        assert result.match_type == "legacy_gd_folder"
        assert result.should_create_folder is True

    def test_ignores_existing_legacy_gd_protocol_folder_in_pending_mode(self, tmp_path: Path):
        client = tmp_path / "Cliente"
        legacy = client / "GD Neoenergia" / "2600001104"
        legacy.mkdir(parents=True)

        result = resolve_archive_destination_folder(
            client,
            "2600001104",
            "2026-07-14",
            "Cliente",
            tmp_path,
        )

        assert result.match_type == "pending_manual_review"
        assert result.destination_folder == tmp_path / "_PENDENTES_CONFERENCIA_GD" / "2600001104 - Cliente"
        assert result.legacy_gd_ignored is True

    def test_ignores_protocol_file_inside_legacy_gd_protocol_folder_in_pending_mode(self, tmp_path: Path):
        client = tmp_path / "Cliente"
        legacy = client / "GD Neoenergia" / "2600001104"
        legacy.mkdir(parents=True)
        (legacy / "Orcamento_de_Conexao_2600001104.pdf").touch()

        result = resolve_archive_destination_folder(
            client,
            "2600001104",
            "2026-07-14",
            "Cliente",
            tmp_path,
        )

        assert result.match_type == "pending_manual_review"
        assert result.destination_folder == tmp_path / "_PENDENTES_CONFERENCIA_GD" / "2600001104 - Cliente"
        assert result.legacy_gd_ignored is True

    def test_uses_existing_legacy_gd_protocol_folder_when_allowed_by_flag(self, tmp_path: Path, monkeypatch):
        settings = SimpleNamespace(
            CACHE_CLIENT_FOLDER_LOOKUP=False,
            client_folder_cache_path=tmp_path / "cache.json",
            clientes_root_path=tmp_path,
            FUZZY_AUTO_MATCH_SCORE=90,
            FUZZY_REVIEW_MATCH_SCORE=75,
            ARCHIVE_FALLBACK_MODE="pending",
            ARCHIVE_ENTRY_FOLDER_WHEN_MULTIPLE="latest",
            ARCHIVE_ALLOW_EXISTING_LEGACY_GD=True,
        )
        monkeypatch.setattr("src.client_folder_service.get_settings", lambda: settings)
        client = tmp_path / "Cliente"
        legacy = client / "GD Neoenergia" / "2600001104"
        legacy.mkdir(parents=True)

        result = resolve_archive_destination_folder(
            client,
            "2600001104",
            "2026-07-14",
            "Cliente",
            tmp_path,
        )

        assert result.destination_folder == legacy
        assert result.match_type == "protocol_folder"
        assert result.legacy_gd_ignored is False

    def test_uses_existing_legacy_gd_protocol_folder_in_legacy_gd_mode(self, tmp_path: Path, monkeypatch):
        settings = SimpleNamespace(
            CACHE_CLIENT_FOLDER_LOOKUP=False,
            client_folder_cache_path=tmp_path / "cache.json",
            clientes_root_path=tmp_path,
            FUZZY_AUTO_MATCH_SCORE=90,
            FUZZY_REVIEW_MATCH_SCORE=75,
            ARCHIVE_FALLBACK_MODE="legacy_gd",
            ARCHIVE_ENTRY_FOLDER_WHEN_MULTIPLE="latest",
            ARCHIVE_ALLOW_EXISTING_LEGACY_GD=False,
        )
        monkeypatch.setattr("src.client_folder_service.get_settings", lambda: settings)
        client = tmp_path / "Cliente"
        legacy = client / "GD Neoenergia" / "2600001104"
        legacy.mkdir(parents=True)

        result = resolve_archive_destination_folder(
            client,
            "2600001104",
            "2026-07-14",
            "Cliente",
            tmp_path,
        )

        assert result.destination_folder == legacy
        assert result.match_type == "protocol_folder"
        assert result.legacy_gd_ignored is False

    def test_does_not_create_entry_folder(self, tmp_path: Path):
        client = tmp_path / "Cliente"
        client.mkdir()
        resolve_archive_destination_folder(client, "2600001104", "2026-07-14", "Cliente", tmp_path)
        assert not (client / "Entrada 14-07-2026").exists()

    def test_archive_pdf_to_entry_folder_versions_existing_file(self, tmp_path: Path):
        root = tmp_path / "clientes"
        client = root / "Cliente"
        entry = client / "Entrada 14-07-2026"
        entry.mkdir(parents=True)
        pdf = tmp_path / "origem.pdf"
        pdf.write_bytes(b"pdf")
        existing = entry / "Orcamento_de_Conexao_2600001104.pdf"
        existing.write_bytes(b"old")
        match = ClientFolderMatch(
            protocol="2600001104",
            client_name="CLIENTE SINTETICO LTDA",
            matched_path=str(client),
            match_type="fuzzy_name",
            confidence=100.0,
            reason="found",
        )

        result = archive_pdf_to_client_folder(
            pdf,
            "2600001104",
            "Cliente",
            root,
            match=match,
            entry_date="2026-07-14",
        )

        assert result.success is True
        assert result.match_type == "entrada_date_folder"
        assert Path(result.archived_pdf_path).name == "Orcamento_de_Conexao_2600001104_v2.pdf"
        assert existing.read_bytes() == b"old"


class TestBuildDestinationPdfPath:
    def test_new_file(self, tmp_path: Path):
        result = build_destination_pdf_path(tmp_path, "2600001097")
        assert result.name == "Orcamento_de_Conexao_2600001097.pdf"

    def test_existing_file_gets_version(self, tmp_path: Path):
        (tmp_path / "Orcamento_de_Conexao_2600001097.pdf").touch()
        result = build_destination_pdf_path(tmp_path, "2600001097")
        assert result.name == "Orcamento_de_Conexao_2600001097_v2.pdf"

    def test_multiple_versions(self, tmp_path: Path):
        (tmp_path / "Orcamento_de_Conexao_2600001097.pdf").touch()
        (tmp_path / "Orcamento_de_Conexao_2600001097_v2.pdf").touch()
        result = build_destination_pdf_path(tmp_path, "2600001097")
        assert result.name == "Orcamento_de_Conexao_2600001097_v3.pdf"


class TestRelativeDepth:
    def test_same_level(self, tmp_path: Path):
        assert _relative_depth(tmp_path, tmp_path) == 0

    def test_one_level(self, tmp_path: Path):
        child = tmp_path / "child"
        child.mkdir()
        assert _relative_depth(tmp_path, child) == 1


class TestShouldSkipDir:
    def test_skip_known_dirs(self, tmp_path: Path):
        for name in SKIP_DIR_NAMES:
            d = tmp_path / name
            d.mkdir()
            assert _should_skip_dir(d) is True

    def test_skip_hidden(self, tmp_path: Path):
        d = tmp_path / ".hidden"
        d.mkdir()
        assert _should_skip_dir(d) is True

    def test_normal_dir_not_skipped(self, tmp_path: Path):
        d = tmp_path / "normal_folder"
        d.mkdir()
        assert _should_skip_dir(d) is False


class TestPendingFolder:
    def test_returns_pendentes_path(self, tmp_path: Path):
        result = _pending_folder(tmp_path, "123456", "Cliente Teste")
        assert "_PENDENTES_CONFERENCIA_GD" in str(result)
        assert "123456" in str(result)
        assert "Cliente Teste" in str(result)
