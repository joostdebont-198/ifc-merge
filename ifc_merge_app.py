"""
IFC Merge  -  drag-and-drop desktop app to merge several IFC models into one.

Run:   python ifc_merge_app.py
Build: see build_exe.bat / README.md
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from merge_core import MergeOptions, merge

# optional: real drag and drop from Explorer/Finder
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _HAS_DND = True
except Exception:  # pragma: no cover
    _HAS_DND = False

APP_TITLE = "IFC Merge"
IFC_EXT = (".ifc", ".ifczip", ".ifcxml")


class App:
    def __init__(self) -> None:
        self.root = TkinterDnD.Tk() if _HAS_DND else tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("760x640")
        self.root.minsize(640, 520)
        self.files: list[str] = []
        self.q: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.running = False
        self._build()
        self.root.after(100, self._poll)

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        pad = {"padx": 10, "pady": 4}
        root = self.root

        # --- file list / drop zone ------------------------------------------
        top = ttk.LabelFrame(root, text=" 1. Models to merge  (first one = main model: its units, schema and origin win) ")
        top.pack(fill="both", expand=False, **pad)

        self.listbox = tk.Listbox(top, height=8, selectmode="extended", activestyle="none")
        self.listbox.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        sb = ttk.Scrollbar(top, command=self.listbox.yview)
        sb.pack(side="left", fill="y", pady=8)
        self.listbox.config(yscrollcommand=sb.set)
        self._show_placeholder()

        btns = ttk.Frame(top)
        btns.pack(side="left", fill="y", padx=8, pady=8)
        ttk.Button(btns, text="Add files...", command=self.add_files).pack(fill="x", pady=2)
        ttk.Button(btns, text="Remove", command=self.remove_selected).pack(fill="x", pady=2)
        ttk.Button(btns, text="Move up", command=lambda: self.move(-1)).pack(fill="x", pady=2)
        ttk.Button(btns, text="Move down", command=lambda: self.move(1)).pack(fill="x", pady=2)
        ttk.Button(btns, text="Clear", command=self.clear).pack(fill="x", pady=(14, 2))

        if _HAS_DND:
            for w in (self.listbox, top):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)

        # --- options -----------------------------------------------------------
        opts = ttk.LabelFrame(root, text=" 2. Options ")
        opts.pack(fill="x", **pad)

        self.v_sites = tk.BooleanVar(value=True)
        self.v_buildings = tk.BooleanVar(value=True)
        self.v_storeys = tk.BooleanVar(value=True)
        self.v_tag = tk.BooleanVar(value=True)
        self.v_tol = tk.StringVar(value="0.30")
        self.v_schema = tk.StringVar(value="(first model)")
        self.v_project = tk.StringVar(value="")

        row = ttk.Frame(opts)
        row.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(row, text="Combine elements with the same name:").pack(side="left")
        ttk.Checkbutton(row, text="sites", variable=self.v_sites).pack(side="left", padx=6)
        ttk.Checkbutton(row, text="buildings", variable=self.v_buildings).pack(side="left", padx=6)
        ttk.Checkbutton(row, text="storeys", variable=self.v_storeys).pack(side="left", padx=6)
        ttk.Label(row, text="   storey elevation tolerance (m):").pack(side="left")
        ttk.Entry(row, textvariable=self.v_tol, width=6).pack(side="left", padx=4)

        row2 = ttk.Frame(opts)
        row2.pack(fill="x", padx=8, pady=(4, 0))
        ttk.Checkbutton(row2, text="Tag every element with its source file (Pset_MergeSource)",
                        variable=self.v_tag).pack(side="left")

        row3 = ttk.Frame(opts)
        row3.pack(fill="x", padx=8, pady=(4, 8))
        ttk.Label(row3, text="Target IFC schema:").pack(side="left")
        ttk.Combobox(row3, textvariable=self.v_schema, width=14, state="readonly",
                     values=["(first model)", "IFC2X3", "IFC4", "IFC4X3"]).pack(side="left", padx=4)
        ttk.Label(row3, text="   Project name (optional):").pack(side="left")
        ttk.Entry(row3, textvariable=self.v_project, width=28).pack(side="left", padx=4)

        # --- output ------------------------------------------------------------
        out = ttk.LabelFrame(root, text=" 3. Output ")
        out.pack(fill="x", **pad)
        row4 = ttk.Frame(out)
        row4.pack(fill="x", padx=8, pady=8)
        self.v_out = tk.StringVar(value="")
        ttk.Entry(row4, textvariable=self.v_out).pack(side="left", fill="x", expand=True)
        ttk.Button(row4, text="Browse...", command=self.choose_output).pack(side="left", padx=(6, 0))

        # --- run + log -----------------------------------------------------------
        run = ttk.Frame(root)
        run.pack(fill="x", **pad)
        self.btn_merge = ttk.Button(run, text="Merge", command=self.start, width=18)
        self.btn_merge.pack(side="left")
        self.progress = ttk.Progressbar(run, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=10)
        self.btn_open = ttk.Button(run, text="Open output folder", command=self.open_folder, state="disabled")
        self.btn_open.pack(side="left")

        logf = ttk.LabelFrame(root, text=" Log ")
        logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, height=12, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        sb2 = ttk.Scrollbar(logf, command=self.log.yview)
        sb2.pack(side="left", fill="y", pady=8)
        self.log.config(yscrollcommand=sb2.set)
        self.log.tag_config("warn", foreground="#b35c00")
        self.log.tag_config("err", foreground="#b00020")
        self.log.tag_config("ok", foreground="#1b7f3b")

        hint = "Drop IFC files here or use 'Add files...'." if _HAS_DND else \
            "Use 'Add files...' (install tkinterdnd2 for drag-and-drop)."
        self._log(hint)

    # ------------------------------------------------------------------ files
    def _show_placeholder(self) -> None:
        self.listbox.delete(0, "end")
        if not self.files:
            self.listbox.insert("end", "   <- drop .ifc files here")
            self.listbox.itemconfig(0, fg="grey")
        else:
            for i, p in enumerate(self.files, 1):
                self.listbox.insert("end", f"{i}.  {os.path.basename(p)}     ({os.path.dirname(p)})")

    def _add_paths(self, paths) -> None:
        for p in paths:
            p = os.path.normpath(p)
            if os.path.isdir(p):
                self._add_paths(sorted(os.path.join(p, x) for x in os.listdir(p)))
                continue
            if p.lower().endswith(IFC_EXT) and p not in self.files:
                self.files.append(p)
        self._show_placeholder()
        if self.files and not self.v_out.get():
            first = self.files[0]
            self.v_out.set(os.path.join(os.path.dirname(first), "merged.ifc"))

    def _on_drop(self, event) -> None:
        paths = self.root.tk.splitlist(event.data)
        self._add_paths(paths)

    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(title="Select IFC files",
                                            filetypes=[("IFC files", "*.ifc *.ifczip *.ifcxml"), ("All", "*.*")])
        if paths:
            self._add_paths(paths)

    def remove_selected(self) -> None:
        sel = sorted(self.listbox.curselection(), reverse=True)
        for i in sel:
            if i < len(self.files):
                del self.files[i]
        self._show_placeholder()

    def move(self, d: int) -> None:
        sel = list(self.listbox.curselection())
        if len(sel) != 1 or not self.files:
            return
        i = sel[0]
        j = i + d
        if 0 <= j < len(self.files):
            self.files[i], self.files[j] = self.files[j], self.files[i]
            self._show_placeholder()
            self.listbox.selection_set(j)

    def clear(self) -> None:
        self.files = []
        self._show_placeholder()

    def choose_output(self) -> None:
        init = self.v_out.get() or "merged.ifc"
        p = filedialog.asksaveasfilename(title="Save merged IFC as", defaultextension=".ifc",
                                         initialfile=os.path.basename(init),
                                         initialdir=os.path.dirname(init) or None,
                                         filetypes=[("IFC file", "*.ifc")])
        if p:
            self.v_out.set(p)

    def open_folder(self) -> None:
        folder = os.path.dirname(self.v_out.get())
        if not os.path.isdir(folder):
            return
        if sys.platform.startswith("win"):
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])

    # ------------------------------------------------------------------ run
    def _options(self) -> MergeOptions:
        try:
            tol = float(self.v_tol.get().replace(",", "."))
        except ValueError:
            tol = 0.3
        schema = self.v_schema.get()
        return MergeOptions(
            target_schema=None if schema.startswith("(") else schema,
            combine_sites=self.v_sites.get(),
            combine_buildings=self.v_buildings.get(),
            combine_storeys=self.v_storeys.get(),
            storey_tolerance_m=tol,
            tag_source=self.v_tag.get(),
            project_name=self.v_project.get().strip() or None,
        )

    def start(self) -> None:
        if self.running:
            return
        if len(self.files) < 2:
            messagebox.showwarning(APP_TITLE, "Add at least two IFC files.")
            return
        out = self.v_out.get().strip()
        if not out:
            messagebox.showwarning(APP_TITLE, "Choose an output file.")
            return
        if os.path.abspath(out) in [os.path.abspath(p) for p in self.files]:
            messagebox.showwarning(APP_TITLE, "Output file must not be one of the inputs.")
            return
        if os.path.exists(out) and not messagebox.askyesno(APP_TITLE, f"{os.path.basename(out)} exists. Overwrite?"):
            return

        self.running = True
        self.btn_merge.config(state="disabled")
        self.btn_open.config(state="disabled")
        self.progress.start(12)
        self._clear_log()
        opts = self._options()
        files = list(self.files)

        def work() -> None:
            try:
                rep = merge(files, out, opts, log=lambda s: self.q.put(("log", s)))
                self.q.put(("done", rep))
            except Exception as e:  # noqa: BLE001
                self.q.put(("error", f"{type(e).__name__}: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    s = str(payload)
                    self._log(s, "warn" if s.lstrip().startswith("!") else None)
                elif kind == "done":
                    self._finish(payload)
                elif kind == "error":
                    self._log("ERROR: " + str(payload), "err")
                    self._stop()
                    messagebox.showerror(APP_TITLE, str(payload))
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _finish(self, rep) -> None:
        self._stop()
        self._log("")
        self._log(rep.text(), "ok" if not rep.warnings else None)
        self.btn_open.config(state="normal")
        # write the report next to the output
        try:
            with open(os.path.splitext(rep.output)[0] + "_merge_report.txt", "w", encoding="utf-8") as fh:
                fh.write(rep.text())
        except OSError:
            pass

    def _stop(self) -> None:
        self.running = False
        self.progress.stop()
        self.btn_merge.config(state="normal")

    # ------------------------------------------------------------------ log
    def _log(self, s: str, tag: str | None = None) -> None:
        self.log.config(state="normal")
        self.log.insert("end", s + "\n", tag or ())
        self.log.see("end")
        self.log.config(state="disabled")

    def _clear_log(self) -> None:
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
