import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from automacao_gd.application.operational_guard import (
    INSPECT_PORTAL_OPERATION,
    DirectRouteAuthorization,
    guard_direct_route,
)
from automacao_gd.infrastructure.files.file_service import ensure_directories
from automacao_gd.infrastructure.logging import logger, setup_logger
from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.portal.factory import create_portal_automation


def main() -> int:
    print("Status: BLOQUEADO")
    print("Inspeção direta do Portal desativada nesta etapa.")
    return 2


def _legacy_inspect_portal_table(
    authorization: DirectRouteAuthorization | None = None,
) -> None:
    settings = get_settings()
    with guard_direct_route(
        settings,
        authorization,
        operation=INSPECT_PORTAL_OPERATION,
    ):
        _inspect_portal_table_locked(settings)


def _inspect_portal_table_locked(settings) -> None:
    ensure_directories()
    setup_logger()
    automation = create_portal_automation(settings)
    browser_started = False
    try:
        automation.start_browser()
        browser_started = True
        automation.open_portal()
        automation.wait_manual_login()
        automation.save_auth_state()
        records = automation.read_current_page_table()
        output_path = automation.save_table_snapshot(records)

        print(f"JSON salvo em: {output_path}")
        print("Amostra (até 5 registros):")
        for record in records[:5]:
            print(
                f"- {record.protocol} | {record.client_name or '-'} | "
                f"{record.status or '-'} | {record.entry_date or '-'}"
            )

        if settings.CDP_MODE:
            print("Conexão CDP ativa; o Edge permanecerá aberto ao encerrar.")
        input("Pressione ENTER para encerrar a inspeção.")
    except Exception as exc:
        logger.exception(f"Falha ao inspecionar tabela do portal: {exc}")
        raise
    finally:
        if browser_started:
            automation.close()


if __name__ == "__main__":
    raise SystemExit(main())
