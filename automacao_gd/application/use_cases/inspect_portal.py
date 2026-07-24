from __future__ import annotations

from collections.abc import Callable

from automacao_gd.infrastructure.config import Settings, get_settings
from automacao_gd.infrastructure.portal.factory import create_portal_automation


class InspectPortalUseCase:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def execute(self, confirm_login: Callable[[str], bool] | None = None) -> dict:
        automation = create_portal_automation(self.settings)
        try:
            automation.start_browser()
            automation.open_portal()
            message = (
                "Faça o login manualmente, abra 'Minhas Solicitações' e confirme "
                "para continuar."
            )
            if confirm_login is None:
                automation.wait_manual_login()
            elif not confirm_login(message):
                return {"cancelled": True, "records": [], "snapshot_path": None}
            automation.save_auth_state()
            records = automation.read_current_page_table()
            snapshot = automation.save_table_snapshot(records)
            return {
                "cancelled": False,
                "records": [record.model_dump(mode="json") for record in records],
                "snapshot_path": str(snapshot),
            }
        finally:
            automation.close()
