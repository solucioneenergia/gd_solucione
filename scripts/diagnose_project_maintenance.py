from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORT))

from automacao_gd.application.project_maintenance_inventory_service import (
    FinalArtifactWriter,
    Stage51DiagnosticError,
    build_stage51_artifact_bundle,
    capture_filesystem_snapshot,
    compare_filesystem_snapshots,
    resolve_canonical_project_root,
    run_project_maintenance_inventory,
)


OFFICIAL_WORKBOOK_SHA = "192084b5d780db5ad027aaa640ed418072cfafc4ca9b8b62648a987acc58594b"
DEFAULT_OFFICIAL_WORKBOOK = Path(r"Y:\000\Levantamento de projetos\planilha.xlsx")


def main() -> int:
    canonical_root = resolve_canonical_project_root(Path(__file__))
    if not canonical_root.is_valid:
        raise Stage51DiagnosticError("STAGE5_1_ROOT_VALIDATION_FAILED")
    root = canonical_root.resolved_path
    logs_dir = root / "data" / "logs"
    snapshot_before = capture_filesystem_snapshot(root)
    official_workbook = Path(os.environ.get("PLANILHA_PATH", str(DEFAULT_OFFICIAL_WORKBOOK)))
    external_roots = _external_roots(official_workbook)
    result = run_project_maintenance_inventory(
        root,
        official_workbook_path=official_workbook,
        official_workbook_sha=OFFICIAL_WORKBOOK_SHA,
        external_roots=external_roots,
    )
    bundle = build_stage51_artifact_bundle(result)
    allowed_new_files = [f"data/logs/{item['file_name']}" for item in bundle.artifact_manifest]
    snapshot_pre_write = capture_filesystem_snapshot(root)
    pre_write_diff = compare_filesystem_snapshots(snapshot_before, snapshot_pre_write)
    if pre_write_diff["status"] != "OK":
        raise Stage51DiagnosticError("STAGE5_1_BLOCKED — FILESYSTEM_CHANGED_BEFORE_REPORT_WRITE")
    outputs = FinalArtifactWriter(canonical_root).write(bundle, logs_dir)
    snapshot_after = capture_filesystem_snapshot(root)
    final_diff = compare_filesystem_snapshots(snapshot_before, snapshot_after, allowed_new_files=allowed_new_files)
    if final_diff["status"] != "OK":
        raise Stage51DiagnosticError("STAGE5_1_REJECTED — FILESYSTEM_CHANGED_DURING_DIAGNOSTIC")
    metrics = result["metrics"]
    print("Inventario read-only concluido")
    print(f"- Decisao: {result['decision']}")
    print(f"- Arquivos inventariados: {metrics['files_scanned']}")
    print(f"- Tamanho total: {metrics['total_size_bytes']} bytes")
    print(f"- Protegidos: {metrics['protected_files']}")
    print(f"- Duplicados: {metrics['duplicate_groups']} grupos")
    print(f"- Temporarios candidatos: {metrics['temporary_orphan_candidates']}")
    print(f"- Caches regeneraveis: {metrics['cache_files']}")
    print("- Artefatos:")
    for path in outputs.values():
        if isinstance(path, Path):
            print(f"  - {path.relative_to(root)}")
    return 0

def _external_roots(official_workbook: Path) -> list[Path]:
    roots = [official_workbook.parent]
    clientes = os.environ.get("CLIENTES_ROOT")
    roots.append(Path(clientes) if clientes else Path(r"Z:\Clientes"))
    auth = os.environ.get("AUTH_STATE_PATH")
    browser = os.environ.get("BROWSER_PROFILE_DIR")
    if auth:
        roots.append(Path(auth).parent)
    if browser:
        roots.append(Path(browser))
    return roots


if __name__ == "__main__":
    raise SystemExit(main())
