import tkinter as tk
from pathlib import Path
import sys
from tkinter import messagebox, ttk
import webbrowser
import queue
import json
import time
import threading
import traceback
import ctypes
import re

from utils import (
    start_test,
    stop_process,
    is_process_running,
    file_exists,
    get_physical_cores,
    update_working_cfg,
    reset_working_cfg,
    get_working_cfg_path,
    get_default_cfg_path,
    get_app_root,
    set_process_affinity,
    write_text_atomic,
)
from sensors import get_cpu_name, SensorService, resolve_hwinfo_path
from sensor_history import SensorHistory
from sensor_window import SensorWindow
from widgets import ThinScrollbar, RoundedPanel, RoundedButton, RoundedEntry
from window_frame import WindowFrame
from presets import PresetWindow, cpu_vendor, build_intel_plan
from test_selector import TESTS
from vf_curve import build_plan, detect_ccds, IterationTracker, FAILURE, format_stage_status, format_cpu_scope


MAX_TEST_SECONDS = 30 * 24 * 3600


def completion_status(returncode):
    """Status line for a test that ended without a manual stop request.

    y-cruncher returns a non-zero code when a test aborts (bad cfg, out of
    memory, a detected error).  The GUI used to report every exit as a
    successful finish, which hid real failures.
    """
    if returncode in (0, None):
        return "Тест завершён"
    return "Тест завершён с ошибкой y-cruncher (код %s)" % returncode


def console_at_bottom(first, last, tolerance=0.002):
    """True when the console view is pinned to the last line.

    Auto-scrolling is only correct in that state: taking the view back while
    the user is reading older output makes the log unreadable during a test.
    """
    try:
        return float(last) >= 1.0 - tolerance
    except (TypeError, ValueError):
        return True

class SimpleTooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        self.job = None

        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")

    def schedule(self, event=None):
        self.cancel()
        self.job = self.widget.after(300, self.show)

    def cancel(self):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None

    def show(self):
        if self.tip:
            return

        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.attributes("-topmost", True)


        root = self.widget.winfo_toplevel()
        dark_var = getattr(root, "dark_mode", None)
        dark = dark_var.get() if dark_var is not None else True
        if dark:
            tip_bg = "#2b2b2b"
            tip_fg = "#ffffff"
            tip_border = "#ffffff"
        else:
            tip_bg = "#ffffff"
            tip_fg = "#1f1f1f"
            tip_border = "#bdbdbd"

        frame = tk.Frame(
            self.tip,
            bg=tip_bg,
            highlightbackground=tip_border,
            highlightthickness=1
        )
        frame.pack()

        tk.Label(
            frame,
            text=self.text,
            bg=tip_bg,
            fg=tip_fg,
            justify="left",
            padx=10,
            pady=8,
            font=("Segoe UI", 9)
        ).pack()


        self.tip.update_idletasks()
        x = self.widget.winfo_rootx() - self.tip.winfo_reqwidth()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6


        screen_w = self.tip.winfo_screenwidth()
        screen_h = self.tip.winfo_screenheight()
        x = max(4, min(x, screen_w - self.tip.winfo_reqwidth() - 4))
        y = max(4, min(y, screen_h - self.tip.winfo_reqheight() - 4))
        self.tip.geometry(f"+{x}+{y}")

    def hide(self, event=None):
        self.cancel()

        if self.tip:
            self.tip.destroy()
            self.tip = None



class LauncherApp(tk.Tk):

    BG = "#17191c"
    CARD = "#22252a"
    TEXT = "#f2f2f2"
    MUTED = "#a7adb5"
    BLUE = "#3d8bfd"
    BLUE_HOVER = "#2f75d6"
    GREEN = "#35c98a"
    RED = "#ef5b63"
    RED_HOVER = "#c9474f"
    BORDER = "#363b43"

    DARK_THEME = {
        "BG": "#17191c",
        "CARD": "#22252a",
        "TEXT": "#f2f2f2",
        "MUTED": "#a7adb5",
        "BLUE": "#3d8bfd",
        "BLUE_HOVER": "#2f75d6",
        "GREEN": "#35c98a",
        "RED": "#ef5b63",
        "RED_HOVER": "#c9474f",
        "BORDER": "#363b43",
        "CONSOLE_BG": "#101214",
        "CONSOLE_FG": "#d9e1e8",
        "TROUGH": "#30343b",
    }
    LIGHT_THEME = {
        "BG": "#f2f2f2",
        "CARD": "#ffffff",
        "TEXT": "#1f1f1f",
        "MUTED": "#777777",
        "BLUE": "#2f80ed",
        "BLUE_HOVER": "#256dcc",
        "GREEN": "#20a464",
        "RED": "#d64545",
        "RED_HOVER": "#b93636",
        "BORDER": "#d9d9d9",
        "CONSOLE_BG": "#f8f8f8",
        "CONSOLE_FG": "#222222",
        "TROUGH": "#e5e5e5",
    }

    @staticmethod
    def _resource_dir(name):
        """Find resource folders both in source mode and frozen EXE mode."""
        candidates = []
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidates.append(exe_dir / name)
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                candidates.append(Path(meipass) / name)
        project_root = Path(__file__).resolve().parents[1]
        candidates.append(project_root / name)
        candidates.append(Path.cwd() / name)
        for path in candidates:
            if path.exists():
                return path
        return candidates[0]

    @staticmethod
    def _get_icon_path():
        candidates = []

        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidates.append(exe_dir / "icon.ico")
            candidates.append(exe_dir / "launcher.ico")
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                candidates.append(Path(meipass) / "icon.ico")
                candidates.append(Path(meipass) / "launcher.ico")
        else:
            project_root = Path(__file__).resolve().parents[1]
            candidates.append(project_root / "icon.ico")
            candidates.append(Path.cwd() / "icon.ico")
            candidates.append(project_root / "launcher.ico")
            candidates.append(Path.cwd() / "launcher.ico")

        for path in candidates:
            if path.is_file():
                return path
        return None

    def __init__(self):
        super().__init__()

        # Use the same custom icon in the Tkinter window title bar.
        icon_path = self._get_icon_path()
        if icon_path is not None:
            try:
                self.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass

        self.title("y-cruncher GUI 3.0 by @rx580_2048")
        self.geometry("1280x760")
        self.minsize(1280, 760)
        self.resizable(True, True)

        self.test_process = None
        self._test_started_at = None
        self._seconds_total = 0
        self._seconds_per_test = 120
        self._sequential_cores = []
        self._sequential_index = 0
        self._last_ycruncher_iteration = None
        self._ycruncher_iteration_started = False
        self._run_mode = "all"
        self._vf_active = False
        self._vf_queue = queue.Queue()
        self._vf_failed = False
        self._stop_event = False
        self._console_stop_event = None
        self._finishing = False
        self._selected_tests = []
        self.core_vars = {}
        self.cores = []
        self.monitor_ycruncher_job = None
        self.console_poll_job = None
        self.manual_stop_requested = False
        self.test_vars = {}
        self.console_queue = queue.Queue()
        self._checkbuttons = []
        self._console_max_lines = 5000
        self._user_selection_backup = None
        self._error_dialog_shown = False

        for name, _ in TESTS:
            self.test_vars[name] = tk.BooleanVar(value=False)

        self.seconds_total = tk.StringVar(value="3600")
        self.seconds_per_test = tk.StringVar(value="120")
        self.unlimited = tk.BooleanVar(value=False)
        self.stop_on_error = tk.BooleanVar(value=True)
        self.cpu_mode = tk.StringVar(value="all")
        self.status_text = tk.StringVar(value="Тест не запущен")
        self.timer_text = tk.StringVar(value="00:00:00")
        self.status_color = self.MUTED
        self.status_label = None
        self.dark_mode = tk.BooleanVar(value=False)
        self.preset_window = None
        self.cpu_name = get_cpu_name()
        self.hwinfo_path = ""
        self.sensor_voltage_key = ""
        self.sensor_window = None
        self.sensor_rows = []
        self.sensor_status = "Нажмите «Датчики» для запуска HWiNFO"
        self._closing = False
        self._load_settings()
        self.hwinfo_path = resolve_hwinfo_path(get_app_root(), self.hwinfo_path)
        self.sensor_history = SensorHistory(get_app_root() / 'sensor_history.json')
        if not Path(self.hwinfo_path).is_file():
            self.sensor_status = 'Положите HWiNFO64.exe и его INI в папку hwinfo рядом с GUI'

        self._setup_style()
        self.window_frame = WindowFrame(self, self.on_close, lambda: self.dark_mode.get())
        self._build_ui()
        self.apply_theme()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self._poll_console()
        self.sensor_service = SensorService("", True, get_app_root() / "sensor_diagnostics.json")
        self._poll_sensors()

    def _setup_style(self):
        self._style = ttk.Style(self)
        style = self._style
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TEntry", padding=8, font=("Segoe UI", 11))
        style.configure("Horizontal.TProgressbar", borderwidth=0, thickness=5)

        # Minimal Windows 11-like scrollbars.
        style.configure(
            "Modern.Vertical.TScrollbar",
            gripcount=0,
            width=5,
            background="#555555",
            darkcolor="#555555",
            lightcolor="#555555",
            bordercolor="#22252a",
            troughcolor="#22252a",
            arrowcolor="#aaaaaa"
        )

    def _build_ui(self):
        self.configure(bg=self.BG)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        top = tk.Frame(self, bg=self.BG, bd=0)
        top.grid(row=1, column=0, sticky="nsew", padx=18, pady=16)
        top.grid_rowconfigure(0, weight=1)

        self._split_ratio = 0.58
        self._split_min_left = 560
        self._split_min_right = 450
        self._splitter_width = 8

        self.console_panel = RoundedPanel(top, bg=self.CARD, border=self.BORDER, surrounding=self.BG, radius=14)
        console_card = self.console_panel.body
        console_card._is_card_body = True
        console_card.grid_rowconfigure(1, weight=1)
        console_card.grid_columnconfigure(0, weight=1)
        tk.Label(console_card, text="y-cruncher", font=("Segoe UI", 11, "bold"),
                 fg=self.TEXT, bg=self.CARD).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 8))

        self.console_content_panel = RoundedPanel(console_card, bg=self.DARK_THEME["CONSOLE_BG"],
                                                  border=self.DARK_THEME["CONSOLE_BG"], surrounding=self.CARD)
        self.console_content_panel.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        console_wrap = self.console_content_panel.body
        console_wrap.grid_rowconfigure(0, weight=1)
        console_wrap.grid_columnconfigure(0, weight=1)
        self.console = tk.Text(console_wrap, wrap="none", font=("Consolas", 10),
                               bg="#101214", fg="#d9e1e8", insertbackground="#ffffff",
                               relief="flat", bd=0, padx=10, pady=8, state="disabled")
        self.console.grid(row=0, column=0, sticky="nsew")
        self.console.tag_configure("running", foreground="#35c98a")
        self.console.tag_configure("passed", foreground="#35c98a")
        self.console.tag_configure("error", foreground="#ef5b63")
        console_scroll = ThinScrollbar(console_wrap, command=self.console.yview)
        self.console_scroll = console_scroll
        console_scroll.grid(row=0, column=1, sticky="ns")
        self.console.configure(yscrollcommand=console_scroll.set)

        self.control_panel = RoundedPanel(top, bg=self.CARD, border=self.BORDER, surrounding=self.BG, radius=14)
        control_card = self.control_panel.body
        control_card._is_card_body = True
        control_card.grid_columnconfigure(0, weight=1)
        control_card.grid_columnconfigure(1, weight=0)
        control_card.grid_rowconfigure(7, weight=1)

        tk.Label(control_card, text="Выберите тест:", font=("Segoe UI", 12, "bold"),
                 fg=self.TEXT, bg=self.CARD).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 10))
        self.presets_button = RoundedButton(control_card, text="Пресеты", command=self.open_presets,
            font=("Segoe UI", 9, "bold"), bg=self.CARD, fg=self.TEXT,
            border=self.BORDER, surrounding=self.CARD, padx=12, pady=4)
        self.presets_button.grid(row=0, column=1, sticky="e", padx=18, pady=(16, 10))
        self.preset_buttons = [self.presets_button]

        test_frame = tk.Frame(control_card, bg=self.CARD)
        test_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=18)
        test_frame.grid_columnconfigure(0, weight=1, uniform="test_column")
        test_frame.grid_columnconfigure(1, weight=1, uniform="test_column")
        for index, (name, _) in enumerate(TESTS):
            checkbutton = tk.Checkbutton(test_frame, text=name, variable=self.test_vars[name],
                                         bg=self.CARD, fg=self.TEXT, activebackground=self.CARD,
                                         activeforeground=self.TEXT, selectcolor=self.CARD,
                                         font=("Segoe UI", 9), highlightthickness=0, bd=0, anchor="w")
            checkbutton.grid(row=index // 2, column=index % 2, sticky="w", pady=2)
            self._checkbuttons.append(checkbutton)

        separator = tk.Frame(control_card, height=1, bg=self.BORDER)
        separator.grid(row=2, column=0, columnspan=2, sticky="ew", padx=18, pady=10)

        self.stop_error_check = tk.Checkbutton(control_card, text="Останавливать при ошибке",
                                               variable=self.stop_on_error, bg=self.CARD, fg=self.TEXT,
                                               activebackground=self.CARD, activeforeground=self.TEXT,
                                               selectcolor=self.CARD, font=("Segoe UI", 9),
                                               highlightthickness=0, bd=0, anchor="w")
        self.stop_error_check.grid(row=3, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 7))

        # Time settings: two equal-width columns.  Each title is above its
        # input field, and the unlimited option stays directly below the
        # total-time field on the left.
        time_frame = tk.Frame(control_card, bg=self.CARD)
        time_frame.grid(row=4, column=0, columnspan=2, sticky="ew", padx=18, pady=(2, 0))
        time_frame.grid_columnconfigure(0, weight=1, uniform="time_column")
        time_frame.grid_columnconfigure(1, weight=1, uniform="time_column")

        total_time_frame = tk.Frame(time_frame, bg=self.CARD)
        total_time_frame.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        total_time_frame.grid_columnconfigure(0, weight=1)
        tk.Label(total_time_frame, text="Всего (секунд):", anchor="w",
                 font=("Segoe UI", 9), fg=self.MUTED, bg=self.CARD).grid(
                     row=0, column=0, sticky="w", pady=(0, 4))
        self.duration_entry = RoundedEntry(total_time_frame, textvariable=self.seconds_total,
                                           justify="center", width=12, font=("Segoe UI", 11),
                                           bg=self.CARD, fg=self.TEXT, border=self.BORDER, surrounding=self.CARD)
        self.duration_entry.grid(row=1, column=0, sticky="ew")

        per_test_frame = tk.Frame(time_frame, bg=self.CARD)
        per_test_frame.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        per_test_frame.grid_columnconfigure(0, weight=1)
        tk.Label(per_test_frame, text="Один тест (секунд):", anchor="w",
                 font=("Segoe UI", 9), fg=self.MUTED, bg=self.CARD).grid(
                     row=0, column=0, sticky="w", pady=(0, 4))
        self.seconds_per_test_entry = RoundedEntry(per_test_frame, textvariable=self.seconds_per_test,
                                                   justify="center", width=12, font=("Segoe UI", 11),
                                                   bg=self.CARD, fg=self.TEXT, border=self.BORDER, surrounding=self.CARD)
        self.seconds_per_test_entry.grid(row=1, column=0, sticky="ew")

        self.unlimited_check = tk.Checkbutton(control_card, text="Неограниченное время теста",
                                              variable=self.unlimited, command=self._on_unlimited_changed,
                                              bg=self.CARD, fg=self.TEXT, activebackground=self.CARD,
                                              activeforeground=self.TEXT, selectcolor=self.CARD,
                                              font=("Segoe UI", 9), highlightthickness=0, bd=0, anchor="w")
        self.unlimited_check.grid(row=5, column=0, columnspan=2, sticky="w", padx=18, pady=(6, 3))

        mode_frame = tk.Frame(control_card, bg=self.CARD)
        mode_frame.grid(row=7, column=0, columnspan=2, sticky="nsew", padx=18)
        mode_frame.grid_rowconfigure(3, weight=1)
        mode_frame.grid_columnconfigure(0, weight=1)
        self.mode_all = tk.Radiobutton(mode_frame, text="Все ядра", variable=self.cpu_mode, value="all",
                                       command=self._update_core_controls, bg=self.CARD, fg=self.TEXT,
                                       activebackground=self.CARD, activeforeground=self.TEXT,
                                       selectcolor=self.CARD, font=("Segoe UI", 9), highlightthickness=0, bd=0, anchor="w")
        self.mode_selected = tk.Radiobutton(mode_frame, text="Выбранные ядра", variable=self.cpu_mode, value="selected",
                                            command=self._update_core_controls, bg=self.CARD, fg=self.TEXT,
                                            activebackground=self.CARD, activeforeground=self.TEXT,
                                            selectcolor=self.CARD, font=("Segoe UI", 9), highlightthickness=0, bd=0, anchor="w")
        self.mode_sequential = tk.Radiobutton(mode_frame, text="Последовательно по выбранным ядрам", variable=self.cpu_mode, value="sequential",
                                              command=self._update_core_controls, bg=self.CARD, fg=self.TEXT,
                                              activebackground=self.CARD, activeforeground=self.TEXT,
                                              selectcolor=self.CARD, font=("Segoe UI", 9), highlightthickness=0, bd=0, anchor="w")
        self.mode_all.grid(row=0, column=0, sticky="w")
        self.mode_selected.grid(row=1, column=0, sticky="w")
        self.mode_sequential.grid(row=2, column=0, sticky="w")

        self.core_panel = RoundedPanel(mode_frame, bg=self.CARD, border=self.BORDER)
        self.core_panel.grid(row=3, column=0, sticky="nsew", pady=(6, 0))
        core_outer = self.core_panel.body
        core_outer.grid_rowconfigure(0, weight=1)
        core_outer.grid_columnconfigure(0, weight=1)
        self.core_canvas = tk.Canvas(core_outer, bg=self.CARD, highlightthickness=0, bd=0)
        self.core_canvas.grid(row=0, column=0, sticky="nsew")
        core_scroll = ThinScrollbar(core_outer, command=self.core_canvas.yview)
        self.core_scroll = core_scroll
        core_scroll.grid(row=0, column=1, sticky="ns")
        self.core_canvas.configure(yscrollcommand=core_scroll.set)
        self.core_frame = tk.Frame(self.core_canvas, bg=self.CARD)
        self.core_window = self.core_canvas.create_window((0, 0), window=self.core_frame, anchor="nw")
        self.core_frame.bind("<Configure>", lambda e: self.core_canvas.configure(scrollregion=self.core_canvas.bbox("all")))
        self.core_canvas.bind("<Configure>", lambda e: self.core_canvas.itemconfigure(self.core_window, width=e.width))

        bottom = tk.Frame(control_card, bg=self.CARD)
        bottom.grid(row=8, column=0, columnspan=2, sticky="ew", padx=18, pady=(12, 16))
        bottom.grid_columnconfigure(0, weight=1)
        tk.Label(bottom, textvariable=self.timer_text, font=("Consolas", 18, "bold"), fg=self.TEXT, bg=self.CARD).grid(row=0, column=0, pady=(0, 2))
        self.status_label = tk.Label(bottom, textvariable=self.status_text, font=("Segoe UI", 9, "bold"), fg=self.status_color, bg=self.CARD)
        self.status_label.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        bottom.bind("<Configure>", lambda e: self.status_label.configure(wraplength=max(160, e.width)))
        self.progress_canvas = tk.Canvas(bottom, height=5, bg=self.BORDER, highlightthickness=0, bd=0)
        self.progress_canvas.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        self.progress_fill = self.progress_canvas.create_rectangle(0, 0, 0, 5, fill=self.BLUE, width=0)
        self.progress_canvas.bind("<Configure>", lambda e: self._update_canvas_progress())
        self._progress_value = 0
        self.start_button = RoundedButton(bottom, text="Запустить тест", command=self.start_selected_test,
                                      font=("Segoe UI", 10, "bold"), fg="white", bg=self.BLUE,
                                      activeforeground="white", activebackground=self.BLUE_HOVER,
                                      relief="flat", cursor="hand2", bd=0)
        self.start_button.grid(row=3, column=0, sticky="ew", pady=(0, 7))
        self.stop_button = RoundedButton(bottom, text="Остановить тест", command=self.stop_test,
                                     font=("Segoe UI", 10, "bold"), fg="white", bg=self.RED,
                                     activeforeground="white", activebackground=self.RED_HOVER,
                                     disabledforeground="white", relief="flat", cursor="hand2", bd=0, state="disabled")
        self.stop_button.grid(row=4, column=0, sticky="ew")
        # Icon buttons use pre-rendered 32x32 antialiased PNGs at 1:1 scale.
        # Avoid Tkinter zoom()/subsample(): the assets are already rendered at
        # the exact display size, which keeps diagonal edges smooth.
        icons_frame = tk.Frame(bottom, bg=self.CARD)
        icons_frame.grid(row=5, column=0, sticky="w", pady=(10, 0))

        assets_dir = self._resource_dir("assets")
        self._check_icons = {theme: {
            state: tk.PhotoImage(file=str(assets_dir / ('check_%s_%s.png' % (theme, state))))
            for state in ('off', 'on', 'disabled')}
            for theme in ('dark', 'light')}

        # Dark theme uses light glyphs; light theme uses dark glyphs.
        # The button surface itself follows the surrounding card background.
        self.telegram_icon_dark = tk.PhotoImage(file=str(assets_dir / "telegram.png"))
        self.telegram_icon_light = tk.PhotoImage(file=str(assets_dir / "telegram_light.png"))
        self.sun_icon = tk.PhotoImage(file=str(assets_dir / "sun.png"))
        self.moon_icon = tk.PhotoImage(file=str(assets_dir / "moon.png"))
        self.trash_icon_dark = tk.PhotoImage(file=str(assets_dir / "trash.png"))
        self.trash_icon_light = tk.PhotoImage(file=str(assets_dir / "trash_light.png"))
        self.reset_icon_dark = tk.PhotoImage(file=str(assets_dir / "reset.png"))
        self.reset_icon_light = tk.PhotoImage(file=str(assets_dir / "reset_light.png"))

        # Размер PNG-иконок: 32x32 px.
        # Размер кликабельной квадратной кнопки можно менять здесь.
        # Например: 44, 48, 52, 56. Значение меньше 32 обрежет иконку.
        icon_button_size = 37
        self.icon_frames = []

        def icon_slot(column):
            slot = tk.Frame(icons_frame, width=43, height=43, bg=self.CARD,
                            bd=0, highlightthickness=0)
            slot.grid(row=0, column=column, padx=(0, 6) if column < 4 else 0)
            slot.pack_propagate(False)
            self.icon_frames.append(slot)
            return slot

        # Расстояния между КАЖДОЙ парой соседних иконок настраиваются отдельно.
        # Значения указаны в пикселях. Можно поставить, например, 0, 4, 8, 12.
        gap_telegram_theme = 6
        gap_theme_clear = 6
        gap_clear_reset = 6
        self.telegram = RoundedButton(
            icon_slot(0), image=self.telegram_icon_dark, command=self.open_telegram,
            width=icon_button_size, height=icon_button_size,
            bg=self.CARD, activebackground=self.CARD,
            relief="flat", cursor="hand2", bd=0, highlightthickness=0,
            padx=0, pady=0
        )
        self.telegram.pack(fill="both", expand=True, padx=3, pady=3)

        self.theme_button = RoundedButton(
            icon_slot(1), image=self.moon_icon, command=self.toggle_theme,
            width=icon_button_size, height=icon_button_size,
            bg=self.CARD, activebackground=self.CARD,
            relief="flat", cursor="hand2", bd=0, highlightthickness=0,
            padx=0, pady=0
        )
        self.theme_button.pack(fill="both", expand=True, padx=3, pady=3)

        self.clear_console_button = RoundedButton(
            icon_slot(2), image=self.trash_icon_dark, command=self._clear_console,
            width=icon_button_size, height=icon_button_size,
            bg=self.BLUE, activebackground=self.BLUE_HOVER,
            relief="flat", cursor="hand2", bd=0, highlightthickness=0,
            padx=0, pady=0
        )
        self.clear_console_button.pack(fill="both", expand=True, padx=3, pady=3)

        self.reset_config_button = RoundedButton(
            icon_slot(3), image=self.reset_icon_dark, command=self._reset_config_to_default,
            width=icon_button_size, height=icon_button_size,
            bg=self.BLUE, activebackground=self.BLUE_HOVER,
            relief="flat", cursor="hand2", bd=0, highlightthickness=0,
            padx=0, pady=0
        )
        self.reset_config_button.pack(fill="both", expand=True, padx=3, pady=3)

        self.hwinfo_icon = tk.PhotoImage(file=str(assets_dir / "hwinfo.png"))
        self.sensor_button = RoundedButton(
            icon_slot(4), image=self.hwinfo_icon, command=self.open_sensors,
            width=icon_button_size, height=icon_button_size,
            bg=self.BLUE, activebackground=self.BLUE_HOVER,
            relief="flat", bd=0, highlightthickness=0, cursor="hand2", padx=0, pady=0)
        self.sensor_button.pack(fill="both", expand=True, padx=3, pady=3)

        SimpleTooltip(self.sensor_button, "Датчики — HWiNFO")
        SimpleTooltip(self.telegram, "Telegram")
        SimpleTooltip(self.theme_button, "Сменить тему")
        SimpleTooltip(self.clear_console_button, "Очистить консоль y-cruncher")
        SimpleTooltip(self.reset_config_button, "Сбросить конфиг из default.cfg")

        # Reserve the splitter's full width. Overlap used to cover the left
        # panel's right corners and clip the right panel at the container edge.
        half = self._splitter_width // 2
        self.console_panel.place(relx=0.0, rely=0.0, relwidth=self._split_ratio,
                                 width=-half, relheight=1.0)
        splitter = tk.Frame(top, width=self._splitter_width, bg=self.BG, cursor="sb_h_double_arrow")
        splitter.place(relx=self._split_ratio, rely=0.0, x=-self._splitter_width // 2, width=self._splitter_width, relheight=1.0)
        splitter.bind("<ButtonPress-1>", self._start_split_drag)
        splitter.bind("<B1-Motion>", self._drag_splitter)
        self.control_panel.place(relx=self._split_ratio, rely=0.0, x=self._splitter_width // 2,
                           relwidth=1.0 - self._split_ratio, width=-half, relheight=1.0)
        self._split_container = top
        self._splitter = splitter
        self._console_card = self.console_panel
        self._control_card = self.control_panel
        self.refresh_cores()
        self._update_core_controls()
        self._on_unlimited_changed()

    def _start_split_drag(self, event):

        self._split_drag_start = event.x_root

    def _drag_splitter(self, event):
        top = self._split_container
        total = top.winfo_width()
        if total <= 0:
            return


        x = event.x_root - top.winfo_rootx()

        min_ratio_left = self._split_min_left / total
        max_ratio_left = 1.0 - (self._split_min_right / total)

        ratio = x / total
        ratio = max(min_ratio_left, min(max_ratio_left, ratio))

        self._split_ratio = ratio

        half = self._splitter_width // 2

        self._console_card.place_configure(
            relwidth=ratio, width=-half
        )

        self._splitter.place_configure(
            relx=ratio,
            x=-half
        )

        self._control_card.place_configure(
            relx=ratio,
            x=half,
            relwidth=1.0 - ratio, width=-half
        )

    def toggle_theme(self):
        self.dark_mode.set(not self.dark_mode.get())
        self.apply_theme()

    def apply_theme(self):
        dark = self.dark_mode.get()
        palette = self.DARK_THEME if dark else self.LIGHT_THEME

        for name in (
            "BG", "CARD", "TEXT", "MUTED", "BLUE",
            "BLUE_HOVER", "GREEN", "RED", "RED_HOVER", "BORDER",
        ):
            setattr(self, name, palette[name])

        # Blend icon buttons into the surrounding panel instead of drawing
        # separate black/white squares behind them.
        icon_bg = self.CARD
        icon_active_bg = self.CARD
        self.telegram.configure(bg=icon_bg, activebackground=icon_active_bg)
        self.theme_button.configure(bg=icon_bg, activebackground=icon_active_bg)
        self.clear_console_button.configure(bg=icon_bg, activebackground=icon_active_bg)
        self.reset_config_button.configure(bg=icon_bg, activebackground=icon_active_bg)
        self.core_panel.set_colors(self.CARD, self.BORDER, surrounding=self.CARD)
        self.core_canvas.configure(bg=self.CARD)
        if hasattr(self, "console_scroll"):
            self.console_scroll.set_color("#555555" if dark else "#999999", palette["CONSOLE_BG"])
        if hasattr(self, "core_scroll"):
            self.core_scroll.set_color("#555555" if dark else "#999999", self.CARD)
        self.configure(bg=self.BG)
        self._recolor(self)
        self.console_panel.set_colors(self.CARD, self.BORDER, surrounding=self.BG)
        self.control_panel.set_colors(self.CARD, self.BORDER, surrounding=self.BG)
        self.console_content_panel.set_colors(palette["CONSOLE_BG"], palette["CONSOLE_BG"], surrounding=self.CARD)
        for entry in (self.duration_entry, self.seconds_per_test_entry):
            entry.configure(bg=self.CARD, fg=self.TEXT, disabledforeground=self.MUTED, insertbackground=self.TEXT,
                            border=self.BORDER, surrounding=self.CARD)
        for button in self.preset_buttons + [self.start_button, self.stop_button,
                self.telegram, self.theme_button, self.clear_console_button, self.reset_config_button, self.sensor_button]:
            button.configure(surrounding=self.CARD)
        for button in (self.start_button, self.stop_button, self.telegram, self.theme_button,
                self.clear_console_button, self.reset_config_button, self.sensor_button):
            button.configure(border='')
        self.presets_button.configure(border=self.BORDER)
        self.window_frame.theme()

        self.console.configure(
            bg=palette["CONSOLE_BG"],
            fg=palette["CONSOLE_FG"],
            insertbackground=self.TEXT,
        )
        # Console highlighting must follow the active theme as well.
        self.console.tag_configure("running", foreground=palette["GREEN"])
        self.console.tag_configure("passed", foreground=palette["GREEN"])
        self.console.tag_configure("error", foreground=palette["RED"])

        if self.sensor_window is not None:
            self.sensor_window.theme()
        if self.preset_window is not None:
            self.preset_window.theme()
        self._style.configure("TFrame", background=self.CARD)
        self._style.configure("TLabel", background=self.CARD, foreground=self.TEXT)
        self._style.configure("TCheckbutton", background=self.CARD, foreground=self.TEXT)
        self._style.configure(
            "TEntry",
            fieldbackground=self.CARD,
            foreground=self.TEXT,
        )
        if hasattr(self, "progress_canvas"):
            self.progress_canvas.configure(bg=palette["TROUGH"])
            self.progress_canvas.itemconfigure(self.progress_fill, fill=palette["BLUE"])

        scrollbar_color = "#555555" if dark else "#b8b8b8"
        scrollbar_trough = "#22252a" if dark else "#e5e5e5"
        self._style.configure(
            "Modern.Vertical.TScrollbar",
            width=5,
            background=scrollbar_color,
            darkcolor=scrollbar_color,
            lightcolor=scrollbar_color,
            bordercolor=scrollbar_trough,
            troughcolor=scrollbar_trough,
            arrowcolor=scrollbar_color,
        )

        for button in self.preset_buttons:
            button.configure(bg=self.CARD, fg=self.TEXT,
                             activebackground=self.BORDER, activeforeground=self.TEXT, border=self.BORDER)
        for slot in self.icon_frames:
            slot.configure(bg=self.CARD)
        self.sensor_button.configure(bg=self.BLUE, activebackground=self.BLUE_HOVER, fg="#ffffff", activeforeground="#ffffff")
        self.start_button.configure(bg=self.BLUE, activebackground=self.BLUE_HOVER,
                                    disabledbackground=None,
                                    disabledforeground="#9ba2ac" if dark else "#a0a0a0", fg="white", activeforeground="white")
        self.stop_button.configure(bg=self.RED, activebackground=self.RED_HOVER,
                                   disabledbackground=None,
                                   disabledforeground="#9ba2ac" if dark else "#a0a0a0", fg="white", activeforeground="white")

        # Match every bottom icon button to the active theme.
        self.telegram.configure(
            image=self.telegram_icon_dark if dark else self.telegram_icon_light,
            bg=icon_bg,
            activebackground=icon_active_bg,
        )
        self.clear_console_button.configure(
            image=self.trash_icon_dark if dark else self.trash_icon_light,
            bg=icon_bg,
            activebackground=icon_active_bg,
        )
        self.reset_config_button.configure(
            image=self.reset_icon_dark if dark else self.reset_icon_light,
            bg=icon_bg,
            activebackground=icon_active_bg,
        )
        self.theme_button.configure(
            image=self.sun_icon if dark else self.moon_icon,
            bg=icon_bg,
            fg=self.TEXT,
            activebackground=icon_active_bg,
            activeforeground=self.TEXT,
        )
        if self.status_label is not None:
            self.status_label.configure(fg=self.status_color)

    def _recolor(self, widget):
        try:
            cls = widget.winfo_class()
            if cls in ("Frame", "Labelframe"):

                if getattr(widget, "_is_card_body", False) or widget.cget("highlightthickness") == 1:
                    widget.configure(bg=self.CARD, highlightbackground=self.BORDER)
                else:
                    widget.configure(bg=self.BG)
            elif cls == "Label":
                parent = widget.nametowidget(widget.winfo_parent())
                bg = self.CARD if parent.cget("highlightthickness") == 1 else self.BG
                widget.configure(bg=bg, fg=self.TEXT if widget.cget("text") != "" else self.TEXT)
            elif cls in ("Checkbutton", "Radiobutton"):
                widget.configure(
                    bg=self.CARD, fg=self.TEXT,
                    activebackground=self.CARD, activeforeground=self.TEXT,
                    selectcolor=self.CARD
                )
                if cls == "Checkbutton":
                    icons = self._check_icons['dark' if self.dark_mode.get() else 'light']
                    widget.configure(indicatoron=False, image=icons['off'], selectimage=icons['on'],
                                     compound='left',
                                     relief='flat', offrelief='flat', overrelief='flat')
        except (tk.TclError, KeyError):
            pass

        for child in widget.winfo_children():
            self._recolor(child)

        self._fix_card_children(widget)

    def _fix_card_children(self, widget):
        try:
            if getattr(widget, "_is_card_body", False) or widget.cget("highlightthickness") == 1:
                self._set_descendant_bg(widget, self.CARD)
        except tk.TclError:
            pass

    def _set_descendant_bg(self, widget, bg):
        for child in widget.winfo_children():
            try:
                cls = child.winfo_class()
                if cls in ("Frame", "Label", "Checkbutton", "Radiobutton"):
                    child.configure(bg=bg)
            except tk.TclError:
                pass
            self._set_descendant_bg(child, bg)


    def _set_progress_value(self, value):
        self._progress_value = max(0, min(100, float(value)))
        self._update_canvas_progress()

    def _update_canvas_progress(self):
        if not hasattr(self, "progress_canvas"):
            return
        width = self.progress_canvas.winfo_width()
        if width <= 1:
            return
        self.progress_canvas.coords(
            self.progress_fill,
            0, 0, width * self._progress_value / 100, 5
        )

    def _console_follows_output(self):
        try:
            first, last = self.console.yview()
        except tk.TclError:
            return True
        return console_at_bottom(first, last)

    def _append_console(self, text):
        lines = text.splitlines(True)
        if not lines:
            return

        follow = self._console_follows_output()
        self.console.configure(state="normal")
        for line in lines:
            stripped = line.strip().lower()
            if stripped.startswith("running "):
                tag = "running"
            elif "passed test" in stripped or stripped.startswith("passed"):
                tag = "passed"
            elif "error" in stripped or "failed" in stripped:
                tag = "error"
            else:
                tag = None
            self.console.insert("end", line, tag)

        line_count = int(self.console.index("end-1c").split(".")[0])
        if line_count > self._console_max_lines:
            first_line = line_count - self._console_max_lines + 1
            self.console.delete("1.0", f"{first_line}.0")

        if follow:
            self.console.see("end")
        self.console.configure(state="disabled")

    def _clear_console(self):
        """Очистить видимую консоль y-cruncher в окне GUI."""
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def _reset_config_to_default(self):
        """Восстановить working.cfg из default.cfg и синхронизировать поля GUI."""
        if self.test_process is not None and is_process_running(self.test_process):
            messagebox.showwarning(
                "Сброс конфига",
                "Сначала остановите текущий тест y-cruncher."
            )
            return

        if not messagebox.askyesno(
            "Сброс конфига",
            "Сбросить working.cfg до значений из default.cfg?"
        ):
            return

        try:
            cfg_path = reset_working_cfg()
            default_path = get_default_cfg_path()
            cfg_text = default_path.read_text(encoding="utf-8-sig", errors="replace")

            def read_int(field):
                match = re.search(r"(?m)^\s*" + re.escape(field) + r"\s*:\s*(\d+)", cfg_text)
                return int(match.group(1)) if match else None

            seconds_total = read_int("SecondsTotal")
            seconds_per_test = read_int("SecondsPerTest")

            if seconds_per_test is not None:
                self.seconds_per_test.set(str(seconds_per_test))

            if seconds_total is not None:
                self.unlimited.set(seconds_total == 0)
                self.seconds_total.set(str(seconds_total))
                self._on_unlimited_changed()

            stop_match = re.search(r"(?mi)^\s*StopOnError\s*:\s*(true|false)", cfg_text)
            if stop_match:
                self.stop_on_error.set(stop_match.group(1).lower() == "true")

            tests_match = re.search(r"(?mi)^\s*Tests\s*:\s*\[(.*?)\]", cfg_text)
            if tests_match:
                default_tests = set(re.findall(r'"([^"]+)"', tests_match.group(1)))
                for name, var in self.test_vars.items():
                    var.set(name in default_tests)

            self._save_settings()
            self._append_console("[Launcher] Конфиг сброшен из default.cfg: %s\n" % cfg_path)
        except Exception as exc:
            messagebox.showerror("Сброс конфига", str(exc))

    def _replace_console(self, text):
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        if text:
            for line in text.splitlines(True):
                stripped = line.strip().lower()
                if stripped.startswith("running "):
                    tag = "running"
                elif "passed test" in stripped or stripped.startswith("passed"):
                    tag = "passed"
                elif "error" in stripped or "failed" in stripped or "crashed" in stripped:
                    tag = "error"
                else:
                    tag = None
                self.console.insert("end", line, tag)
        self.console.see("end")
        self.console.configure(state="disabled")

    def _poll_console(self):
        try:
            while True:
                item = self.console_queue.get_nowait()
                if isinstance(item, tuple) and len(item) == 2:
                    kind, text = item
                    if kind == "snapshot":
                        self._replace_console(text)
                    else:
                        self._handle_ycruncher_output(text)
                        self._append_console(text)
                else:
                    self._append_console(str(item))
        except queue.Empty:
            pass
        self.console_poll_job = self.after(150, self._poll_console)

    def open_sensors(self):
        if self.sensor_window is None or not self.sensor_window.winfo_exists():
            if not self.sensor_rows:
                self.sensor_status = 'Запуск HWiNFO из папки hwinfo…'
                self.sensor_service.command('start', self.hwinfo_path)
            self.sensor_window = SensorWindow(self)
        self.sensor_window.lift()

    def _poll_sensors(self):
        if self._closing:
            return
        updated = False
        while True:
            try:
                kind, value = self.sensor_service.events.get_nowait()
            except queue.Empty:
                break
            updated = True
            if kind == 'snapshot':
                self.sensor_history.update(value)
                self.sensor_rows = value
                self.sensor_status = 'Датчики подключены · обновление каждую секунду'
            else:
                self.sensor_status = value
                self.sensor_rows = []
        if self.sensor_window is not None and updated:
            status = self.sensor_status
            if self.sensor_history.error:
                status += ' · ' + self.sensor_history.error
            self.sensor_window.refresh(self.sensor_rows, status)
        self.after(500, self._poll_sensors)

    def reset_sensor_history(self):
        try:
            self.sensor_history.reset()
        except OSError as exc:
            messagebox.showerror('Сброс датчиков', 'Не удалось очистить sensor_history.json:\n%s' % exc, parent=self.sensor_window or self)
            return
        if self.sensor_window is not None:
            self.sensor_window.refresh(self.sensor_rows, self.sensor_status)

    def _settings_path(self):
        return get_app_root() / "settings.json"

    def _load_settings(self):
        try:
            path = self._settings_path()
            if not path.is_file():
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            self.hwinfo_path = str(data.get("hwinfo_path", ""))
            self.sensor_voltage_key = str(data.get("sensor_voltage_key", ""))
            self.seconds_total.set(str(data.get("seconds_total", "3600")))
            self.seconds_per_test.set(str(data.get("seconds_per_test", "120")))
            self.unlimited.set(bool(data.get("unlimited", False)))
            self.stop_on_error.set(bool(data.get("stop_on_error", True)))
            self.cpu_mode.set(data.get("cpu_mode", "all"))
            self.dark_mode.set(bool(data.get("dark_mode", True)))
            self._saved_core_indices = [int(x) for x in data.get("cores", [])]
            saved_tests = set(data.get("selected_tests", []))
            for name, var in self.test_vars.items():
                var.set(name in saved_tests)
        except Exception:
            self._saved_core_indices = []

    def _save_settings(self):
        try:
            selected = [i for i, var in self.core_vars.items() if var.get()]
            data = {
                "hwinfo_path": self.hwinfo_path,
                "sensor_voltage_key": self.sensor_voltage_key,
                "seconds_total": self.seconds_total.get(),
                "seconds_per_test": self.seconds_per_test.get(),
                "unlimited": self.unlimited.get(),
                "stop_on_error": self.stop_on_error.get(),
                "cpu_mode": self.cpu_mode.get(),
                "dark_mode": self.dark_mode.get(),
                "cores": selected,
                "selected_tests": [name for name, var in self.test_vars.items() if var.get()],
            }
            write_text_atomic(self._settings_path(),
                              json.dumps(data, ensure_ascii=False, indent=2))
        except (OSError, tk.TclError):
            # Settings are not critical for running a test; failing here must
            # not break the shutdown path.  The file is written atomically, so
            # a failure leaves the previous copy intact.
            pass

    def refresh_cores(self):
        try:
            self.cores = get_physical_cores()
        except Exception as exc:
            self.cores = []
            self._append_console("[Launcher] Ошибка определения ядер: %s\n" % exc)
        for child in self.core_frame.winfo_children():
            child.destroy()
        self.core_vars = {}
        saved = set(getattr(self, "_saved_core_indices", []))
        for core in self.cores:
            var = tk.BooleanVar(value=core.index in saved)
            self.core_vars[core.index] = var
            cb = tk.Checkbutton(self.core_frame, text=core.label(), variable=var,
                                bg=self.CARD, fg=self.TEXT, activebackground=self.CARD,
                                activeforeground=self.TEXT, selectcolor=self.CARD,
                                font=("Segoe UI", 9), highlightthickness=0, bd=0,
                                anchor="w")
            # Two columns: Core 1, Core 2 / Core 3, Core 4 / ...
            col = core.index % 2
            row = core.index // 2
            cb.grid(row=row, column=col, sticky="w", padx=8, pady=1)
            self.core_frame.grid_columnconfigure(0, weight=1)
            self.core_frame.grid_columnconfigure(1, weight=1)
        if self.cores and not saved:
            self.core_vars[0].set(True)

    def _update_core_controls(self):
        state = "normal" if self.cpu_mode.get() != "all" and not self._vf_active and not is_process_running(self.test_process) else "disabled"
        for child in self.core_frame.winfo_children():
            try:
                child.configure(state=state)
            except tk.TclError:
                pass

    def _on_unlimited_changed(self):
        if self.unlimited.get():
            self.seconds_total.set("0")
            self.duration_entry.configure(state="disabled")
        else:
            self.duration_entry.configure(state="normal")
            if not self.seconds_total.get().strip() or self.seconds_total.get().strip() == "0":
                self.seconds_total.set("3600")

    def _parse_positive_int(self, value, field):
        try:
            number = int(str(value).strip())
        except ValueError:
            raise ValueError("%s должен быть целым числом." % field)
        if number < 0 or (field != "Общая длительность" and number == 0):
            raise ValueError("%s задан некорректно." % field)
        if number > MAX_TEST_SECONDS:
            raise ValueError("%s больше %d секунд (30 суток)." % (field, MAX_TEST_SECONDS))
        return number

    def _selected_logical_cpus(self):
        selected = [i for i, var in self.core_vars.items() if var.get()]
        if not selected:
            raise ValueError("Выберите хотя бы одно физическое ядро.")
        cpus = []
        for index in selected:
            cpus.extend(self.cores[index].logical_processors)
        return selected, sorted(set(cpus))

    def _configure_cfg(self, selected_tests):
        total = 0 if self.unlimited.get() else self._parse_positive_int(self.seconds_total.get(), "Общая длительность")
        per_test = self._parse_positive_int(self.seconds_per_test.get(), "Длительность одного теста")
        update_working_cfg(selected_tests, total, per_test, self.stop_on_error.get())
        return total, per_test

    def start_selected_test(self):
        if self._vf_active:
            return
        if self.test_process is not None and is_process_running(self.test_process):
            return
        selected_tests = [name for name, var in self.test_vars.items() if var.get()]
        if not selected_tests:
            messagebox.showwarning("Выбор теста", "Сначала выберите тип теста.")
            return
        if not file_exists():
            messagebox.showerror("Ошибка", "Файл y-cruncher.exe не найден.")
            return
        try:
            total, per_test = self._configure_cfg(selected_tests)
            if self.cpu_mode.get() == "all":
                selected_indices, affinity = [], None
            else:
                selected_indices, affinity = self._selected_logical_cpus()
                if self.cpu_mode.get() == "sequential":
                    self._sequential_cores = selected_indices
                    self._sequential_index = 0
        except Exception as exc:
            messagebox.showerror("Ошибка настроек", str(exc))
            return

        self._save_settings()
        self.manual_stop_requested = False
        self._stop_event = False
        self._console_stop_event = threading.Event()
        self._seconds_total = total
        self._seconds_per_test = per_test
        self._selected_tests = list(selected_tests)
        self._selected_physical_indices = list(selected_indices)
        # Timer starts only when y-cruncher reports the first real Iteration.
        self._test_started_at = None
        self._clear_console()
        self._append_console("[Launcher] Тесты: %s\n" % ", ".join(selected_tests))
        self._append_console("[Launcher] CFG: %s\n" % get_working_cfg_path())
        if self.cpu_mode.get() == "all":
            self._append_console("[Launcher] CPU: все ядра\n")
        elif self.cpu_mode.get() == "selected":
            self._append_console("[Launcher] CPU: выбранные физические ядра %s\n" % selected_indices)
        else:
            self._append_console("[Launcher] CPU: последовательный тест %s\n" % self._sequential_cores)
        self._append_console("\n")

        self.start_button.configure(state="disabled")
        for button in self.preset_buttons:
            button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.duration_entry.configure(state="disabled")
        self.seconds_per_test_entry.configure(state="disabled")
        self._set_checkbuttons_state("disabled")
        self.stop_error_check.configure(state="disabled")
        self.unlimited_check.configure(state="disabled")
        self.mode_all.configure(state="disabled")
        self.mode_selected.configure(state="disabled")
        self.mode_sequential.configure(state="disabled")
        self._update_core_controls()
        self._set_progress_value(0)
        if self._seconds_total > 0:
            self.timer_text.set(self._format_time(self._seconds_total))
        else:
            self.timer_text.set("00:00:00")
        self.status_text.set("Запуск y-cruncher...")
        self._ycruncher_iteration_started = False
        self.status_color = self.GREEN
        self.status_label.configure(fg=self.status_color)

        if self.cpu_mode.get() == "sequential":
            self._launch_next_sequential()
        else:
            self.test_process = start_test(selected_tests, output_queue=self.console_queue, affinity_cpus=affinity, stop_event=self._console_stop_event)
            if self.test_process is None:
                self._finish_ui("Ошибка запуска")
                messagebox.showerror("Ошибка запуска", "Не удалось запустить y-cruncher.")
                return
            self._start_ycruncher_monitor()

    def open_presets(self):
        if self._vf_active or is_process_running(self.test_process):
            return
        if self.preset_window is not None and self.preset_window.winfo_exists():
            self.preset_window.lift()
            return
        self.preset_window = PresetWindow(self)

    def start_memory_preset(self, seconds):
        if self._vf_active or is_process_running(self.test_process):
            return False
        if not isinstance(seconds, int) or seconds <= 0:
            raise ValueError("Общая длительность должна быть положительной")
        for name, variable in self.test_vars.items():
            variable.set(name == "VT3")
        self.cpu_mode.set("all")
        self.stop_on_error.set(True)
        self.unlimited.set(False)
        self.seconds_total.set(str(seconds))
        self.seconds_per_test.set("120")
        self._on_unlimited_changed()
        self._update_core_controls()
        self.start_selected_test()
        return is_process_running(self.test_process)

    def start_processor_preset(self, mode):
        if self._vf_active or is_process_running(self.test_process):
            return False
        vendor = cpu_vendor(self.cpu_name)
        if vendor == "amd":
            return self.start_vf_curve(mode)
        if vendor != "intel":
            messagebox.showerror("Пресеты", "Не удалось определить AMD или Intel.")
            return False
        if not file_exists():
            messagebox.showerror("Ошибка", "Файл y-cruncher.exe не найден.")
            return False
        try:
            plan = build_intel_plan(self.cores, mode)
        except ValueError as exc:
            messagebox.showerror("Пресет процессора", str(exc))
            return False
        return self._start_processor_plan(plan, "Intel " + mode,
                                          "[Пресет Intel] Режим " + mode)

    def start_vf_curve(self, mode):
        if self._vf_active or is_process_running(self.test_process):
            return False
        if not file_exists():
            messagebox.showerror("Ошибка", "Файл y-cruncher.exe не найден.")
            return False
        try:
            if not self.cores:
                raise ValueError("Не удалось определить физические ядра CPU")
            if any(group != 0 for core in self.cores for group in core.groups):
                raise ValueError("V/F curve поддерживает одну группу процессоров Windows")
            ccds = detect_ccds(self.cores, get_app_root())
            plan = build_plan(self.cores, ccds, mode)
        except Exception as exc:
            messagebox.showerror("V/F curve: топология", str(exc))
            return False
        return self._start_processor_plan(plan, "V/F curve",
            "[V/F] Режим %s; CCD (ядра с 1): %s" %
            (mode, [[i + 1 for i in ccd] for ccd in ccds]))

    def _start_processor_plan(self, plan, title, intro):
        self._user_selection_backup = self._capture_user_selection()
        self._vf_plan = plan
        self._vf_label = title
        self._vf_active = True
        self._vf_failed = False
        self._vf_index = 0
        self._vf_completed_seconds = 0
        self._vf_total = sum(stage["duration"] for stage in self._vf_plan)
        self._vf_stop_on_error = self.stop_on_error.get()
        self._set_progress_value(0)
        self.timer_text.set(self._format_time(self._vf_total))
        self.unlimited.set(False)
        self.seconds_total.set(str(self._vf_total))
        self.seconds_per_test.set(str(plan[0]["seconds"]))
        self.manual_stop_requested = False
        self._stop_event = False
        self._console_stop_event = threading.Event()
        self._clear_console()
        self._append_console(intro + "\n")
        self._append_console("[V/F] План: %d этапов, примерно %s + запуск/переходы\n" %
                             (len(self._vf_plan), self._format_time(self._vf_total)))
        self.start_button.configure(state="disabled")
        for button in self.preset_buttons:
            button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        for widget in (self.duration_entry, self.seconds_per_test_entry,
                       self.stop_error_check, self.unlimited_check,
                       self.mode_all, self.mode_selected, self.mode_sequential):
            widget.configure(state="disabled")
        self._set_checkbuttons_state("disabled")
        self._update_core_controls()
        self.status_color = self.GREEN
        self.status_label.configure(fg=self.GREEN)
        self._launch_vf_stage()
        return self._vf_active

    def _launch_vf_stage(self):
        if self._stop_event or not self._vf_active:
            return
        stage = self._vf_plan[self._vf_index]
        for name, variable in self.test_vars.items():
            variable.set(name in stage["tests"])
        targets = set(stage["core_indices"])
        for index, variable in self.core_vars.items():
            variable.set(index in targets)
        self.cpu_mode.set("all" if len(targets) == len(self.cores) else "selected")
        self._vf_tracker = IterationTracker(stage["iterations"])
        self._vf_boundary = False
        self._vf_stage_error = False
        self._vf_stopper = None
        self._vf_stop_result = None
        self._vf_queue = queue.Queue()
        self._test_started_at = None
        self._vf_launched_at = time.monotonic()
        self._append_console("\n[V/F] Этап %d/%d: %s; CPU %s; %d итераций; %s по %d с\n" %
                             (self._vf_index + 1, len(self._vf_plan), stage["label"],
                              stage["cpus"], stage["iterations"],
                              ", ".join(stage["tests"]), stage["seconds"]))
        try:
            # Unlimited total duration: stop at an actual iteration boundary,
            # so allocation time cannot eat into the requested pass count.
            update_working_cfg(stage["tests"], 0, stage["seconds"],
                               self._vf_stop_on_error, logical_cpus=stage["cpus"])
            self.test_process = start_test(stage["tests"], output_queue=self._vf_queue,
                                          affinity_cpus=stage["cpus"],
                                          stop_event=self._console_stop_event)
            if self.test_process is None:
                raise RuntimeError("Не удалось запустить y-cruncher")
        except Exception as exc:
            self._append_console("[V/F] Ошибка запуска: %s\n" % exc)
            self._finish_ui("Ошибка запуска " + getattr(self, "_vf_label", "V/F curve"))
            return
        self.status_text.set(format_stage_status(stage, 0, self._vf_index, len(self._vf_plan)))
        self._start_ycruncher_monitor()

    def _stop_vf_stage(self):
        if self._vf_stopper is not None:
            return
        process = self.test_process
        result_queue = self._vf_queue
        def terminate():
            result_queue.put(("stop_result", stop_process(process)))
        self._vf_stopper = threading.Thread(target=terminate, daemon=True)
        self._vf_stopper.start()

    def _drain_vf_output(self):
        while True:
            try:
                kind, text = self._vf_queue.get_nowait()
            except queue.Empty:
                return
            if kind == "stop_result":
                self._vf_stop_result = text
                continue
            self._append_console(text)
            for line in text.splitlines():
                if FAILURE.search(line):
                    self._vf_stage_error = self._vf_failed = True
                    if self._vf_stop_on_error:
                        self._stop_vf_stage()
                try:
                    boundary = self._vf_tracker.feed(line)
                except ValueError:
                    self._vf_stage_error = self._vf_failed = True
                    self._stop_vf_stage()
                    continue
                if self._vf_tracker.first is not None and self._test_started_at is None:
                    self._test_started_at = time.monotonic()
                if boundary and not (self._vf_stage_error and self._vf_stop_on_error):
                    self._vf_boundary = True
                    self._stop_vf_stage()

    def _monitor_vf_curve(self):
        self.monitor_ycruncher_job = None
        if self.manual_stop_requested or not self._vf_active:
            return
        self._drain_vf_output()
        stage = self._vf_plan[self._vf_index]
        elapsed = (time.monotonic() - self._test_started_at
                   if self._test_started_at is not None else 0)
        done = self._vf_completed_seconds + min(elapsed, stage["duration"])
        self.timer_text.set(self._format_time(self._vf_total - done))
        self._set_progress_value(100 * done / self._vf_total)
        if self._test_started_at is not None:
            completed = self._vf_tracker.last - self._vf_tracker.first + 1
            self.status_text.set(format_stage_status(
                stage, completed, self._vf_index, len(self._vf_plan)))
        if time.monotonic() - self._vf_launched_at > stage["duration"] + max(180, stage["duration"]):
            if self._vf_stopper is None:
                self._append_console("[V/F] Нет завершения итераций в ожидаемый срок.\n")
                self._vf_stage_error = self._vf_failed = True
                self._stop_vf_stage()
        process = self.test_process
        if not is_process_running(process) and process.output_done.is_set():
            self._drain_vf_output()
            if self._vf_stopper is not None and self._vf_stopper.is_alive():
                self.monitor_ycruncher_job = self.after(100, self._monitor_ycruncher)
                return
            if (not self._vf_boundary or
                    (self._vf_stage_error and self._vf_stop_on_error) or
                    self._vf_stop_result is False):
                self._finish_ui("Ошибка " + getattr(self, "_vf_label", "V/F curve") + ": " + stage["label"])
                return
            self._append_console("[V/F] Этап завершён: %s\n" % stage["label"])
            self._vf_completed_seconds += stage["duration"]
            self._vf_index += 1
            self.test_process = None
            if self._vf_index == len(self._vf_plan):
                self._set_progress_value(100)
                self.timer_text.set("00:00:00")
                self._finish_ui((getattr(self, "_vf_label", "V/F curve") + " завершён с ошибками") if self._vf_failed else "Тест завершён")
            else:
                self._launch_vf_stage()
            return
        self.monitor_ycruncher_job = self.after(150, self._monitor_ycruncher)

    def _launch_next_sequential(self):
        """Run one y-cruncher process and switch affinity on real y-cruncher iterations.

        y-cruncher prints an ``Iteration: N`` line when it reaches the next
        stress-test iteration.  That line is our synchronization point.
        We therefore do not use SecondsPerTest or a GUI ``after()`` timer to
        decide when to switch CPUs.  SecondsPerTest remains owned by
        y-cruncher itself.
        """
        if self._stop_event:
            return
        if not self._sequential_cores:
            self._finish_ui("Нет выбранных ядер")
            return

        self._sequential_index = 0
        self._last_ycruncher_iteration = None
        self.status_text.set("Запуск y-cruncher...")
        self._append_console("\n[Launcher] === Последовательный режим: синхронизация по Iteration ===\n")
        self._append_console("[Launcher] Ядра по очереди: %s\n" % self._sequential_cores)
        self._append_console("[Launcher] Affinity будет назначаться по реальным строкам y-cruncher 'Iteration: N'.\n")

        # Start with no affinity.  The first affinity is applied when the
        # actual ``Iteration: 0`` event arrives from y-cruncher.
        self.test_process = start_test(
            self._selected_tests,
            output_queue=self.console_queue,
            affinity_cpus=None,
            stop_event=self._console_stop_event,
        )
        if self.test_process is None:
            self._finish_ui("Ошибка запуска")
            return

        self._start_ycruncher_monitor()

    def _handle_ycruncher_output(self, text):
        """Parse y-cruncher iteration events and update startup status."""
        if self._stop_event or self.test_process is None:
            return

        # Until the first real iteration appears y-cruncher is still loading.
        # Show a clear state instead of implying that the benchmark has started.
        if not self._ycruncher_iteration_started:
            for line in str(text).splitlines():
                if re.search(r"^\s*Iteration\s*:\s*\d+\b", line, re.IGNORECASE):
                    self._ycruncher_iteration_started = True
                    self._test_started_at = time.monotonic()
                    if self.cpu_mode.get() != "sequential":
                        self.status_text.set(format_cpu_scope(
                            self.cpu_mode.get(), getattr(self, '_selected_physical_indices', [])))
                    break

        if self.cpu_mode.get() != "sequential":
            return

        # y-cruncher emits, for example:
        #   Iteration: 0  Total Elapsed Time: ...
        #   Iteration: 1  Total Elapsed Time: ...
        # The transition to a new iteration is therefore a much better
        # synchronization point than a GUI timer based on SecondsPerTest.
        for line in str(text).splitlines():
            match = re.search(r"^\s*Iteration\s*:\s*(\d+)\b", line, re.IGNORECASE)
            if not match:
                continue

            iteration = int(match.group(1))
            if self._last_ycruncher_iteration == iteration:
                continue
            self._last_ycruncher_iteration = iteration
            self._apply_sequential_iteration(iteration)

    def _apply_sequential_iteration(self, iteration):
        if self._stop_event or self.test_process is None or not is_process_running(self.test_process):
            return
        if not self._sequential_cores:
            return

        self._sequential_index = iteration % len(self._sequential_cores)
        core_index = self._sequential_cores[self._sequential_index]
        core = self.cores[core_index]

        try:
            set_process_affinity(
                self.test_process.pid,
                core.logical_processors,
                self.console_queue,
            )
            self.status_text.set(
                "Последовательно: Iteration %d → ядро %d" %
                (iteration, core_index + 1)
            )
            self._append_console(
                "[Launcher] Iteration %d → нагрузка назначена на Core %d [%s] / CPUs %s\n" %
                (iteration, core_index + 1, core.core_type, list(core.logical_processors))
            )
        except Exception as exc:
            self._append_console(
                "[Launcher] Ошибка назначения Core %d на Iteration %d: %s\n" %
                (core_index + 1, iteration, exc)
            )

    def _start_ycruncher_monitor(self):
        if self.monitor_ycruncher_job is None:
            self.monitor_ycruncher_job = self.after(500, self._monitor_ycruncher)

    def _monitor_ycruncher(self):
        if self._vf_active:
            self._monitor_vf_curve()
            return
        self.monitor_ycruncher_job = None
        if self.manual_stop_requested:
            return
        elapsed = time.monotonic() - (self._test_started_at or time.monotonic())
        if self._seconds_total > 0:
            self.timer_text.set(self._format_time(max(0, int(self._seconds_total - elapsed))))
            self._set_progress_value(min(100, elapsed / self._seconds_total * 100))
        else:
            self.timer_text.set(self._format_time(int(elapsed)))
            self._set_progress_value(0)
        if not is_process_running(self.test_process):
            process, self.test_process = self.test_process, None
            if not self._stop_event:
                self._finish_ui(completion_status(getattr(process, "returncode", 0)))
            return
        self.monitor_ycruncher_job = self.after(500, self._monitor_ycruncher)

    @staticmethod
    def _format_time(seconds):
        h, rem = divmod(max(0, int(seconds)), 3600)
        m, s = divmod(rem, 60)
        return "%02d:%02d:%02d" % (h, m, s)

    def _finish_ui(self, status):
        if self._finishing:
            return
        self._finishing = True
        self._cancel_monitor_job()
        self.status_text.set(status)
        self.status_color = self.GREEN if status == "Тест завершён" else self.RED
        if self.status_label is not None:
            self.status_label.configure(fg=self.status_color)
        self._vf_active = False
        self.start_button.configure(state="normal")
        for button in self.preset_buttons:
            button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.duration_entry.configure(state="disabled" if self.unlimited.get() else "normal")
        self.seconds_per_test_entry.configure(state="normal")
        self._set_checkbuttons_state("normal")
        self.stop_error_check.configure(state="normal")
        self.unlimited_check.configure(state="normal")
        self.mode_all.configure(state="normal")
        self.mode_selected.configure(state="normal")
        self.mode_sequential.configure(state="normal")
        self._update_core_controls()
        self.test_process = None
        self._test_started_at = None
        if self._console_stop_event is not None:
            self._console_stop_event.set()
        self._console_stop_event = None
        self.manual_stop_requested = False
        self._stop_event = False
        self._finishing = False
        self._restore_user_selection()
        self._save_settings()

    def _cancel_monitor_job(self):
        job, self.monitor_ycruncher_job = self.monitor_ycruncher_job, None
        if job is None:
            return
        try:
            self.after_cancel(job)
        except tk.TclError:
            pass

    def report_callback_exception(self, exc, value, tb):
        """Tk callbacks run without a console in the packaged EXE.

        Without this hook an exception inside a button handler is dropped
        silently and the window simply stops reacting.  Keep the traceback in
        error.log beside the application and tell the user once.
        """
        self._log_error("".join(traceback.format_exception(exc, value, tb)))
        if self._error_dialog_shown or self._closing:
            return
        self._error_dialog_shown = True
        try:
            messagebox.showerror("Ошибка интерфейса",
                                 "Подробности записаны в error.log рядом с программой.")
        except tk.TclError:
            pass

    def _log_error(self, text):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with (get_app_root() / "error.log").open("a", encoding="utf-8") as handle:
                handle.write("[%s]\n%s\n" % (stamp, text))
        except OSError:
            pass

    def _capture_user_selection(self):
        """Remember the fields a preset is about to overwrite.

        Processor presets rewrite the test list, the core selection and the
        duration fields for their own stages.  Without a snapshot those values
        were saved to settings.json when the run ended, so one preset silently
        replaced the user's own configuration.
        """
        return {
            "tests": {name: var.get() for name, var in self.test_vars.items()},
            "cores": {index: var.get() for index, var in self.core_vars.items()},
            "cpu_mode": self.cpu_mode.get(),
            "seconds_total": self.seconds_total.get(),
            "seconds_per_test": self.seconds_per_test.get(),
            "unlimited": self.unlimited.get(),
        }

    def _restore_user_selection(self):
        saved, self._user_selection_backup = self._user_selection_backup, None
        if not saved:
            return
        try:
            for name, value in saved["tests"].items():
                if name in self.test_vars:
                    self.test_vars[name].set(value)
            for index, value in saved["cores"].items():
                if index in self.core_vars:
                    self.core_vars[index].set(value)
            self.cpu_mode.set(saved["cpu_mode"])
            self.seconds_total.set(saved["seconds_total"])
            self.seconds_per_test.set(saved["seconds_per_test"])
            self.unlimited.set(saved["unlimited"])
            self._on_unlimited_changed()
            self._update_core_controls()
        except (KeyError, tk.TclError):
            pass

    def stop_test(self):
        if self.test_process is None or not is_process_running(self.test_process):
            return
        if not messagebox.askyesno("Остановить тест", "Вы действительно хотите остановить текущий тест?"):
            return
        process, self.test_process = self.test_process, None
        self.manual_stop_requested = True
        self._stop_event = True
        if self._console_stop_event is not None:
            self._console_stop_event.set()
        self._cancel_monitor_job()
        self.status_text.set("Остановка y-cruncher…")
        self.status_label.configure(fg=self.MUTED)
        self.stop_button.configure(state="disabled")
        # taskkill + wait used to run on the Tk thread, freezing the window
        # for several seconds.  Stop the process in a worker and poll for it.
        done = threading.Event()

        def terminate():
            try:
                stop_process(process)
            finally:
                done.set()

        threading.Thread(target=terminate, name="ycruncher-stop", daemon=True).start()
        self._await_process_stop(done)

    def _await_process_stop(self, done):
        if self._closing:
            return
        if done.is_set():
            self._finish_ui("Тест остановлен")
            return
        self.after(50, lambda: self._await_process_stop(done))

    def _set_checkbuttons_state(self, state):
        for widget in self._checkbuttons:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass

    def open_telegram(self):
        webbrowser.open("https://t.me/overkloking")

    def on_close(self):
        if self.test_process is not None and is_process_running(self.test_process):
            if not messagebox.askyesno("Закрыть приложение", "Тест сейчас выполняется.\n\nОстановить тест и закрыть приложение?"):
                return
            self._stop_event = True
            if self._console_stop_event is not None:
                self._console_stop_event.set()
            # Kill off the Tk thread but keep the close responsive: taskkill
            # plus a process wait can take several seconds.
            stopper = threading.Thread(target=stop_process, args=(self.test_process,),
                                       name="ycruncher-close-stop", daemon=True)
            stopper.start()
            stopper.join(2.0)
        self._save_settings()
        if self.console_poll_job is not None:
            try:
                self.after_cancel(self.console_poll_job)
            except tk.TclError:
                pass
        self._closing = True
        self.sensor_service.close()
        self.withdraw()
        self._close_deadline = time.monotonic() + 12
        self._wait_sensor_shutdown()

    def _wait_sensor_shutdown(self):
        if self.sensor_service.thread.is_alive() and time.monotonic() < self._close_deadline:
            self.after(100, self._wait_sensor_shutdown)
        else:
            self.destroy()

def _detach_python_console():
    # When ui.py is launched with python.exe, Windows gives Python its own
    # console. y-cruncher needs its own real console, so detach the GUI from
    # the Python console before y-cruncher is created. When launched with
    # pythonw.exe or as a --noconsole PyInstaller build there is simply no
    # console and FreeConsole() is harmless.
    if sys.platform == "win32":
        try:
            ctypes.WinDLL("kernel32", use_last_error=True).FreeConsole()
        except Exception:
            pass


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        from self_test import run_tests
        raise SystemExit(run_tests())
    if '--debug' not in sys.argv:
        _detach_python_console()
    app = LauncherApp()

    def _thread_exception(args):
        app._log_error("Поток %s завершился с ошибкой:\n%s" % (
            getattr(args.thread, 'name', '?'),
            "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))))

    threading.excepthook = _thread_exception
    app.mainloop()
