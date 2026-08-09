import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from rapidfuzz import fuzz

from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.files.file_service import sanitize_filename
from automacao_gd.infrastructure.files.paths import (
    ensure_path_within_root,
    validate_protocol_component,
)
from automacao_gd.infrastructure.persistence.atomic import (
    atomic_copy_file,
    atomic_write_json,
)
from automacao_gd.infrastructure.logging import logger
from automacao_gd.domain.models import ArchiveResult, ClientFolderMatch


SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "env",
    ".mypy_cache",
    ".pytest_cache",
}

_FIRST_SECOND_LEVEL_CACHE: dict[str, list[Path]] = {}
_ALL_PATHS_CACHE: dict[str, list[Path]] = {}


def clear_folder_cache() -> None:
    """Invalida caches de busca de pastas de cliente.

    Deve ser chamado quando a árvore de CLIENTES_ROOT muda durante a execução,
    evitando dados obsoletos em processamentos longos.
    """
    _FIRST_SECOND_LEVEL_CACHE.clear()
    _ALL_PATHS_CACHE.clear()
    logger.debug("Caches de busca de pastas de cliente invalidados.")


@dataclass(frozen=True)
class ProtocolSearchResult:
    matched_path: Path
    hit_path: Path
    found_by: str
    reason: str


@dataclass(frozen=True)
class ArchiveDestinationResult:
    destination_folder: Path | None
    match_type: str
    confidence: float
    reason: str
    should_create_folder: bool
    fallback_mode: str
    legacy_gd_ignored: bool = False


def normalize_name(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.upper()
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def safe_folder_name(text: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", str(text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    cleaned = (cleaned or "SEM_NOME")[:120]
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if cleaned.upper() in reserved:
        cleaned = f"_{cleaned}"
    return cleaned


def find_client_folder(
    clientes_root: Path, protocol: str, client_name: str
) -> ClientFolderMatch:
    settings = get_settings()
    clientes_root = Path(clientes_root)
    started_at = time.perf_counter()
    logger.info(
        f"Buscando pasta do cliente: protocolo={protocol}, cliente='{client_name}', "
        f"raiz={clientes_root}"
    )

    if not clientes_root.exists():
        reason = f"Pasta raiz de clientes não existe: {clientes_root}"
        logger.warning(reason)
        return ClientFolderMatch(
            protocol=protocol,
            client_name=client_name,
            matched_path=str(_pending_folder(clientes_root, protocol, client_name)),
            match_type="pending_review",
            confidence=0.0,
            reason=reason,
            found_by="pending_review",
            search_elapsed_seconds=_elapsed_seconds(started_at),
        )

    cached_match = _cached_client_folder_match(
        clientes_root, protocol, client_name, started_at
    )
    if cached_match is not None:
        logger.info(
            f"Pasta do cliente reutilizada do cache: {cached_match.matched_path}"
        )
        return cached_match

    best_path, best_score = _find_by_fuzzy_name(clientes_root, client_name)
    if best_path and best_score >= settings.FUZZY_AUTO_MATCH_SCORE:
        reason = "Pasta localizada por nome aproximado do cliente"
        logger.info(f"{reason}: {best_path}; score={best_score:.1f}")
        match = ClientFolderMatch(
            protocol=protocol,
            client_name=client_name,
            matched_path=str(best_path),
            match_type="fuzzy_name",
            confidence=best_score,
            reason=reason,
            found_by="fuzzy_name",
            search_elapsed_seconds=_elapsed_seconds(started_at),
        )
        _save_client_folder_cache_match(match)
        return match

    protocol_match = _find_by_protocol(clientes_root, protocol)
    if protocol_match:
        logger.info(
            f"{protocol_match.reason}: {protocol_match.hit_path}; "
            f"destino base={protocol_match.matched_path}"
        )
        match = ClientFolderMatch(
            protocol=protocol,
            client_name=client_name,
            matched_path=str(protocol_match.matched_path),
            match_type="protocol",
            confidence=100.0,
            reason=protocol_match.reason,
            found_by=protocol_match.found_by,
            protocol_search_hit=str(protocol_match.hit_path),
            search_elapsed_seconds=_elapsed_seconds(started_at),
        )
        _save_client_folder_cache_match(match)
        return match

    if best_path and best_score >= settings.FUZZY_REVIEW_MATCH_SCORE:
        reason = (
            "Nome parecido encontrado, mas abaixo do limite automático; "
            "conferência necessária"
        )
        logger.warning(f"{reason}: {best_path}; score={best_score:.1f}")
        return ClientFolderMatch(
            protocol=protocol,
            client_name=client_name,
            matched_path=str(_pending_folder(clientes_root, protocol, client_name)),
            match_type="pending_review",
            confidence=best_score,
            reason=reason,
            found_by="pending_review",
            search_elapsed_seconds=_elapsed_seconds(started_at),
        )

    reason = "Nenhuma pasta ou arquivo encontrado por nome ou protocolo"
    logger.warning(reason)
    return ClientFolderMatch(
        protocol=protocol,
        client_name=client_name,
        matched_path=str(_pending_folder(clientes_root, protocol, client_name)),
        match_type="pending_review",
        confidence=best_score if best_path else 0.0,
        reason=reason,
        found_by="pending_review",
        search_elapsed_seconds=_elapsed_seconds(started_at),
    )


def build_destination_folder(
    clientes_root: Path,
    match: ClientFolderMatch,
    protocol: str,
    client_name: str,
    entry_date: Any = None,
) -> Path:
    destination = resolve_archive_destination_folder(
        client_folder=Path(match.matched_path) if match.matched_path else None,
        protocol=protocol,
        entry_date=entry_date,
        client_name=client_name,
        clientes_root=Path(clientes_root),
    )
    if destination.destination_folder:
        return destination.destination_folder
    return _pending_folder(Path(clientes_root), protocol, client_name)


def resolve_archive_destination_folder(
    client_folder: Path | None,
    protocol: str,
    entry_date: Any,
    client_name: str | None = None,
    clientes_root: Path | None = None,
) -> ArchiveDestinationResult:
    settings = get_settings()
    fallback_mode = settings.ARCHIVE_FALLBACK_MODE
    allow_existing_legacy_gd = (
        fallback_mode == "legacy_gd" or settings.ARCHIVE_ALLOW_EXISTING_LEGACY_GD
    )
    clientes_root = Path(clientes_root or settings.clientes_root_path)
    client_name = client_name or "SEM_NOME"

    if not client_folder:
        return _archive_fallback_destination(
            clientes_root,
            None,
            protocol,
            client_name,
            fallback_mode,
            "Pasta do cliente ausente.",
        )

    client_folder = Path(client_folder)
    if not client_folder.exists():
        return _archive_fallback_destination(
            clientes_root,
            client_folder,
            protocol,
            client_name,
            fallback_mode,
            f"Pasta do cliente nao existe: {client_folder}",
        )

    exact_entry = _find_entry_folder_by_date(client_folder, entry_date)
    if exact_entry:
        return ArchiveDestinationResult(
            destination_folder=exact_entry,
            match_type="entrada_date_folder",
            confidence=100.0,
            reason="Pasta Entrada localizada pela Data de ingresso.",
            should_create_folder=False,
            fallback_mode=fallback_mode,
        )

    legacy_self_ignored = False
    if (
        protocol
        and protocol in client_folder.name
        and _is_legacy_gd_protocol_path(client_folder, protocol)
        and not allow_existing_legacy_gd
    ):
        legacy_self_ignored = True
    elif protocol and protocol in client_folder.name:
        return ArchiveDestinationResult(
            destination_folder=client_folder,
            match_type="protocol_folder",
            confidence=100.0,
            reason="Pasta do cliente localizada ja contem o protocolo.",
            should_create_folder=False,
            fallback_mode=fallback_mode,
        )

    if not entry_date:
        entry_candidates = _entry_folder_candidates(client_folder)
        if (
            len(entry_candidates) > 1
            and settings.ARCHIVE_ENTRY_FOLDER_WHEN_MULTIPLE == "pending"
        ):
            return _archive_fallback_destination(
                clientes_root,
                client_folder,
                protocol,
                client_name,
                fallback_mode,
                "Varias pastas Entrada localizadas e a configuracao exige pendencia.",
            )
        entry_candidate = _find_best_existing_entry_folder(client_folder)
        if entry_candidate:
            folder, reason, confidence = entry_candidate
            return ArchiveDestinationResult(
                destination_folder=folder,
                match_type="entrada_date_folder",
                confidence=confidence,
                reason=reason,
                should_create_folder=False,
                fallback_mode=fallback_mode,
            )

    protocol_match, legacy_gd_ignored = _find_protocol_path_inside_client_folder(
        client_folder,
        protocol,
        allow_existing_legacy_gd,
    )
    if protocol_match:
        match_type = (
            "protocol_folder" if protocol_match.hit_path.is_dir() else "protocol_file_parent"
        )
        return ArchiveDestinationResult(
            destination_folder=protocol_match.matched_path,
            match_type=match_type,
            confidence=100.0,
            reason=protocol_match.reason,
            should_create_folder=False,
            fallback_mode=fallback_mode,
            legacy_gd_ignored=legacy_gd_ignored,
        )

    return _archive_fallback_destination(
        clientes_root,
        client_folder,
        protocol,
        client_name,
        fallback_mode,
        "Nenhuma pasta Entrada ou caminho com protocolo encontrado.",
        legacy_gd_ignored=legacy_self_ignored or legacy_gd_ignored,
    )


def build_destination_pdf_path(destination_folder: Path, protocol: str) -> Path:
    return _next_available_pdf_path(Path(destination_folder), protocol)


def _find_entry_folder_by_date(client_folder: Path, entry_date: Any) -> Path | None:
    parsed_date = _parse_entry_date(entry_date)
    if not parsed_date:
        return None
    target = parsed_date.strftime("%d-%m-%Y")
    accepted = {
        f"ENTRADA {target}",
        f"ENTRADA {target.replace('-', '_')}",
        f"ENTRADA {target.replace('-', '.')}",
    }
    for folder in _iter_folders(client_folder):
        normalized = re.sub(r"\s+", " ", folder.name.strip().upper())
        if normalized in accepted:
            return folder
    return None


def _find_best_existing_entry_folder(client_folder: Path) -> tuple[Path, str, float] | None:
    settings = get_settings()
    candidates = _entry_folder_candidates(client_folder)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if len(candidates) == 1:
        return candidates[0][2], "Unica pasta Entrada existente localizada.", 95.0
    if settings.ARCHIVE_ENTRY_FOLDER_WHEN_MULTIPLE == "latest":
        return (
            candidates[0][2],
            "Varias pastas Entrada localizadas; usando a mais recente.",
            80.0,
        )
    return None


def _entry_folder_candidates(client_folder: Path) -> list[tuple[date, str, Path]]:
    candidates: list[tuple[date, str, Path]] = []
    for folder in _iter_folders(client_folder):
        parsed = _parse_entry_folder_date(folder.name)
        if parsed:
            candidates.append((parsed, folder.name.lower(), folder))
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates


def _find_protocol_path_inside_client_folder(
    client_folder: Path,
    protocol: str,
    allow_existing_legacy_gd: bool,
) -> tuple[ProtocolSearchResult | None, bool]:
    candidates: list[tuple[int, int, str, ProtocolSearchResult]] = []
    legacy_gd_ignored = False
    for path in _iter_all_paths(client_folder):
        if protocol not in path.name:
            continue
        if (
            _is_legacy_gd_protocol_path(path, protocol)
            and not allow_existing_legacy_gd
        ):
            legacy_gd_ignored = True
            continue
        if path.is_dir():
            candidates.append(
                (
                    0,
                    _relative_depth(client_folder, path),
                    str(path).lower(),
                    ProtocolSearchResult(
                        matched_path=path,
                        hit_path=path,
                        found_by="protocol_folder",
                        reason="Pasta existente contendo o protocolo localizada dentro da pasta do cliente.",
                    ),
                )
            )
        else:
            candidates.append(
                (
                    1,
                    _relative_depth(client_folder, path.parent),
                    str(path).lower(),
                    ProtocolSearchResult(
                        matched_path=path.parent,
                        hit_path=path,
                        found_by="protocol_file_parent",
                        reason="Arquivo contendo o protocolo localizado; usando pasta pai.",
                    ),
                )
            )
    if not candidates:
        return None, legacy_gd_ignored
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][3], legacy_gd_ignored


def _is_legacy_gd_protocol_path(path: Path, protocol: str) -> bool:
    normalized_parts = [normalize_name(part) for part in Path(path).parts]
    normalized_gd = normalize_name("GD Neoenergia")
    protocol_text = str(protocol)
    for index, part in enumerate(normalized_parts[:-1]):
        if part == normalized_gd and normalized_parts[index + 1] == protocol_text:
            return True
    return False


def _archive_fallback_destination(
    clientes_root: Path,
    client_folder: Path | None,
    protocol: str,
    client_name: str,
    fallback_mode: str,
    reason: str,
    legacy_gd_ignored: bool = False,
) -> ArchiveDestinationResult:
    if fallback_mode == "legacy_gd" and client_folder and client_folder.exists():
        return ArchiveDestinationResult(
            destination_folder=client_folder / "GD Neoenergia" / protocol,
            match_type="legacy_gd_folder",
            confidence=50.0,
            reason=f"{reason} Fallback legacy_gd habilitado.",
            should_create_folder=True,
            fallback_mode=fallback_mode,
            legacy_gd_ignored=legacy_gd_ignored,
        )
    if fallback_mode == "disabled":
        return ArchiveDestinationResult(
            destination_folder=None,
            match_type="pending_manual_review",
            confidence=0.0,
            reason=f"{reason} Arquivamento desabilitado sem destino seguro.",
            should_create_folder=False,
            fallback_mode=fallback_mode,
            legacy_gd_ignored=legacy_gd_ignored,
        )
    return ArchiveDestinationResult(
        destination_folder=_pending_folder(clientes_root, protocol, client_name),
        match_type="pending_manual_review",
        confidence=0.0,
        reason=f"{reason} Enviado para pendencia.",
        should_create_folder=True,
        fallback_mode=fallback_mode,
        legacy_gd_ignored=legacy_gd_ignored,
    )


def _iter_folders(root: Path):
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name.lower())
    except (OSError, PermissionError) as exc:
        logger.warning(f"Sem permissao para listar '{root}': {exc}")
        return
    for child in children:
        if child.is_dir() and not _should_skip_dir(child):
            yield child


def _parse_entry_folder_date(name: str) -> date | None:
    match = re.search(r"\bentrada\s+(\d{2})[-_.](\d{2})[-_.](\d{4})\b", name, re.I)
    if not match:
        return None
    day, month, year = map(int, match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_entry_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def archive_pdf_to_client_folder(
    pdf_path: Path,
    protocol: str,
    client_name: str,
    clientes_root: Path,
    match: ClientFolderMatch | None = None,
    entry_date: Any = None,
) -> ArchiveResult:
    pdf_path = Path(pdf_path)
    clientes_root = Path(clientes_root)
    logger.info(f"Arquivando PDF '{pdf_path}' para protocolo {protocol}.")

    try:
        protocol = validate_protocol_component(protocol)
        if not pdf_path.exists() or not pdf_path.is_file():
            raise FileNotFoundError(f"PDF não encontrado: {pdf_path}")
        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError(f"Arquivo de origem não é PDF: {pdf_path}")
        max_bytes = int(getattr(get_settings(), "MAX_PDF_SIZE_MB", 25)) * 1024 * 1024
        if pdf_path.stat().st_size > max_bytes:
            raise ValueError(f"PDF excede o limite configurado de {max_bytes // (1024 * 1024)} MB.")

        match = match or find_client_folder(clientes_root, protocol, client_name)
        destination = resolve_archive_destination_folder(
            client_folder=Path(match.matched_path) if match.matched_path else None,
            protocol=protocol,
            entry_date=entry_date,
            client_name=client_name,
            clientes_root=clientes_root,
        )
        if not destination.destination_folder:
            raise RuntimeError(destination.reason)
        destination_folder = ensure_path_within_root(
            clientes_root, destination.destination_folder
        )
        created_folder = False
        if not destination_folder.exists():
            created_folder = True
        destination_folder.mkdir(parents=True, exist_ok=True)
        destination_file = build_destination_pdf_path(destination_folder, protocol)

        atomic_copy_file(pdf_path, destination_file, private=True)
        logger.info(
            f"PDF arquivado em '{destination_file}' "
            f"(match_type={destination.match_type}, confidence={destination.confidence})."
        )
        return ArchiveResult(
            original_pdf_path=str(pdf_path),
            archived_pdf_path=str(destination_file),
            match_type=destination.match_type,
            confidence=destination.confidence,
            success=True,
            error=None,
            destination_folder=str(destination_folder),
            reason=destination.reason,
            should_create_folder=destination.should_create_folder,
            fallback_mode=destination.fallback_mode,
            created_folder=created_folder,
            legacy_gd_ignored=destination.legacy_gd_ignored,
        )
    except Exception as exc:
        logger.exception(f"Falha ao arquivar PDF: {exc}")
        return ArchiveResult(
            original_pdf_path=str(pdf_path),
            archived_pdf_path="",
            match_type="error",
            confidence=None,
            success=False,
            error=str(exc),
            fallback_mode=get_settings().ARCHIVE_FALLBACK_MODE,
        )


def _find_by_protocol(clientes_root: Path, protocol: str) -> ProtocolSearchResult | None:
    candidates: list[tuple[int, int, str, ProtocolSearchResult]] = []
    for path in _iter_all_paths(clientes_root):
        if protocol not in path.name:
            continue

        if path.is_dir():
            relative_depth = _relative_depth(clientes_root, path)
            reason = (
                "Pasta localizada pelo protocolo no nome da pasta"
                if relative_depth <= 1
                else "Subpasta localizada pelo protocolo"
            )
            candidates.append(
                (
                    0,
                    relative_depth,
                    str(path).lower(),
                    ProtocolSearchResult(
                        matched_path=path,
                        hit_path=path,
                        found_by="protocol_folder",
                        reason=reason,
                    ),
                )
            )
        else:
            candidates.append(
                (
                    1,
                    _relative_depth(clientes_root, path.parent),
                    str(path).lower(),
                    ProtocolSearchResult(
                        matched_path=path.parent,
                        hit_path=path,
                        found_by="protocol_file",
                        reason=(
                            "Arquivo localizado pelo protocolo; "
                            "usando pasta pai do arquivo"
                        ),
                    ),
                )
            )

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][3]


def _find_by_fuzzy_name(clientes_root: Path, client_name: str) -> tuple[Path | None, float]:
    target = normalize_name(client_name)
    if not target:
        return None, 0.0

    best_path: Path | None = None
    best_score = 0.0
    for folder in _iter_first_and_second_level_folders(clientes_root):
        candidate = normalize_name(folder.name)
        score = float(fuzz.token_set_ratio(target, candidate))
        if score > best_score:
            best_path = folder
            best_score = score

    return best_path, best_score


def _iter_all_paths(root: Path):
    cache_key = str(root.resolve())
    if cache_key in _ALL_PATHS_CACHE:
        yield from _ALL_PATHS_CACHE[cache_key]
        return

    paths: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir(), key=lambda path: path.name.lower())
        except (OSError, PermissionError) as exc:
            logger.warning(f"Sem permissão para listar '{current}': {exc}")
            continue

        for child in children:
            if child.is_dir():
                if _should_skip_dir(child):
                    continue
                paths.append(child)
                yield child
                stack.append(child)
            else:
                paths.append(child)
                yield child

    _ALL_PATHS_CACHE[cache_key] = paths


def _iter_first_and_second_level_folders(root: Path):
    cache_key = str(root.resolve())
    if cache_key in _FIRST_SECOND_LEVEL_CACHE:
        yield from _FIRST_SECOND_LEVEL_CACHE[cache_key]
        return

    folders: list[Path] = []
    try:
        first_level = [path for path in root.iterdir() if path.is_dir()]
    except (OSError, PermissionError) as exc:
        logger.warning(f"Sem permissão para listar '{root}': {exc}")
        return

    for folder in first_level:
        if folder.name.startswith("_") or _should_skip_dir(folder):
            continue
        folders.append(folder)
        yield folder
        try:
            for child in folder.iterdir():
                if child.is_dir() and not child.name.startswith("_") and not _should_skip_dir(child):
                    folders.append(child)
                    yield child
        except (OSError, PermissionError) as exc:
            logger.warning(f"Sem permissão para listar subpastas de '{folder}': {exc}")

    _FIRST_SECOND_LEVEL_CACHE[cache_key] = folders


def _next_available_pdf_path(destination_folder: Path, protocol: str) -> Path:
    protocol = validate_protocol_component(protocol)
    base_name = sanitize_filename(f"Orcamento_de_Conexao_{protocol}")
    candidate = destination_folder / f"{base_name}.pdf"
    if not candidate.exists():
        return candidate

    version = 2
    while True:
        versioned = destination_folder / f"{base_name}_v{version}.pdf"
        if not versioned.exists():
            return versioned
        version += 1


def _pending_folder(clientes_root: Path, protocol: str, client_name: str) -> Path:
    safe_protocol = validate_protocol_component(protocol)
    pending_name = safe_folder_name(f"{safe_protocol} - {client_name}")
    return ensure_path_within_root(
        Path(clientes_root),
        Path(clientes_root) / "_PENDENTES_CONFERENCIA_GD" / pending_name,
    )


def _relative_depth(root: Path, path: Path) -> int:
    try:
        return len(path.relative_to(root).parts)
    except ValueError:
        return len(path.parts)


def _should_skip_dir(path: Path) -> bool:
    name = path.name
    if name in SKIP_DIR_NAMES or name.startswith("."):
        return True
    try:
        return bool(getattr(path.stat(), "st_file_attributes", 0) & 0x2)
    except OSError:
        return False


def _elapsed_seconds(started_at: float) -> float:
    return round(time.perf_counter() - started_at, 3)


def _cached_client_folder_match(
    clientes_root: Path, protocol: str, client_name: str, started_at: float
) -> ClientFolderMatch | None:
    settings = get_settings()
    if not settings.CACHE_CLIENT_FOLDER_LOOKUP:
        return None

    cache = _load_client_folder_cache()
    candidates: list[tuple[str, dict]] = []
    protocol_entry = cache.get(protocol)
    if isinstance(protocol_entry, dict):
        candidates.append((protocol, protocol_entry))

    normalized_client = normalize_name(client_name)
    for key, entry in cache.items():
        if key == protocol or not isinstance(entry, dict):
            continue
        if normalize_name(entry.get("client_name", "")) == normalized_client:
            candidates.append((key, entry))

    ttl_days = int(getattr(settings, "CLIENT_FOLDER_CACHE_TTL_DAYS", 30))
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    for cache_key, entry in candidates:
        matched_path = entry.get("matched_path")
        updated_at = entry.get("updated_at")
        if updated_at:
            try:
                parsed = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                if parsed < cutoff:
                    continue
            except ValueError:
                continue
        if matched_path and Path(matched_path).exists():
            try:
                ensure_path_within_root(clientes_root, Path(matched_path))
            except ValueError:
                logger.warning("Entrada de cache fora da raiz de clientes foi ignorada.")
                continue
            raw_match_type = entry.get("match_type")
            match_type: Literal["protocol", "fuzzy_name"] = (
                "protocol" if raw_match_type == "protocol" else "fuzzy_name"
            )
            return ClientFolderMatch(
                protocol=protocol,
                client_name=client_name,
                matched_path=matched_path,
                match_type=match_type,
                confidence=entry.get("confidence"),
                reason="Pasta localizada no cache de clientes",
                found_by="client_folder_cache",
                search_elapsed_seconds=_elapsed_seconds(started_at),
                cache_hit=True,
                cache_key=cache_key,
            )

    return None


def _save_client_folder_cache_match(match: ClientFolderMatch) -> None:
    settings = get_settings()
    if not settings.CACHE_CLIENT_FOLDER_LOOKUP:
        return
    if match.match_type not in {"protocol", "fuzzy_name"} or not match.matched_path:
        return
    if not Path(match.matched_path).exists():
        return

    cache = _load_client_folder_cache()
    cache[match.protocol] = {
        "client_name": match.client_name,
        "matched_path": match.matched_path,
        "match_type": match.match_type,
        "confidence": match.confidence,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    max_entries = int(getattr(settings, "CLIENT_FOLDER_CACHE_MAX_ENTRIES", 5000))
    if len(cache) > max_entries:
        ordered = sorted(
            cache.items(),
            key=lambda item: str(item[1].get("updated_at", "")) if isinstance(item[1], dict) else "",
            reverse=True,
        )
        cache = dict(ordered[:max_entries])
    _save_client_folder_cache(cache)


def _load_client_folder_cache() -> dict:
    path = get_settings().client_folder_cache_path
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning(f"Nao foi possivel ler cache de pastas '{path}': {exc}")
        return {}


def _save_client_folder_cache(cache: dict) -> None:
    path = get_settings().client_folder_cache_path
    atomic_write_json(path, cache, private=True)
