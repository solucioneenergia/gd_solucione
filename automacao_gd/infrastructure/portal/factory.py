from __future__ import annotations

from automacao_gd.infrastructure.config import Settings
from automacao_gd.infrastructure.logging import logger
from automacao_gd.infrastructure.portal.browser import (
    PersistentBrowserPortalGDAutomation,
)
from automacao_gd.infrastructure.portal.cdp_browser import CDPPortalGDAutomation


def create_portal_automation(settings: Settings):
    logger.info(
        "Configuração efetiva: "
        f"CDP_MODE={str(settings.CDP_MODE).lower()}; "
        f"CDP_ENDPOINT={settings.CDP_ENDPOINT}"
    )
    if settings.CDP_MODE:
        return CDPPortalGDAutomation(settings)
    return PersistentBrowserPortalGDAutomation(settings)
