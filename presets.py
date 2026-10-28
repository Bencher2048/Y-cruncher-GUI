"""Processor and RAM preset plans and their small selection windows."""
import re
import tkinter as tk
from tkinter import messagebox

from widgets import RoundedButton, RoundedEntry, RoundedPanel
from window_frame import WindowFrame


def cpu_vendor(name):
    if re.search(r'\bintel\b|genuineintel', name, re.I):
        return 'intel'
    if re.search(r'\bamd\b|\bryzen\b|\bthreadripper\b|authenticamd', name, re.I):
        return 'amd'
    return None


def memory_duration(value):
    """The preset duration field is in whole minutes, converted once to seconds."""
    text = str(value).strip()
    if not re.fullmatch(r'[0-9]+', text) or int(text) < 1:
        raise ValueError('Введите целое положительное время теста в минутах.')
    return int(text) * 60


def build_intel_plan(cores, mode):
    if mode not in ('fast', 'full'):
        raise ValueError('Неизвестный режим теста процессора')
    if not cores:
        raise ValueError('Не удалось определить физические ядра CPU')
    indices = [core.index for core in cores]
    if len(indices) != len(set(indices)) or any(not core.logical_processors for core in cores):
        raise ValueError('Некорректная топология физических ядер')
    if any(group != 0 for core in cores for group in core.groups):
        raise ValueError('Пресет поддерживает одну группу процессоров Windows')
    tests = ('VT3',) if mode == 'fast' else ('VT3', 'SFTv4')
    stages = []
    for core in sorted(cores, key=lambda core: core.index):
        stages.append(dict(label='Ядро %d' % (core.index + 1),
                           core_indices=[core.index], cpus=sorted(set(core.logical_processors)),
                           iterations=1, seconds=60, tests=tests, duration=60 * len(tests)))
    stages.append(dict(label='Все ядра', core_indices=sorted(indices),
                       cpus=sorted({cpu for core in cores for cpu in core.logical_processors}),
                       iterations=15, seconds=60, tests=tests, duration=15 * 60 * len(tests)))
    return stages


class PresetWindow(tk.Toplevel):
    def __init__(self, app, page='choice'):
        # Every page belongs to the app, so closing the choice window cannot
        # destroy the next page through Tk's parent/child ownership.
        super().__init__(app)
        self.withdraw()
        self.app, self.page = app, page
        self.position_job = None
        self.title('Пресеты')
        width, height = (400, 250) if page == 'choice' else (420, 290)
        self.geometry('%dx%d' % (width, height))
        self.minsize(width, height)
        self.window_frame = WindowFrame(self, self.close, lambda: app.dark_mode.get(),
                                        layout='pack', close_only=True, autoinstall=False)
        self.panel = RoundedPanel(self, bg=app.CARD, border=app.BORDER,
                                  surrounding=app.BG, radius=10)
        self.panel.pack(fill='both', expand=True, padx=12, pady=12)
        self.content = self.panel.body
        self.buttons, self.labels = [], []
        self.duration_entry = None
        if page == 'choice':
            self._label('Тест:', bold=True)
            self._button('Процессора', lambda: self.open_detail('cpu'))
            self._button('ОЗУ', lambda: self.open_detail('ram'))
        elif page == 'ram':
            self._label('Время теста:', bold=True)
            self.minutes = tk.StringVar(value='60')
            self.duration_entry = RoundedEntry(self.content, textvariable=self.minutes,
                justify='center', font=('Segoe UI', 11), bg=app.CARD, fg=app.TEXT,
                border=app.BORDER, surrounding=app.CARD)
            self.duration_entry.pack(fill='x', padx=12, pady=(8, 4))
            self._label('В минутах')
            self._button('Старт', self.start_memory)
            self.duration_entry.bind('<Return>', lambda event: self.start_memory())
        else:
            self._label('Тест процессора:', bold=True)
            vendor = cpu_vendor(app.cpu_name)
            if vendor:
                self._label('AMD · V/F curve' if vendor == 'amd' else 'Intel')
                for mode in ('fast', 'full'):
                    self._button(mode, lambda mode=mode: self.start_processor(mode))
            else:
                self._label('Не удалось определить AMD или Intel.')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.theme()
        self.position_job = self.after_idle(self.show)

    def show(self):
        self.position_job = None
        # Build hidden, then map and install the frame in a single callback.
        # In particular, RoundedEntry's idle layout cannot install a half-built
        # RAM dialog, and the first opening receives the same raise as a reopen.
        if self.window_frame.enabled:
            self.overrideredirect(True)
        self.position()
        self.deiconify()
        self.window_frame.install()
        self.position()
        self.lift()
        self.focus_force()

    def _label(self, text, bold=False):
        label = tk.Label(self.content, text=text, bg=self.app.CARD, fg=self.app.TEXT,
                         font=('Segoe UI', 11, 'bold') if bold else ('Segoe UI', 9), wraplength=360)
        label.pack(anchor='w', padx=12, pady=(8, 4))
        self.labels.append(label)

    def _button(self, text, command):
        button = RoundedButton(self.content, text=text, command=command,
            font=('Segoe UI', 10, 'bold'), bg=self.app.CARD, fg=self.app.TEXT,
            border=self.app.BORDER, surrounding=self.app.CARD, padx=12, pady=6)
        button.pack(fill='x', padx=12, pady=6)
        self.buttons.append(button)

    def position(self):
        self.position_job = None
        self.update_idletasks()
        # Presets are fixed-size. While withdrawn, winfo_width/height may
        # still report 1, so use the configured size before the first mapping.
        width, height = self.minsize()
        native = self.app.window_frame.native
        left, top, sw, sh = native.work_area() if native else (0, 0, self.winfo_screenwidth(), self.winfo_screenheight())
        x = left + (sw - width) // 2
        y = top + (sh - height) // 2
        rect = (max(left, min(x, left + sw - width)),
                max(top, min(y, top + sh - height)), width, height)
        # Keep the position in Tk's geometry state as well as on screen.
        # Moving only the Win32 wrapper lets a later Tk layout reset it to 0,0.
        self.geometry('%dx%d+%d+%d' % (width, height, rect[0], rect[1]))

    def open_detail(self, page):
        app = self.app
        self.close()
        app.preset_window = PresetWindow(app, page)

    def start_memory(self):
        try:
            seconds = memory_duration(self.minutes.get())
        except ValueError as exc:
            messagebox.showerror('Время теста', str(exc), parent=self)
            return
        if self.app.start_memory_preset(seconds):
            self.app.preset_window.close()

    def start_processor(self, mode):
        if self.app.start_processor_preset(mode):
            self.app.preset_window.close()

    def theme(self):
        app = self.app
        self.configure(bg=app.BG)
        self.window_frame.theme()
        self.panel.set_colors(app.CARD, app.BORDER, surrounding=app.BG)
        for label in self.labels:
            label.configure(bg=app.CARD, fg=app.TEXT)
        for button in self.buttons:
            button.configure(bg=app.CARD, fg=app.TEXT, border=app.BORDER,
                             activebackground=app.BORDER, activeforeground=app.TEXT,
                             surrounding=app.CARD)
        if self.duration_entry is not None:
            self.duration_entry.configure(bg=app.CARD, fg=app.TEXT, insertbackground=app.TEXT,
                                          border=app.BORDER, surrounding=app.CARD)

    def close(self):
        if self.position_job is not None:
            self.after_cancel(self.position_job)
            self.position_job = None
        if self.app.preset_window is self:
            self.app.preset_window = None
        self.destroy()
