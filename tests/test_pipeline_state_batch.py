from pathlib import Path
from typing import Any

from automacao_gd.domain.models import PortalSolicitation
from automacao_gd.infrastructure.state.pipeline_state import PipelineStateStore


class CountingPipelineStateStore(PipelineStateStore):
    def __init__(self, path: Path) -> None:
        self.save_calls = 0
        super().__init__(path=path, resume=False)

    def save(self) -> None:
        self.save_calls += 1
        super().save()


def test_mark_discovered_many_persists_state_once(tmp_path: Path) -> None:
    store = CountingPipelineStateStore(tmp_path / "pipeline_state.json")
    records: list[Any] = [
        PortalSolicitation(
            protocol=f"260000000{index}",
            client_name=f"Cliente {index}",
            status="Solicitação Concluída",
            page_number=1,
            row_index=index,
        )
        for index in range(3)
    ]

    entries = store.mark_discovered_many(records)

    assert len(entries) == 3
    assert store.save_calls == 1
    assert store.get_protocol("2600000000")["last_step"] == "discovered"
    assert store.get_protocol("2600000002")["page_number"] == 1

