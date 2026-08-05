"""Interface desktop inicial, pronta para evolução sem acoplar UI à automação."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import messagebox, ttk

from automacao_gd.application.contracts import OperationResult
from automacao_gd.infrastructure.files.file_service import ensure_directories
from automacao_gd.infrastructure.logging import setup_logger
from automacao_gd.presentation.controller import ApplicationController
from automacao_gd.presentation.operational_output import format_operation_summary


@dataclass(frozen=True)
class _ResultEvent:
    result: OperationResult


@dataclass(frozen=True)
class _ConfirmationEvent:
    message: str
    completed: threading.Event
    answer: list[bool]


_DesktopEvent = _ResultEvent | _ConfirmationEvent


class AutomationDesktopApp(ttk.Frame):
    def __init__(self, master: tk.Tk, controller: ApplicationController | None = None) -> None:
        super().__init__(master, padding=16)
        self.root = master
        self.controller = controller or ApplicationController()
        self.events: queue.Queue[_DesktopEvent] = queue.Queue()
        self.buttons: list[ttk.Button] = []
        self._build_ui()
        self.after(150, self._drain_events)

    def _build_ui(self) -> None:
        self.root.title("Automação GD Neoenergia")
        self.root.geometry("920x650")
        self.root.minsize(760, 520)
        self.grid(sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)

        ttk.Label(self, text="Automação GD Neoenergia", font=("Segoe UI", 18, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        settings = self.controller.settings
        ttk.Label(
            self,
            text=f"Ambiente: {settings.APP_ENV} | DRY_RUN: {settings.DRY_RUN}",
        ).grid(row=1, column=0, sticky="w", pady=(2, 12))

        paths = ttk.LabelFrame(self, text="Configuração ativa", padding=10)
        paths.grid(row=2, column=0, sticky="ew")
        paths.columnconfigure(1, weight=1)
        for index, (label, value) in enumerate(
            [
                ("Planilha", settings.planilha_path),
                ("Clientes", settings.clientes_root_path),
                ("Downloads", settings.downloads_dir_path),
            ]
        ):
            ttk.Label(paths, text=f"{label}:").grid(row=index, column=0, sticky="nw", padx=(0, 8))
            ttk.Label(paths, text=str(value), wraplength=700).grid(row=index, column=1, sticky="w")

        actions = ttk.Frame(self)
        actions.grid(row=3, column=0, sticky="ew", pady=12)
        definitions = [
            ("Verificar ambiente", lambda: self._run("preflight", self.controller.preflight)),
            ("Inspecionar portal", self._inspect_portal),
            ("Simular processamento", lambda: self._run("simulate", lambda: self.controller.process_downloads(dry_run=True))),
            ("Aplicar processamento", self._real_processing),
            ("Executar pipeline CDP", self._pipeline),
        ]
        for index, (text, command) in enumerate(definitions):
            button = ttk.Button(actions, text=text, command=command)
            button.grid(row=index // 3, column=index % 3, padx=4, pady=4, sticky="ew")
            actions.columnconfigure(index % 3, weight=1)
            self.buttons.append(button)

        output_frame = ttk.LabelFrame(self, text="Execução e relatórios", padding=8)
        output_frame.grid(row=4, column=0, sticky="nsew")
        output_frame.rowconfigure(0, weight=1)
        output_frame.columnconfigure(0, weight=1)
        self.output = tk.Text(output_frame, wrap="word", state="disabled", font=("Consolas", 10))
        self.output.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(output_frame, orient="vertical", command=self.output.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.output.configure(yscrollcommand=scrollbar.set)

        self.status = tk.StringVar(value="Pronto")
        ttk.Label(self, textvariable=self.status).grid(row=5, column=0, sticky="w", pady=(8, 0))

    def _inspect_portal(self) -> None:
        def confirm(message: str) -> bool:
            event = threading.Event()
            answer: list[bool] = []
            self.events.put(_ConfirmationEvent(message, event, answer))
            event.wait()
            return bool(answer and answer[0])
        self._run("portal", lambda: self.controller.inspect_portal(confirm))

    def _real_processing(self) -> None:
        if not messagebox.askyesno(
            "Confirmar alterações",
            "A planilha e as pastas de clientes poderão ser alteradas. Continuar?",
        ):
            return
        self._run("real", lambda: self.controller.process_downloads(dry_run=False))

    def _pipeline(self) -> None:
        if not self.controller.settings.DRY_RUN and not messagebox.askyesno(
            "Confirmar pipeline real", "DRY_RUN=false. Confirmar execução real?"
        ):
            return
        self._run("pipeline", self.controller.run_pipeline)

    def _run(self, name: str, operation: Callable[[], OperationResult]) -> None:
        operation_names = {
            "preflight": "preflight",
            "portal": "inspect_portal",
            "simulate": "process_dry_run",
            "real": "process_real",
            "pipeline": "pipeline",
        }
        self._active_operation = operation_names.get(name, name)
        self._set_busy(True, f"Executando: {name}")
        threading.Thread(target=self._worker, args=(operation,), daemon=True).start()

    def _worker(self, operation: Callable[[], OperationResult]) -> None:
        self.events.put(_ResultEvent(operation()))

    def _drain_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if isinstance(event, _ResultEvent):
                    result = event.result
                    self._append_output(result)
                    self._set_busy(False, "Concluído" if result.success else "Falha")
                else:
                    event.answer.append(
                        messagebox.askokcancel("Login manual", event.message)
                    )
                    event.completed.set()
        except queue.Empty:
            pass
        self.after(150, self._drain_events)

    def _append_output(self, result: OperationResult) -> None:
        operation_name = getattr(self, "_active_operation", "operation")
        text = format_operation_summary(operation_name, result) + "\n\n"
        self.output.configure(state="normal")
        self.output.insert("end", text)
        self.output.see("end")
        self.output.configure(state="disabled")

    def _set_busy(self, busy: bool, status: str) -> None:
        self.status.set(status)
        state = "disabled" if busy else "normal"
        for button in self.buttons:
            button.configure(state=state)


def main() -> None:
    ensure_directories()
    setup_logger()
    root = tk.Tk()
    AutomationDesktopApp(root)
    root.mainloop()
