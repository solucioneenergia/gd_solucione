import re
import unicodedata

from automacao_gd.infrastructure.dates import parse_date


POINT_OF_CONNECTION_STAGE = "PONTO_DE_CONEXAO_APROVADO"
POINT_OF_CONNECTION_STAGE_LABEL = "Ponto de Conexão Aprovado"
POINT_OF_CONNECTION_NO_DATE_STATUSES = {
    "POINT_OF_CONNECTION_COMPLETED_WITHOUT_DATE",
    "POINT_OF_CONNECTION_STAGE_COMPLETED_WITHOUT_DATE",
    "POINT_OF_CONNECTION_COMPLETION_DATE_NOT_AVAILABLE",
}
_COMPLETION_DATE_RE = re.compile(
    r"Conclu[i\u00ed]do\s+em\s+(\d{2}/\d{2}/\d{4})",
    flags=re.IGNORECASE,
)
_KNOWN_TIMELINE_STAGES = {
    "AGUARDANDO DOCUMENTACAO",
    "EM ANALISE TECNICA",
    "AGUARDANDO SOLICITACAO DE VISTORIA E CONEXAO",
    "REALIZANDO VISTORIA E CONEXAO",
    "PONTO DE CONEXAO APROVADO",
    "SOLICITACAO CONCLUIDA",
}


def extract_point_of_connection_completion_from_text(
    text: str,
    *,
    protocol: str | None = None,
    source_selector: str = "text_block",
) -> dict:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if line.strip()
    ]
    base = {
        "protocol": protocol,
        "completion_date_raw": None,
        "completion_date_normalized": None,
        "completion_source_stage": POINT_OF_CONNECTION_STAGE,
        "completion_source_selector": source_selector,
    }
    stages = _find_stage_ranges(lines, "PONTO DE CONEXAO APROVADO")
    if not stages:
        return {
            **base,
            "completion_extraction_status": "POINT_OF_CONNECTION_STAGE_NOT_FOUND",
        }
    if len(stages) > 1:
        return {
            **base,
            "completion_extraction_status": "AMBIGUOUS_POINT_OF_CONNECTION_STAGE",
        }

    start, end = stages[0]
    next_stage = _next_timeline_stage_index(lines, end + 1)
    block_lines = lines[start : next_stage if next_stage is not None else len(lines)]
    dates = _COMPLETION_DATE_RE.findall("\n".join(block_lines))
    if not dates:
        return {
            **base,
            "completion_extraction_status": "POINT_OF_CONNECTION_COMPLETION_DATE_NOT_AVAILABLE",
        }
    unique_dates = list(dict.fromkeys(dates))
    if len(unique_dates) != 1:
        return {
            **base,
            "completion_extraction_status": "AMBIGUOUS_POINT_OF_CONNECTION_COMPLETION_DATE",
        }
    parsed = parse_date(unique_dates[0])
    if parsed is None:
        return {
            **base,
            "completion_extraction_status": "INVALID_POINT_OF_CONNECTION_COMPLETION_DATE",
        }
    return {
        **base,
        "completion_date_raw": unique_dates[0],
        "completion_date_normalized": parsed.isoformat(),
        "completion_extraction_status": "FOUND",
    }


def _find_stage_ranges(lines: list[str], target_normalized: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for start in range(len(lines)):
        for end in range(start, min(start + 3, len(lines))):
            if _normalize_search(" ".join(lines[start : end + 1])) == target_normalized:
                ranges.append((start, end))
                break
    return ranges


def _next_timeline_stage_index(lines: list[str], start_index: int) -> int | None:
    for index in range(start_index, len(lines)):
        for end in range(index, min(index + 3, len(lines))):
            if _normalize_search(" ".join(lines[index : end + 1])) in _KNOWN_TIMELINE_STAGES:
                return index
    return None


def _normalize_search(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.upper()
    return re.sub(r"\s+", " ", text).strip()
