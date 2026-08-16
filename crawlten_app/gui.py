"""Discovery-first Tkinter interface for CrawlTen."""
from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .discovery import (
    DiscoveryService,
    SearchResult,
    download_selected,
    export_results,
)


class CrawlTenGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("CrawlTen 1.0 — Research Discovery")
        root.geometry("1080x650")
        root.minsize(860, 500)
        self.events: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.stop_event = threading.Event()
        self.active_service: DiscoveryService | None = None
        self.download_dir = Path.home() / "Downloads" / "CrawlTen"
        self.results: list[SearchResult] = []
        self.result_by_id: dict[str, SearchResult] = {}
        self.file_types = {
            name: tk.BooleanVar(value=name in {"PDF", "DOCX"})
            for name in ("PDF", "DOCX", "MP3", "MPEG")
        }
        self.max_results = tk.IntVar(value=50)
        self._build()
        root.after(100, self._drain_events)

    def _build(self):
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(5, weight=1)

        ttk.Label(outer, text="Search Pattern:").grid(row=0, column=0, sticky="w")
        self.query = ttk.Entry(outer)
        self.query.grid(row=0, column=1, columnspan=5, sticky="ew", pady=4)

        ttk.Label(outer, text="Start URL (optional):").grid(row=1, column=0, sticky="w")
        self.website = ttk.Entry(outer)
        self.website.grid(row=1, column=1, columnspan=5, sticky="ew", pady=4)
        for entry in (self.query, self.website):
            entry.bind("<Control-a>", self._select_all)
            entry.bind("<Control-A>", self._select_all)
            entry.bind("<Control-v>", self._paste)
            entry.bind("<Control-V>", self._paste)
            entry.bind("<Shift-Insert>", self._paste)

        options = ttk.Frame(outer)
        options.grid(row=2, column=0, columnspan=6, sticky="ew", pady=8)
        ttk.Label(options, text="File types:").pack(side="left")
        for name, variable in self.file_types.items():
            ttk.Checkbutton(options, text=name, variable=variable).pack(
                side="left", padx=(8, 0))
        ttk.Label(options, text="Maximum results:").pack(side="left", padx=(24, 5))
        ttk.Spinbox(options, from_=1, to=200, width=6,
                    textvariable=self.max_results).pack(side="left")

        controls = ttk.Frame(outer)
        controls.grid(row=3, column=0, columnspan=6, sticky="ew", pady=6)
        self.search_button = ttk.Button(
            controls, text="Search", command=self.start_search)
        self.search_button.pack(side="left", padx=(0, 6))
        self.stop_button = ttk.Button(
            controls, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=6)
        ttk.Button(controls, text="Clear results", command=self.clear).pack(
            side="left", padx=6)
        ttk.Button(controls, text="Open selected", command=self.open_selected).pack(
            side="right", padx=6)
        ttk.Button(controls, text="Download selected",
                   command=self.start_download).pack(side="right", padx=6)
        ttk.Button(controls, text="Export results",
                   command=self.export).pack(side="right", padx=6)

        folder = ttk.Frame(outer)
        folder.grid(row=4, column=0, columnspan=6, sticky="ew", pady=4)
        ttk.Button(folder, text="Download folder", command=self.select_folder).pack(
            side="left")
        self.folder_label = ttk.Label(folder, text=str(self.download_dir))
        self.folder_label.pack(side="left", padx=10)

        columns = ("title", "type", "source", "size", "access", "url")
        table_frame = ttk.Frame(outer)
        table_frame.grid(row=5, column=0, columnspan=6, sticky="nsew", pady=8)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(
            table_frame, columns=columns, show="headings", selectmode="extended")
        widths = {"title": 300, "type": 60, "source": 170,
                  "size": 80, "access": 120, "url": 350}
        for column in columns:
            self.table.heading(column, text=column.title())
            self.table.column(column, width=widths[column], minwidth=50)
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.bind("<Double-1>", lambda _event: self.open_selected())

        bottom = ttk.Frame(outer)
        bottom.grid(row=6, column=0, columnspan=6, sticky="ew")
        self.progress = ttk.Progressbar(bottom, mode="indeterminate")
        self.progress.pack(fill="x")
        self.status = ttk.Label(bottom, text="Ready")
        self.status.pack(anchor="w", pady=(6, 0))

    def selected_types(self) -> frozenset[str]:
        return frozenset(
            name.lower() for name, value in self.file_types.items() if value.get())

    def start_search(self):
        types = self.selected_types()
        if not types:
            messagebox.showwarning("CrawlTen", "Select at least one file type.")
            return
        self.clear()
        self._busy(True, "Searching public sources…")
        service = DiscoveryService(
            types, self.max_results.get(), self._emit, self.stop_event)
        self.active_service = service
        threading.Thread(
            target=self._run_search,
            args=(service, self.query.get(), self.website.get()),
            daemon=True,
        ).start()

    def _run_search(self, service, query, website):
        try:
            service.search(query, website)
        except Exception as exc:
            if self.stop_event.is_set():
                self._emit("cancelled", {})
            else:
                logging.exception("Search failed")
                self._emit("error", {"message": str(exc)})
        finally:
            self.active_service = None

    def start_download(self):
        selected = self._selected_results()
        if not selected:
            messagebox.showinfo("CrawlTen", "Select one or more results first.")
            return
        self._busy(True, f"Downloading {len(selected)} selected file(s)…")
        threading.Thread(
            target=self._run_download, args=(selected,), daemon=True).start()

    def _run_download(self, selected):
        try:
            stats = download_selected(
                selected, self.download_dir, self.selected_types(),
                self._emit, self.stop_event)
            self._emit("download_complete", {"stats": stats})
        except Exception as exc:
            if self.stop_event.is_set():
                self._emit("cancelled", {})
            else:
                logging.exception("Download failed")
                self._emit("error", {"message": str(exc)})

    def _selected_results(self):
        return [
            self.result_by_id[item]
            for item in self.table.selection()
            if item in self.result_by_id
        ]

    def open_selected(self):
        for result in self._selected_results():
            webbrowser.open_new_tab(result.url)

    def export(self):
        if not self.results:
            messagebox.showinfo("CrawlTen", "There are no results to export.")
            return
        destination = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV file", "*.csv")],
            initialfile="crawlten-results.csv",
        )
        if destination:
            export_results(self.results, Path(destination))
            self.status.config(text=f"Exported {len(self.results)} results.")

    def select_folder(self):
        selected = filedialog.askdirectory(initialdir=self.download_dir.parent)
        if selected:
            self.download_dir = Path(selected)
            self.folder_label.config(text=selected)

    def clear(self):
        for item in self.table.get_children():
            self.table.delete(item)
        self.results.clear()
        self.result_by_id.clear()
        self.status.config(text="Ready")

    def stop(self):
        self.stop_event.set()
        if self.active_service is not None:
            self.active_service.cancel()
        self.status.config(text="Stopping safely…")

    @staticmethod
    def _select_all(event):
        event.widget.selection_range(0, tk.END)
        event.widget.icursor(tk.END)
        return "break"

    @staticmethod
    def _paste(event):
        widget = event.widget
        try:
            text = widget.clipboard_get()
        except tk.TclError:
            return "break"
        if widget.selection_present():
            widget.delete(tk.SEL_FIRST, tk.SEL_LAST)
        widget.insert(tk.INSERT, text)
        return "break"

    def _busy(self, active, message="Ready"):
        self.stop_event.clear() if active else None
        self.search_button.config(state="disabled" if active else "normal")
        self.stop_button.config(state="normal" if active else "disabled")
        self.progress.start(10) if active else self.progress.stop()
        self.status.config(text=message)

    def _emit(self, event, data):
        self.events.put((event, data))

    def _drain_events(self):
        try:
            while True:
                event, data = self.events.get_nowait()
                if event == "result":
                    result = data["result"]
                    item = self.table.insert("", "end", values=(
                        result.title, result.file_type, result.source,
                        result.size_label, result.access, result.url))
                    self.results.append(result)
                    self.result_by_id[item] = result
                    self.status.config(text=f"Found {len(self.results)} result(s)…")
                elif event == "status":
                    self.status.config(text=data["message"])
                elif event == "search_complete":
                    self._busy(False, f"Search complete — {data['count']} result(s).")
                elif event == "download_complete":
                    stats = data["stats"]
                    self._busy(
                        False,
                        f"Download complete — {stats.downloaded} downloaded, "
                        f"{stats.blocked} blocked, {stats.failed} failed.",
                    )
                elif event == "error":
                    self._busy(False, f"Error: {data['message']}")
                elif event == "cancelled":
                    self._busy(False, "Operation stopped by user.")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    root = tk.Tk()
    CrawlTenGUI(root)
    root.mainloop()
