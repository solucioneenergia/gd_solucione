import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from automacao_gd.infrastructure.files.client_folder_service import build_destination_folder, find_client_folder
from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.files.file_service import ensure_directories
from automacao_gd.infrastructure.logging import setup_logger


def client_folder_match_summary(protocol: str, client: str, settings=None) -> dict:
    settings = settings or get_settings()
    match = find_client_folder(settings.clientes_root_path, protocol, client)
    target_folder = build_destination_folder(
        settings.clientes_root_path, match, protocol, client
    )
    return {
        "protocol": protocol,
        "client": client,
        "matched_path": match.matched_path,
        "target_folder": str(target_folder),
        "match_type": match.match_type,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Testa busca de pasta de cliente.")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--client", required=True)
    args = parser.parse_args()

    ensure_directories()
    setup_logger()

    summary = client_folder_match_summary(args.protocol, args.client)
    print(f"Protocolo: {summary['protocol']}")
    print(f"Cliente: {summary['client']}")
    print(f"Pasta encontrada: {summary['matched_path'] or '-'}")
    print(f"Destino previsto: {summary['target_folder'] or '-'}")


if __name__ == "__main__":
    main()
