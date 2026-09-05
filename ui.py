import tkinter as tk
from pathlib import Path
import sys
from tkinter import messagebox, ttk
import webbrowser
import queue

from timer import TestTimer
from utils import (
    start_test,
    stop_process,
    is_process_running,
    file_exists,
)
from test_selector import TESTS

class SimpleTooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        self.job = None

        widget.bind("<Enter>", self.schedule)
        widget.bind("<Leave>", self.hide)

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
    def _get_icon_path():
        candidates = []

        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidates.append(exe_dir / "launcher.ico")
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                candidates.append(Path(meipass) / "launcher.ico")
        else:
            project_root = Path(__file__).resolve().parents[1]
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

        self.title("y-cruncher GUI 2.0 by @rx580_2048")
        self.geometry("1400x800")
        self.minsize(1000, 650)
        self.resizable(True, True)

        self.timer = None
        self.test_process = None
        self.monitor_ycruncher_job = None
        self.console_poll_job = None
        self.manual_stop_requested = False
        self.test_vars = {}
        self.console_queue = queue.Queue()
        self._checkbuttons = []
        self._console_max_lines = 5000

        for name, _ in TESTS:
            self.test_vars[name] = tk.BooleanVar(value=False)

        self.duration = tk.StringVar(value="60")
        self.status_text = tk.StringVar(value="Тест не запущен")
        self.timer_text = tk.StringVar(value="00:00:00")
        self.status_color = self.MUTED
        self.status_label = None
        self.dark_mode = tk.BooleanVar(value=True)

        self._setup_style()
        self._build_ui()
        self.apply_theme()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self._poll_console()

    def _setup_style(self):
        self._style = ttk.Style(self)
        style = self._style
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TEntry", padding=8, font=("Segoe UI", 11))
        style.configure("Horizontal.TProgressbar", borderwidth=0, thickness=8)

    def _build_ui(self):
        self.configure(bg=self.BG)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)


        top = tk.Frame(self, bg=self.BG, bd=0)
        top.grid(row=0, column=0, sticky="nsew", padx=18, pady=16)


        top.grid_rowconfigure(0, weight=1)


        self._split_ratio = 4 / 6
        self._split_min_left = 250
        self._split_min_right = 230
        self._splitter_width = 8

        console_card = tk.Frame(
            top, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1
        )
        console_card.grid_rowconfigure(1, weight=1)
        console_card.grid_columnconfigure(0, weight=1)

        tk.Label(
            console_card, text="y-cruncher",
            font=("Segoe UI", 11, "bold"), fg=self.TEXT, bg=self.CARD
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 8))

        console_wrap = tk.Frame(console_card, bg=self.CARD)
        console_wrap.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        console_wrap.grid_rowconfigure(0, weight=1)
        console_wrap.grid_columnconfigure(0, weight=1)

        self.console = tk.Text(
            console_wrap, wrap="none", font=("Consolas", 10),
            bg="#101214", fg="#d9e1e8", insertbackground="#ffffff",
            relief="flat", bd=0, padx=10, pady=8, state="disabled"
        )
        self.console.grid(row=0, column=0, sticky="nsew")
        self.console.tag_configure("running", foreground="#35c98a")
        self.console.tag_configure("passed", foreground="#35c98a")
        self.console.tag_configure("error", foreground="#ef5b63")

        console_scroll = ttk.Scrollbar(
            console_wrap, orient="vertical", command=self.console.yview
        )
        console_scroll.grid(row=0, column=1, sticky="ns")
        self.console.configure(yscrollcommand=console_scroll.set)

        control_card = tk.Frame(
            top, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1
        )
        control_card.grid_columnconfigure(0, weight=1)
        control_card.grid_columnconfigure(1, weight=0)

        control_card.grid_rowconfigure(5, weight=1)

        tk.Label(
            control_card, text="Выберите тест:",
            font=("Segoe UI", 12, "bold"), fg=self.TEXT, bg=self.CARD
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 14))

        help_btn = tk.Label(
            control_card, text="?", font=("Segoe UI", 9, "bold"),
            fg="white", bg="#1b1b1b", relief="solid", bd=1,
            padx=5, pady=1, cursor="hand2"
        )
        help_btn.grid(row=0, column=1, sticky="e", padx=18, pady=(16, 14))

        SimpleTooltip(
            help_btn,
            "Для теста процессора рекомендуется:\n"
            "• BKT + SFTv4 + VT3\n\n"
            "Для теста оперативной памяти рекомендуется:\n"
            "• VT3"
        )


        test_frame = tk.Frame(control_card, bg=self.CARD)
        test_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=18)
        test_frame.grid_columnconfigure(0, weight=1, uniform="test_column")
        test_frame.grid_columnconfigure(1, weight=1, uniform="test_column")

        for index, (name, _) in enumerate(TESTS):
            row = index // 2
            column = index % 2
            checkbutton = tk.Checkbutton(
                test_frame,
                text=name,
                variable=self.test_vars[name],
                bg=self.CARD,
                fg=self.TEXT,
                activebackground=self.CARD,
                activeforeground=self.TEXT,
                selectcolor=self.CARD,
                font=("Segoe UI", 9),
                highlightthickness=0,
                bd=0,
                anchor="w",
            )
            checkbutton.grid(row=row, column=column, sticky="w", pady=2)
            self._checkbuttons.append(checkbutton)

        separator = tk.Frame(control_card, height=1, bg=self.BORDER)
        separator.grid(row=2, column=0, columnspan=2, sticky="ew", padx=18, pady=14)

        tk.Label(
            control_card, text="Продолжительность теста (минут)",
            font=("Segoe UI", 9), fg=self.MUTED, bg=self.CARD
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 6))

        self.duration_entry = ttk.Entry(
            control_card, textvariable=self.duration, justify="center"
        )
        self.duration_entry.grid(row=4, column=0, columnspan=2, sticky="ew", padx=18)


        bottom = tk.Frame(control_card, bg=self.CARD)
        bottom.grid(row=6, column=0, columnspan=2, sticky="ew", padx=18, pady=(18, 16))
        bottom.grid_columnconfigure(0, weight=1)

        tk.Label(
            bottom, textvariable=self.timer_text,
            font=("Consolas", 18, "bold"), fg=self.TEXT, bg=self.CARD
        ).grid(row=0, column=0, pady=(0, 2))

        self.status_label = tk.Label(
            bottom, textvariable=self.status_text,
            font=("Segoe UI", 9, "bold"), fg=self.status_color, bg=self.CARD
        )
        self.status_label.grid(row=1, column=0, pady=(0, 10))

        self.progress = ttk.Progressbar(
            bottom, style="Horizontal.TProgressbar",
            mode="determinate", maximum=100
        )
        self.progress.grid(row=2, column=0, sticky="ew", pady=(0, 14))

        self.start_button = tk.Button(
            bottom, text="Запустить тест", command=self.start_selected_test,
            font=("Segoe UI", 10, "bold"), fg="white", bg=self.BLUE,
            activeforeground="white", activebackground=self.BLUE_HOVER,
            relief="flat", cursor="hand2", bd=0
        )
        self.start_button.grid(row=3, column=0, sticky="ew", pady=(0, 7))

        self.stop_button = tk.Button(
            bottom, text="Остановить тест", command=self.stop_test,
            font=("Segoe UI", 10, "bold"), fg="white", bg=self.RED,
            activeforeground="white", activebackground=self.RED_HOVER,
            disabledforeground="white", relief="flat", cursor="hand2",
            bd=0, state="disabled"
        )
        self.stop_button.grid(row=4, column=0, sticky="ew")

        self.telegram = tk.Button(
            bottom, text="Telegram", command=self.open_telegram,
            font=("Segoe UI", 9, "bold"), fg="white", bg=self.BLUE,
            activeforeground="white", activebackground=self.BLUE_HOVER,
            relief="flat", cursor="hand2", bd=0
        )
        self.telegram.grid(row=5, column=0, sticky="ew", pady=(18, 7))

        self.theme_button = tk.Button(
            bottom, text="☀", command=self.toggle_theme,
            font=("Segoe UI", 8, "bold"), relief="flat", cursor="hand2", bd=0
        )
        self.theme_button.grid(row=6, column=0)


        console_card.place(
            relx=0.0, rely=0.0,
            relwidth=self._split_ratio, relheight=1.0
        )

        splitter = tk.Frame(
            top,
            width=self._splitter_width,
            bg=self.BG,
            cursor="sb_h_double_arrow"
        )
        splitter.place(
            relx=self._split_ratio,
            rely=0.0,
            x=-self._splitter_width // 2,
            width=self._splitter_width,
            relheight=1.0
        )
        splitter.bind("<ButtonPress-1>", self._start_split_drag)
        splitter.bind("<B1-Motion>", self._drag_splitter)

        control_card.place(
            relx=self._split_ratio,
            rely=0.0,
            x=self._splitter_width // 2,
            relwidth=1.0 - self._split_ratio,
            relheight=1.0
        )

        self._split_container = top
        self._splitter = splitter
        self._console_card = console_card
        self._control_card = control_card

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
            relwidth=ratio
        )

        self._splitter.place_configure(
            relx=ratio,
            x=-half
        )

        self._control_card.place_configure(
            relx=ratio,
            x=half,
            relwidth=1.0 - ratio
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

        self.theme_button.configure(text="☀" if dark else "☾")
        self.configure(bg=self.BG)
        self._recolor(self)

        self.console.configure(
            bg=palette["CONSOLE_BG"],
            fg=palette["CONSOLE_FG"],
            insertbackground=self.TEXT,
        )

        self._style.configure(
            "TEntry",
            fieldbackground=self.CARD,
            foreground=self.TEXT,
        )
        self._style.configure(
            "Horizontal.TProgressbar",
            troughcolor=palette["TROUGH"],
            background=self.BLUE,
        )

        self.start_button.configure(bg=self.BLUE, activebackground=self.BLUE_HOVER)
        self.stop_button.configure(bg=self.RED, activebackground=self.RED_HOVER)
        self.telegram.configure(bg=self.BLUE, activebackground=self.BLUE_HOVER)
        self.theme_button.configure(
            bg=self.CARD,
            fg=self.TEXT,
            activebackground=self.BORDER,
            activeforeground=self.TEXT,
        )
        if self.status_label is not None:
            self.status_label.configure(fg=self.status_color)

    def _recolor(self, widget):
        try:
            cls = widget.winfo_class()
            if cls in ("Frame", "Labelframe"):

                if widget.cget("highlightthickness") == 1:
                    widget.configure(bg=self.CARD, highlightbackground=self.BORDER)
                else:
                    widget.configure(bg=self.BG)
            elif cls == "Label":
                parent = widget.nametowidget(widget.winfo_parent())
                bg = self.CARD if parent.cget("highlightthickness") == 1 else self.BG
                widget.configure(bg=bg, fg=self.TEXT if widget.cget("text") != "" else self.TEXT)
            elif cls == "Checkbutton":
                widget.configure(
                    bg=self.CARD, fg=self.TEXT,
                    activebackground=self.CARD, activeforeground=self.TEXT,
                    selectcolor=self.CARD
                )
        except (tk.TclError, KeyError):
            pass

        for child in widget.winfo_children():
            self._recolor(child)

        self._fix_card_children(widget)

    def _fix_card_children(self, widget):
        try:
            if widget.cget("highlightthickness") == 1:
                self._set_descendant_bg(widget, self.CARD)
        except tk.TclError:
            pass

    def _set_descendant_bg(self, widget, bg):
        for child in widget.winfo_children():
            try:
                cls = child.winfo_class()
                if cls in ("Frame", "Label", "Checkbutton"):
                    child.configure(bg=bg)
            except tk.TclError:
                pass
            self._set_descendant_bg(child, bg)

    def _append_console(self, text):
        lines = text.splitlines(True)
        if not lines:
            return

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

        self.console.see("end")
        self.console.configure(state="disabled")

    def _clear_console(self):
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def _poll_console(self):
        try:
            while True:
                text = self.console_queue.get_nowait()
                self._append_console(text)
        except queue.Empty:
            pass
        self.console_poll_job = self.after(150, self._poll_console)

    def start_selected_test(self):
        if self.timer and self.timer.is_running():
            return

        selected_tests = [name for name, var in self.test_vars.items() if var.get()]
        if not selected_tests:
            messagebox.showwarning("Выбор теста", "Сначала выберите тип теста.")
            return

        try:
            minutes = float(self.duration.get().replace(",", "."))
            if minutes <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Ошибка", "Введите корректную длительность теста.")
            return

        if not file_exists():
            messagebox.showerror("Ошибка", "Файл y-cruncher.exe не найден.")
            return

        self.manual_stop_requested = False
        self._finishing = False
        self._clear_console()
        self._append_console(
            f"[Launcher] Запуск тестов: {', '.join(selected_tests)}\n"
            f"[Launcher] Длительность: {minutes:g} мин.\n\n"
        )

        self.test_process = start_test(
            selected_tests, minutes, output_queue=self.console_queue
        )

        if self.test_process is None:
            messagebox.showerror("Ошибка запуска", "Не удалось запустить y-cruncher.")
            return

        self.status_text.set("Тест выполняется")
        self.status_color = self.GREEN


        if self.status_label is not None:
            self.status_label.configure(fg=self.status_color)
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.duration_entry.configure(state="disabled")

        self._set_checkbuttons_state("disabled")

        self.progress["value"] = 0
        self.timer = TestTimer(
            minutes=minutes,
            on_tick=self.on_timer_tick,
            on_finish=self.on_timer_finish,
            on_stop=self.on_timer_stop
        )
        self.timer.start()
        self._start_ycruncher_monitor()

    def _start_ycruncher_monitor(self):
        if self.monitor_ycruncher_job is None:
            self.monitor_ycruncher_job = self.after(1000, self._monitor_ycruncher)

    def _monitor_ycruncher(self):
        self.monitor_ycruncher_job = None
        if self.manual_stop_requested or not self.timer or not self.timer.is_running():
            return

        if not is_process_running(self.test_process):
            remaining_is_done = False
            remaining_is_done = self.timer.get_remaining() <= 1

            self.manual_stop_requested = True
            self.timer.stop()
            self._finish_ui("Тест завершён" if remaining_is_done else "Тест завершён досрочно")
            return

        self.monitor_ycruncher_job = self.after(1000, self._monitor_ycruncher)

    def on_timer_tick(self, hours, minutes, seconds):
        self.after(0, self._update_timer_ui, hours, minutes, seconds)

    def _update_timer_ui(self, hours, minutes, seconds):
        if not self.winfo_exists():
            return
        self.timer_text.set(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        self.progress["value"] = self.timer.get_progress() * 100 if self.timer else 0

    def on_timer_finish(self):
        self.after(0, self._finish_from_timer)

    def _finish_from_timer(self):
        if not self.winfo_exists():
            return
        self._stop_test_process()
        self._finish_ui("Тест завершён")

    def on_timer_stop(self):
        if not self.manual_stop_requested:
            self._finish_ui("Тест остановлен")

    def _finish_ui(self, status):
        if self._finishing:
            return
        self._finishing = True

        if self.monitor_ycruncher_job is not None:
            try:
                self.after_cancel(self.monitor_ycruncher_job)
            except tk.TclError:
                pass
            self.monitor_ycruncher_job = None

        self.status_text.set(status)
        self.status_color = self.GREEN if status == "Тест завершён" else self.RED
        if self.status_label is not None:
            self.status_label.configure(fg=self.status_color)
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.duration_entry.configure(state="normal")

        self._set_checkbuttons_state("normal")

        self.timer = None
        self.test_process = None
        self.manual_stop_requested = False
        self._finishing = False

    def stop_test(self):
        if not self.timer or not self.timer.is_running():
            return

        if not messagebox.askyesno(
            "Остановить тест",
            "Вы действительно хотите остановить текущий тест?"
        ):
            return

        self.manual_stop_requested = True
        if self.monitor_ycruncher_job is not None:
            try:
                self.after_cancel(self.monitor_ycruncher_job)
            except tk.TclError:
                pass
            self.monitor_ycruncher_job = None

        self.timer.stop()
        self._stop_test_process()
        self._finish_ui("Тест остановлен")

    def _stop_test_process(self):
        process = self.test_process
        self.test_process = None
        if process is not None:
            stop_process(process)

    def _set_checkbuttons_state(self, state):
        for widget in self._checkbuttons:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass

    def open_telegram(self):
        webbrowser.open("https://t.me/overkloking")

    def on_close(self):
        if self.timer and self.timer.is_running():
            if not messagebox.askyesno(
                "Закрыть приложение",
                "Тест сейчас выполняется.\n\nОстановить тест и закрыть приложение?"
            ):
                return

            self.manual_stop_requested = True
            if self.monitor_ycruncher_job is not None:
                try:
                    self.after_cancel(self.monitor_ycruncher_job)
                except tk.TclError:
                    pass
            self.timer.stop()
            self._stop_test_process()
            self.monitor_ycruncher_job = None

        else:
            process = self.test_process
            if process is not None:
                try:
                    if is_process_running(process):
                        stop_process(process)
                except Exception:
                    pass

        for job in (self.console_poll_job,):
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass

        self.destroy()

if __name__ == "__main__":
    app = LauncherApp()
    app.mainloop()
