"""
Multi-Platform Bulk Call Tester
================================
Supports:  MS Teams (Graph API)  |  Webex Calling (REST API)

Run:   python multi_caller.py
Build: build.bat
"""

import os, sys, json, time, queue, socket, threading, logging
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

from report_engine import (
    export_csv, export_html, export_xlsx, HAS_XLSX,
    RESULT_ANSWERED, RESULT_NO_ANSWER, RESULT_BUSY,
    RESULT_REJECTED, RESULT_ERROR,
)

# ── Constants ─────────────────────────────────────────────────────────────────
APP_NAME    = "Multi-Platform Bulk Call Tester"
APP_VERSION = "1.0"
CONFIG_FILE = Path.home() / ".multi_caller_config.json"

C_BG      = "#0f1117";  C_SURFACE = "#181c24"; C_CARD    = "#1e2230"
C_BORDER  = "#2a2f3f";  C_TEXT    = "#dce3f0"; C_DIM     = "#6b7899"
C_GREEN   = "#22c997";  C_RED     = "#f05252"; C_ORANGE  = "#f97316"
C_YELLOW  = "#f5c518";  C_PURPLE  = "#a78bfa"
C_TEAMS   = "#5B9CF6";  C_WEBEX   = "#00C86F"; C_CUCM    = "#F5A623"

RESULT_COLOURS = {
    RESULT_ANSWERED:  C_GREEN,
    RESULT_NO_ANSWER: C_ORANGE,
    RESULT_BUSY:      C_YELLOW,
    RESULT_REJECTED:  C_RED,
    RESULT_ERROR:     C_PURPLE,
}

PLATFORM_COLOURS = {
    "MS Teams":      C_TEAMS,
    "Webex Calling": C_WEBEX,
}

DEFAULT_CONFIG = {
    # Teams
    "teams_tenant_id":    "",
    "teams_client_id":    "",
    "teams_client_secret":"",
    "teams_callback_uri": "https://callback.example.com/teams",
    "teams_ring_timeout": "30",
    "teams_ans_duration": "3",
    "teams_delay":        "3",
    # Webex
    "webex_token":        "",
    "webex_ring_timeout": "25",
    "webex_ans_duration": "3",
    "webex_delay":        "2",
    # Shared
    "output_dir":         str(Path.home() / "Desktop"),
}

log = logging.getLogger("multi_caller")


def load_config():
    try: return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text())}
    except: return dict(DEFAULT_CONFIG)

def save_config(cfg):
    try: CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    except: pass


# ═══════════════════════════════════════════════════════════════════════════════
# Shared widgets
# ═══════════════════════════════════════════════════════════════════════════════

def make_entry(parent, sv, show="", width=34):
    e = tk.Entry(parent, textvariable=sv, show=show, width=width,
                 bg=C_SURFACE, fg=C_TEXT, insertbackground=C_TEXT,
                 relief="flat", font=("Courier New", 11),
                 highlightthickness=1, highlightbackground=C_BORDER,
                 highlightcolor=C_TEAMS)
    return e

def section_label(parent, text):
    f = tk.Frame(parent, bg=C_BG)
    f.pack(fill="x", padx=24, pady=(16,4))
    tk.Label(f, text=text, bg=C_BG, fg=C_DIM,
             font=("Courier New", 9)).pack(side="left")
    tk.Frame(f, bg=C_BORDER, height=1).pack(side="left", fill="x",
                                             expand=True, padx=8)

def cfg_row(parent, label, sv, show="", width=34, hint=""):
    f = tk.Frame(parent, bg=C_CARD)
    f.pack(fill="x", padx=24, pady=3)
    tk.Label(f, text=f"{label:<28}", bg=C_CARD, fg=C_DIM,
             font=("Courier New", 10), anchor="w").pack(side="left", padx=12, pady=8)
    e = make_entry(f, sv, show=show, width=width)
    e.pack(side="left", padx=4, pady=8)
    if hint:
        tk.Label(f, text=hint, bg=C_CARD, fg=C_DIM,
                 font=("Courier New", 9)).pack(side="left", padx=8)
    return e

def action_btn(parent, text, cmd, colour=C_TEAMS, fg="#fff", **kw):
    return tk.Button(parent, text=text, command=cmd, bg=colour, fg=fg,
                     font=("Courier New", 11, "bold"), relief="flat",
                     padx=14, pady=8, cursor="hand2", **kw)


# ═══════════════════════════════════════════════════════════════════════════════
# Main Application
# ═══════════════════════════════════════════════════════════════════════════════

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}  v{APP_VERSION}")
        self.geometry("1140x800"); self.minsize(960, 640)
        self.configure(bg=C_BG)

        self.cfg       = load_config()
        self.results   = []
        self.numbers   = []
        self._q        = queue.Queue()
        self._stop     = threading.Event()
        self._running  = False
        self._platform = tk.StringVar(value="MS Teams")

        self._build_ui()
        self._apply_style()
        self._restore_config()
        self.after(80, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Style ─────────────────────────────────────────────────────────────────

    def _apply_style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".",            background=C_BG,      foreground=C_TEXT,
                     fieldbackground=C_SURFACE, font=("Courier New", 11))
        s.configure("TNotebook",    background=C_BG,      tabmargins=[0,0,0,0])
        s.configure("TNotebook.Tab",background=C_SURFACE, foreground=C_DIM,
                     padding=[14,7], font=("Courier New", 10))
        s.map("TNotebook.Tab",
              background=[("selected", C_CARD)],
              foreground=[("selected", C_TEAMS)])
        s.configure("TFrame",       background=C_BG)
        s.configure("TLabel",       background=C_BG, foreground=C_TEXT)
        s.configure("TProgressbar", troughcolor=C_SURFACE, background=C_TEAMS, thickness=5)
        s.configure("Treeview",     background=C_SURFACE, fieldbackground=C_SURFACE,
                     foreground=C_TEXT, rowheight=24, font=("Courier New", 10))
        s.configure("Treeview.Heading", background=C_CARD, foreground=C_DIM,
                     font=("Courier New", 9, "bold"))
        s.map("Treeview",           background=[("selected", C_BORDER)])
        s.configure("TScrollbar",   background=C_SURFACE, troughcolor=C_BG,
                     borderwidth=0, arrowcolor=C_DIM)

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=C_SURFACE, height=54)
        hdr.pack(fill="x"); hdr.pack_propagate(False)

        tk.Label(hdr, text="◈  MULTI-PLATFORM  CALL  TESTER",
                 bg=C_SURFACE, fg=C_TEAMS,
                 font=("Courier New", 15, "bold")).pack(side="left", padx=20, pady=12)

        # Platform selector
        pf = tk.Frame(hdr, bg=C_SURFACE)
        pf.pack(side="left", padx=20)
        tk.Label(pf, text="Platform:", bg=C_SURFACE, fg=C_DIM,
                 font=("Courier New", 9)).pack(side="left")
        for plat, col in PLATFORM_COLOURS.items():
            tk.Radiobutton(pf, text=plat, variable=self._platform, value=plat,
                           bg=C_SURFACE, fg=col, selectcolor=C_CARD,
                           activebackground=C_SURFACE, activeforeground=col,
                           font=("Courier New", 10, "bold"),
                           command=self._on_platform_change
                           ).pack(side="left", padx=8)

        self._status_lbl = tk.Label(hdr, text="● IDLE", bg=C_SURFACE, fg=C_DIM,
                                     font=("Courier New", 10))
        self._status_lbl.pack(side="right", padx=20)

        tk.Frame(self, bg=C_BORDER, height=1).pack(fill="x")

        # Notebook
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill="both", expand=True)

        self._tab_cfg    = ttk.Frame(self._nb)
        self._tab_nums   = ttk.Frame(self._nb)
        self._tab_run    = ttk.Frame(self._nb)
        self._tab_report = ttk.Frame(self._nb)

        self._nb.add(self._tab_cfg,    text="  ⚙  Config  ")
        self._nb.add(self._tab_nums,   text="  ☎  Numbers  ")
        self._nb.add(self._tab_run,    text="  ▶  Run  ")
        self._nb.add(self._tab_report, text="  📊  Report  ")

        self._build_config_tab()
        self._build_numbers_tab()
        self._build_run_tab()
        self._build_report_tab()

    # ── Config tab ────────────────────────────────────────────────────────────

    def _build_config_tab(self):
        p = self._tab_cfg

        # ── MS Teams frame ────────────────────────────────────────────────────
        self._teams_frame = tk.Frame(p, bg=C_BG)

        section_label(self._teams_frame, "AZURE AD APPLICATION")
        self._sv = {}
        for key, label, hint in [
            ("teams_tenant_id",    "Tenant ID",            "Azure AD tenant GUID"),
            ("teams_client_id",    "Client ID (App ID)",   "Application (client) ID"),
            ("teams_client_secret","Client Secret",        ""),
            ("teams_callback_uri", "Callback URI",         "HTTPS endpoint (polling used regardless)"),
        ]:
            sv = tk.StringVar()
            self._sv[key] = sv
            show = "●" if "secret" in key else ""
            cfg_row(self._teams_frame, label, sv, show=show, hint=hint)

        section_label(self._teams_frame, "CALL SETTINGS")
        for key, label, hint in [
            ("teams_ring_timeout", "Ring Timeout (sec)",   "30 recommended for Teams"),
            ("teams_ans_duration", "Answer Hold (sec)",    "Seconds to hold after answer"),
            ("teams_delay",        "Delay Between Calls",  "Seconds between each call"),
        ]:
            sv = tk.StringVar()
            self._sv[key] = sv
            cfg_row(self._teams_frame, label, sv, hint=hint)

        # Auth test button
        bf = tk.Frame(self._teams_frame, bg=C_BG)
        bf.pack(fill="x", padx=24, pady=12)
        action_btn(bf, "⚡ Test Azure AD Auth", self._test_teams_auth,
                   colour=C_TEAMS).pack(side="left")
        self._teams_status = tk.Label(bf, text="", bg=C_BG, font=("Courier New", 10))
        self._teams_status.pack(side="left", padx=12)

        # Info box
        info = tk.Frame(self._teams_frame, bg=C_CARD)
        info.pack(fill="x", padx=24, pady=4)
        info_text = (
            "  Azure AD app setup required:\n"
            "  1. App Registrations → New registration\n"
            "  2. API Permissions → Microsoft Graph → Application → Calls.Initiate.All\n"
            "  3. Grant admin consent for your tenant\n"
            "  4. Configure as a Teams Calling Bot in Teams Admin Center\n"
            "  Targets accepted: UPN (user@domain.com), +E.164 phone, sip:user@domain.com"
        )
        tk.Label(info, text=info_text, bg=C_CARD, fg=C_DIM,
                 font=("Courier New", 9), justify="left",
                 anchor="w").pack(padx=12, pady=10, anchor="w")

        # ── Webex Calling frame ───────────────────────────────────────────────
        self._webex_frame = tk.Frame(p, bg=C_BG)

        section_label(self._webex_frame, "WEBEX CALLING CREDENTIALS")
        self._sv["webex_token"] = tk.StringVar()
        wf = tk.Frame(self._webex_frame, bg=C_CARD)
        wf.pack(fill="x", padx=24, pady=3)
        tk.Label(wf, text=f"{'Personal Access Token':<28}", bg=C_CARD, fg=C_DIM,
                 font=("Courier New", 10), anchor="w").pack(side="left", padx=12, pady=8)
        make_entry(wf, self._sv["webex_token"], show="●", width=50).pack(
            side="left", padx=4, pady=8)

        # Token help link
        hf = tk.Frame(self._webex_frame, bg=C_BG)
        hf.pack(fill="x", padx=24)
        tk.Label(hf, text="Get token → developer.webex.com/docs/getting-your-personal-access-token",
                 bg=C_BG, fg=C_WEBEX, font=("Courier New", 9),
                 cursor="hand2").pack(side="left")

        section_label(self._webex_frame, "CALL SETTINGS")
        for key, label, hint in [
            ("webex_ring_timeout", "Ring Timeout (sec)",   "25 recommended"),
            ("webex_ans_duration", "Answer Hold (sec)",    "Seconds to hold after answer"),
            ("webex_delay",        "Delay Between Calls",  "Seconds between each call"),
        ]:
            sv = tk.StringVar()
            self._sv[key] = sv
            cfg_row(self._webex_frame, label, sv, hint=hint)

        bf2 = tk.Frame(self._webex_frame, bg=C_BG)
        bf2.pack(fill="x", padx=24, pady=12)
        action_btn(bf2, "⚡ Verify Webex Token", self._test_webex_auth,
                   colour=C_WEBEX, fg="#000").pack(side="left")
        self._webex_status = tk.Label(bf2, text="", bg=C_BG, font=("Courier New", 10))
        self._webex_status.pack(side="left", padx=12)

        info2 = tk.Frame(self._webex_frame, bg=C_CARD)
        info2.pack(fill="x", padx=24, pady=4)
        info2_text = (
            "  Webex Calling requirements:\n"
            "  • A Webex Calling license assigned to your account\n"
            "  • An active registered device (Webex App, desk phone, or soft client)\n"
            "  • Token scopes: spark:calls_write  spark:calls_read\n"
            "  Targets accepted: Extension (e.g. 1001), DID, +E.164 number\n"
            "  Note: Calls are placed FROM your registered device (it will ring briefly)"
        )
        tk.Label(info2, text=info2_text, bg=C_CARD, fg=C_DIM,
                 font=("Courier New", 9), justify="left",
                 anchor="w").pack(padx=12, pady=10, anchor="w")

        # Output dir (shared)
        section_label(p, "OUTPUT")
        of = tk.Frame(p, bg=C_CARD)
        of.pack(fill="x", padx=24, pady=3)
        tk.Label(of, text=f"{'Output Directory':<28}", bg=C_CARD, fg=C_DIM,
                 font=("Courier New", 10), anchor="w").pack(side="left", padx=12, pady=8)
        self._sv["output_dir"] = tk.StringVar()
        make_entry(of, self._sv["output_dir"], width=40).pack(side="left", padx=4, pady=8)
        tk.Button(of, text="Browse", command=self._browse_output,
                  bg=C_SURFACE, fg=C_DIM, font=("Courier New", 9),
                  relief="flat", cursor="hand2").pack(side="left", padx=6)

        # Save button
        sf = tk.Frame(p, bg=C_BG)
        sf.pack(fill="x", padx=24, pady=10)
        tk.Button(sf, text="  Save Config  ", command=self._save_cfg,
                  bg=C_SURFACE, fg=C_DIM, font=("Courier New", 10),
                  relief="flat", padx=12, pady=7, cursor="hand2").pack(side="left")

        self._on_platform_change()   # show correct frame

    def _on_platform_change(self):
        plat = self._platform.get()
        self._teams_frame.pack_forget()
        self._webex_frame.pack_forget()
        if plat == "MS Teams":
            self._teams_frame.pack(fill="both", expand=True)
        else:
            self._webex_frame.pack(fill="both", expand=True)

        # Update tab accent colour
        pc = PLATFORM_COLOURS.get(plat, C_TEAMS)
        ttk.Style().map("TNotebook.Tab", foreground=[("selected", pc)])

    # ── Numbers tab ───────────────────────────────────────────────────────────

    def _build_numbers_tab(self):
        p = self._tab_nums
        plat_lbl = tk.Label(p,
            text="Enter one extension / number / UPN per line — or Import CSV / TXT",
            bg=C_BG, fg=C_DIM, font=("Courier New", 10))
        plat_lbl.pack(anchor="w", padx=24, pady=(14,4))

        tf = tk.Frame(p, bg=C_BG)
        tf.pack(fill="both", expand=True, padx=24)
        self._num_txt = tk.Text(tf, bg=C_SURFACE, fg=C_TEXT,
                                 insertbackground=C_TEXT,
                                 font=("Courier New", 12), relief="flat",
                                 highlightthickness=1, highlightbackground=C_BORDER,
                                 highlightcolor=C_TEAMS, wrap="none")
        sb = ttk.Scrollbar(tf, command=self._num_txt.yview)
        self._num_txt.configure(yscrollcommand=sb.set)
        self._num_txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bf = tk.Frame(p, bg=C_BG)
        bf.pack(fill="x", padx=24, pady=10)
        for txt, cmd in [("📂 Import CSV/TXT", self._import_numbers),
                          ("✕ Clear",           lambda: (self._num_txt.delete("1.0","end"),
                                                          self._upd_count()))]:
            tk.Button(bf, text=txt, command=cmd, bg=C_SURFACE, fg=C_TEXT,
                      font=("Courier New", 10), relief="flat",
                      padx=10, pady=5, cursor="hand2").pack(side="left", padx=(0,8))
        self._num_count = tk.Label(bf, text="0 numbers", bg=C_BG, fg=C_DIM,
                                    font=("Courier New", 10))
        self._num_count.pack(side="right")
        self._num_txt.bind("<<Modified>>", lambda e: self._upd_count())

    # ── Run tab ───────────────────────────────────────────────────────────────

    def _build_run_tab(self):
        p = self._tab_run

        # Stats bar
        sf = tk.Frame(p, bg=C_CARD)
        sf.pack(fill="x", padx=24, pady=16)
        sr = tk.Frame(sf, bg=C_CARD)
        sr.pack(fill="x", padx=14, pady=(12,6))
        self._svars = {}
        for lbl, key, col in [
            ("TOTAL",     "total",    C_TEAMS),
            ("ANSWERED",  "answered", C_GREEN),
            ("NO-ANSWER", "no_ans",   C_ORANGE),
            ("BUSY",      "busy",     C_YELLOW),
            ("REJECTED",  "rejected", C_RED),
            ("ERROR",     "errors",   C_PURPLE),
        ]:
            box = tk.Frame(sr, bg=C_SURFACE, padx=12, pady=8)
            box.pack(side="left", padx=5)
            sv = tk.StringVar(value="0")
            self._svars[key] = sv
            tk.Label(box, textvariable=sv, bg=C_SURFACE, fg=col,
                     font=("Courier New", 20, "bold")).pack()
            tk.Label(box, text=lbl, bg=C_SURFACE, fg=C_DIM,
                     font=("Courier New", 8)).pack()

        pbf = tk.Frame(sf, bg=C_CARD)
        pbf.pack(fill="x", padx=14, pady=(2,8))
        self._pbv = tk.DoubleVar()
        ttk.Progressbar(pbf, variable=self._pbv, maximum=100).pack(fill="x")
        self._pbl = tk.Label(pbf, text="0 / 0", bg=C_CARD, fg=C_DIM,
                              font=("Courier New", 9))
        self._pbl.pack(anchor="e", pady=(2,6))

        # Buttons
        br = tk.Frame(p, bg=C_BG)
        br.pack(fill="x", padx=24, pady=(0,10))
        self._start_btn = tk.Button(br, text="▶  START TEST",
                                     command=self._start,
                                     bg=C_GREEN, fg="#000",
                                     font=("Courier New", 13, "bold"),
                                     relief="flat", padx=18, pady=9, cursor="hand2")
        self._start_btn.pack(side="left")
        self._stop_btn = tk.Button(br, text="⏹  STOP",
                                    command=self._stop,
                                    bg=C_RED, fg="#fff",
                                    font=("Courier New", 13, "bold"),
                                    relief="flat", padx=18, pady=9, cursor="hand2",
                                    state="disabled")
        self._stop_btn.pack(side="left", padx=10)
        self._cur_lbl = tk.Label(br, text="", bg=C_BG, fg=C_DIM,
                                  font=("Courier New", 10))
        self._cur_lbl.pack(side="left", padx=8)

        # Table
        tf = tk.Frame(p, bg=C_BG)
        tf.pack(fill="both", expand=True, padx=24, pady=(0,14))
        cols = ("#", "Platform", "Number / Target", "Result", "Code", "Duration(s)", "Timestamp", "Notes")
        self._tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="browse")
        ws = [36, 110, 180, 110, 70, 90, 155, 280]
        for col, w in zip(cols, ws):
            self._tree.heading(col, text=col)
            self._tree.column(col, width=w, minwidth=30,
                               anchor="w" if col in ("Number / Target", "Notes") else "center",
                               stretch=(col == "Notes"))
        vsb = ttk.Scrollbar(tf, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(tf, orient="horizontal",  command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tf.grid_rowconfigure(0, weight=1); tf.grid_columnconfigure(0, weight=1)
        for r, c in RESULT_COLOURS.items():
            self._tree.tag_configure(r, foreground=c)
        self._tree.tag_configure("calling", foreground=C_TEAMS)

    # ── Report tab ────────────────────────────────────────────────────────────

    def _build_report_tab(self):
        p = self._tab_report
        tk.Label(p, text="Reports are saved automatically when the test completes.",
                 bg=C_BG, fg=C_DIM, font=("Courier New", 10)).pack(
                 anchor="w", padx=24, pady=(14,8))
        bf = tk.Frame(p, bg=C_BG)
        bf.pack(fill="x", padx=24)
        for txt, fmt, col, fg in [
            ("📊 Export Excel",    "xlsx", C_GREEN,  "#000"),
            ("📄 Export CSV",      "csv",  C_TEAMS,  "#fff"),
            ("🌐 Export HTML",     "html", C_ORANGE, "#000"),
            ("📋 Export All",      "all",  C_PURPLE, "#fff"),
        ]:
            tk.Button(bf, text=txt, command=lambda f=fmt: self._export(f),
                      bg=col, fg=fg, font=("Courier New", 11, "bold"),
                      relief="flat", padx=14, pady=9, cursor="hand2"
                      ).pack(side="left", padx=(0,8))
        self._summary = scrolledtext.ScrolledText(
            p, bg=C_SURFACE, fg=C_TEXT, font=("Courier New", 11),
            relief="flat", state="disabled")
        self._summary.pack(fill="both", expand=True, padx=24, pady=14)

    # ── Config helpers ────────────────────────────────────────────────────────

    def _restore_config(self):
        for k, v in self.cfg.items():
            if k in self._sv:
                self._sv[k].set(v)

    def _collect_cfg(self):
        for k in DEFAULT_CONFIG:
            if k in self._sv:
                self.cfg[k] = self._sv[k].get().strip()

    def _save_cfg(self):
        self._collect_cfg(); save_config(self.cfg)

    def _browse_output(self):
        d = filedialog.askdirectory(title="Select output directory")
        if d: self._sv["output_dir"].set(d)

    # ── Platform auth tests ───────────────────────────────────────────────────

    def _test_teams_auth(self):
        self._collect_cfg()
        self._teams_status.configure(text="Authenticating…", fg=C_DIM)
        self.update()
        def _run():
            try:
                from teams_engine import TeamsEngine
                e = TeamsEngine(
                    tenant_id     = self.cfg["teams_tenant_id"],
                    client_id     = self.cfg["teams_client_id"],
                    client_secret = self.cfg["teams_client_secret"],
                    callback_uri  = self.cfg["teams_callback_uri"],
                )
                ok, msg = e.authenticate()
                if ok:
                    ok2, msg2 = e.verify_connection()
                    msg = msg2 if ok2 else msg
                self._q.put(("auth_result", "teams", ok, msg))
            except Exception as ex:
                self._q.put(("auth_result", "teams", False, str(ex)))
        threading.Thread(target=_run, daemon=True).start()

    def _test_webex_auth(self):
        self._collect_cfg()
        self._webex_status.configure(text="Verifying…", fg=C_DIM)
        self.update()
        def _run():
            try:
                from webex_engine import WebexEngine
                e = WebexEngine(self.cfg["webex_token"])
                ok, msg = e.verify_token()
                self._q.put(("auth_result", "webex", ok, msg))
            except Exception as ex:
                self._q.put(("auth_result", "webex", False, str(ex)))
        threading.Thread(target=_run, daemon=True).start()

    # ── Numbers ───────────────────────────────────────────────────────────────

    def _import_numbers(self):
        paths = filedialog.askopenfilenames(
            title="Select number file(s)",
            filetypes=[("CSV/TXT","*.csv *.txt"),("All","*.*")])
        nums = set(self._num_txt.get("1.0","end").split())
        for path in paths:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    for part in line.replace(","," ").split():
                        part = part.strip().strip('"\'')
                        if part and part.lower() not in ("","number","extension","target","#","upn"):
                            nums.add(part)
        self._num_txt.delete("1.0","end")
        self._num_txt.insert("1.0", "\n".join(sorted(nums)))
        self._upd_count()

    def _upd_count(self):
        self._num_txt.edit_modified(False)
        n = len([x for x in self._num_txt.get("1.0","end").split() if x.strip()])
        self._num_count.configure(text=f"{n} numbers")

    # ── Test run ──────────────────────────────────────────────────────────────

    def _start(self):
        if self._running: return
        self._collect_cfg()
        self.numbers = [x.strip() for x in self._num_txt.get("1.0","end").split() if x.strip()]
        if not self.numbers:
            messagebox.showwarning("No Numbers","Add numbers in the Numbers tab."); return

        plat = self._platform.get()
        if plat == "MS Teams" and not self.cfg.get("teams_tenant_id"):
            messagebox.showwarning("Missing Config","Enter Azure AD Tenant ID in Config."); return
        if plat == "Webex Calling" and not self.cfg.get("webex_token"):
            messagebox.showwarning("Missing Config","Enter Webex Access Token in Config."); return

        self.results = []; self._stop.clear(); self._running = True
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._status_lbl.configure(text=f"● RUNNING — {plat}", fg=PLATFORM_COLOURS.get(plat, C_TEAMS))
        for k in self._svars: self._svars[k].set("0")
        for item in self._tree.get_children(): self._tree.delete(item)
        for i, n in enumerate(self.numbers, 1):
            self._tree.insert("", "end", iid=str(i),
                               values=(i, plat, n, "Pending", "", "", "", ""),
                               tags=("pending",))
        self._pbv.set(0)
        self._pbl.configure(text=f"0 / {len(self.numbers)}")
        threading.Thread(target=self._run_loop, args=(plat,), daemon=True).start()
        self._nb.select(self._tab_run)

    def _stop(self):
        self._stop.set(); self._stop_btn.configure(state="disabled")

    def _run_loop(self, platform: str):
        # Build engine
        try:
            if platform == "MS Teams":
                from teams_engine import TeamsEngine
                engine = TeamsEngine(
                    tenant_id     = self.cfg["teams_tenant_id"],
                    client_id     = self.cfg["teams_client_id"],
                    client_secret = self.cfg["teams_client_secret"],
                    callback_uri  = self.cfg["teams_callback_uri"],
                )
                ok, msg = engine.authenticate()
                if not ok:
                    self._q.put(("log", f"Auth failed: {msg}", C_RED))
                    self._q.put(("done",)); return
                self._q.put(("log", f"Authenticated — {msg}", C_GREEN))
                ring_t = int(self.cfg.get("teams_ring_timeout") or 30)
                ans_t  = int(self.cfg.get("teams_ans_duration")  or 3)
                delay  = float(self.cfg.get("teams_delay")        or 3)
            else:
                from webex_engine import WebexEngine
                engine = WebexEngine(self.cfg["webex_token"])
                ok, msg = engine.verify_token()
                if not ok:
                    self._q.put(("log", f"Token invalid: {msg}", C_RED))
                    self._q.put(("done",)); return
                self._q.put(("log", f"Webex token OK — {msg}", C_GREEN))
                ring_t = int(self.cfg.get("webex_ring_timeout") or 25)
                ans_t  = int(self.cfg.get("webex_ans_duration")  or 3)
                delay  = float(self.cfg.get("webex_delay")        or 2)
        except Exception as ex:
            self._q.put(("log", f"Engine init failed: {ex}", C_RED))
            self._q.put(("done",)); return

        total    = len(self.numbers)
        counters = dict(total=total, answered=0, no_ans=0, busy=0, rejected=0, errors=0)

        for idx, number in enumerate(self.numbers, 1):
            if self._stop.is_set():
                self._q.put(("log", "⏹ Stopped by user", C_YELLOW)); break

            self._q.put(("calling", idx, number, platform))
            result = engine.test_call(number, ring_timeout=ring_t, answer_duration=ans_t)
            result["platform"] = platform
            self.results.append(result)

            r = result["result"]
            if   r == RESULT_ANSWERED:  counters["answered"] += 1
            elif r == RESULT_NO_ANSWER: counters["no_ans"]   += 1
            elif r == RESULT_BUSY:      counters["busy"]     += 1
            elif r == RESULT_REJECTED:  counters["rejected"] += 1
            else:                       counters["errors"]   += 1

            pct = round(idx / total * 100, 1)
            self._q.put(("row", idx, number, result, counters, pct, platform))

            if idx < total and not self._stop.is_set():
                time.sleep(delay)

        self._q.put(("done", counters, platform))

    # ── Queue handler ─────────────────────────────────────────────────────────

    def _poll(self):
        try:
            while True: self._handle(self._q.get_nowait())
        except queue.Empty: pass
        self.after(80, self._poll)

    def _handle(self, msg):
        kind = msg[0]

        if kind == "auth_result":
            _, plat, ok, text = msg
            col = C_GREEN if ok else C_RED
            if plat == "teams":
                self._teams_status.configure(text=("✔ " if ok else "✖ ") + text, fg=col)
            else:
                self._webex_status.configure(text=("✔ " if ok else "✖ ") + text, fg=col)

        elif kind == "log":
            _, text, col = msg
            self._status_lbl.configure(text=f"● {text}", fg=col)

        elif kind == "calling":
            _, idx, num, plat = msg
            pc = PLATFORM_COLOURS.get(plat, C_TEAMS)
            self._cur_lbl.configure(text=f"Calling {num}…")
            self._tree.item(str(idx), values=(idx, plat, num, "Calling…", "", "", "", ""),
                            tags=("calling",))
            self._tree.see(str(idx))

        elif kind == "row":
            _, idx, num, result, counters, pct, plat = msg
            r = result["result"]
            self._tree.item(str(idx), values=(
                idx, plat, num, r,
                result.get("api_code",""),
                result.get("duration_s",""),
                result.get("started_at",""),
                result.get("note",""),
            ), tags=(r,))
            for k, v in [("total",    counters["total"]),
                          ("answered", counters["answered"]),
                          ("no_ans",   counters["no_ans"]),
                          ("busy",     counters["busy"]),
                          ("rejected", counters["rejected"]),
                          ("errors",   counters["errors"])]:
                self._svars[k].set(str(v))
            self._pbv.set(pct)
            self._pbl.configure(text=f"{idx} / {counters['total']}  ({pct}%)")

        elif kind == "done":
            counters = msg[1] if len(msg) > 1 else {}
            platform = msg[2] if len(msg) > 2 else ""
            self._running = False
            self._start_btn.configure(state="normal")
            self._stop_btn.configure(state="disabled")
            self._cur_lbl.configure(text="")
            self._status_lbl.configure(
                text=f"● COMPLETE — {len(self.results)} calls  [{platform}]", fg=C_GREEN)
            self._pbv.set(100)
            if self.results:
                self._refresh_summary()
                self._auto_export(platform)
            self._nb.select(self._tab_report)

    # ── Export ────────────────────────────────────────────────────────────────

    def _meta(self):
        plat = self._platform.get()
        m = {"platform": plat, "output_dir": self.cfg.get("output_dir",".")}
        if plat == "MS Teams":
            m["tenant_id"] = self.cfg.get("teams_tenant_id","")
        else:
            m["user_display"] = ""
        return m

    def _export(self, fmt="all"):
        if not self.results:
            messagebox.showinfo("No Data","Run a test first."); return
        out = Path(self.cfg.get("output_dir") or "."); out.mkdir(parents=True, exist_ok=True)
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        meta = self._meta(); saved = []
        plat_slug = self._platform.get().lower().replace(" ","_")
        if fmt in ("xlsx","all") and HAS_XLSX:
            p = str(out / f"{plat_slug}_call_test_{ts}.xlsx")
            export_xlsx(self.results, p, meta); saved.append(p)
        if fmt in ("csv","all"):
            p = str(out / f"{plat_slug}_call_test_{ts}.csv")
            export_csv(self.results, p); saved.append(p)
        if fmt in ("html","all"):
            p = str(out / f"{plat_slug}_call_test_{ts}.html")
            export_html(self.results, p, meta); saved.append(p)
        if saved:
            messagebox.showinfo("Exported", "Saved:\n" + "\n".join(saved))

    def _auto_export(self, platform):
        out = Path(self.cfg.get("output_dir") or "."); out.mkdir(parents=True, exist_ok=True)
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        meta = self._meta()
        slug = platform.lower().replace(" ","_")
        try:
            export_csv(self.results,  str(out / f"{slug}_call_test_{ts}.csv"))
            export_html(self.results, str(out / f"{slug}_call_test_{ts}.html"), meta)
            if HAS_XLSX:
                export_xlsx(self.results, str(out / f"{slug}_call_test_{ts}.xlsx"), meta)
        except Exception as e:
            log.warning("Auto-export error: %s", e)

    def _refresh_summary(self):
        rows    = self.results
        total   = len(rows)
        answered  = sum(1 for r in rows if r["result"] == RESULT_ANSWERED)
        no_ans    = sum(1 for r in rows if r["result"] == RESULT_NO_ANSWER)
        busy      = sum(1 for r in rows if r["result"] == RESULT_BUSY)
        rejected  = sum(1 for r in rows if r["result"] == RESULT_REJECTED)
        errors    = sum(1 for r in rows if r["result"] == RESULT_ERROR)
        pct       = round(answered / total * 100, 1) if total else 0
        platform  = self._platform.get()
        lines = [
            "=" * 56,
            f"  {platform.upper()}  CALL TEST — SUMMARY",
            f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 56, "",
            f"  Total Attempted : {total}",
            f"  Answered        : {answered:<5}  ({pct}%)",
            f"  No Answer       : {no_ans}",
            f"  Busy            : {busy}",
            f"  Rejected        : {rejected}",
            f"  Error           : {errors}",
            "", "=" * 56, "  DETAIL",
            "=" * 56,
            f"{'#':<5} {'Target':<25} {'Result':<13} {'Code':<8} Notes",
            "-" * 56,
        ]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i:<5} {r['number']:<25} {r['result']:<13} "
                          f"{str(r.get('api_code','')):<8} {r.get('note','')}")
        self._summary.configure(state="normal")
        self._summary.delete("1.0","end")
        self._summary.insert("1.0", "\n".join(lines))
        self._summary.configure(state="disabled")

    def _on_close(self):
        if self._running:
            if not messagebox.askyesno("Quit","Test is running. Stop and quit?"): return
            self._stop.set()
        save_config(self.cfg); self.destroy()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)s  %(message)s",
                        datefmt="%H:%M:%S")
    App().mainloop()
