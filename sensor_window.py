import tkinter as tk
from tkinter import ttk
import math
import re
from window_frame import WindowFrame
from widgets import ThinScrollbar, RuledTreeview, RoundedButton, RoundedPanel
from sensors import cpu_rows, select_voltage, voltage_role, usable_voltage, cpu_load_rows, voltage_unit, memory_temperature_rows, whea_rows, is_memory_temperature, is_whea_count


def value_text(row, value):
    if not math.isfinite(value) or abs(value) > 1e9:
        return '—'
    if is_whea_count(row):
        return '%.0f' % value
    if row.kind == 2:
        return '%.3f V' % (value / 1000 if voltage_unit(row) == 'mv' else value)
    if row.unit == '%':
        return '%.1f %%' % value
    return ('%.1f °C' if row.kind == 1 else '%.0f ' + row.unit) % value


def display_name(row):
    if is_whea_count(row):
        return row.label if re.search(r'\bwhea\b', row.label, re.I) else 'WHEA — ' + row.label
    if is_memory_temperature(row):
        # Generic Temperature/SPD Hub/PMIC names repeat on each installed DIMM.
        source = row.hardware
        slot = re.search(r'(?:DIMM|RAM|Memory|Память)\s*(?:\[[^\]]+\]|#\d+|[A-Z]\d+)', source, re.I)
        if not slot:
            slot = re.search(r'(?:DIMM|RAM|Memory|Память)\s*(?:\[[^\]]+\]|#\d+|[A-Z]\d+)', row.hardware_original, re.I)
        if slot:
            source = slot.group(0)
            if source.casefold() in row.label.casefold():
                return row.label
        elif re.search(r'memory.*channel.*rank|памят.*канал.*ранг', row.original + ' ' + row.label, re.I):
            return row.label
        return source + ': ' + row.label
    return row.label


class SensorWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title('Датчики — HWiNFO')
        self.geometry('680x590')
        self.minsize(460, 320)
        self.configure(highlightthickness=2, highlightbackground='#ffffff', highlightcolor='#ffffff')
        self.status = tk.StringVar(value=app.sensor_status)
        self.window_frame = WindowFrame(self, self.close, lambda: app.dark_mode.get(), layout='pack')
        frame = ttk.Frame(self, padding=14, style='Sensors.TFrame')
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, style='Sensors.TLabel', text=app.cpu_name, font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        actions = ttk.Frame(frame, style='Sensors.TFrame')
        actions.pack(fill='x')
        border = '#515861' if app.dark_mode.get() else '#b8bec5'
        self.action_buttons = []
        for label, command in (
                ('Запустить', lambda: self.launch('start')),
                ('Перезапустить', lambda: self.launch('restart')),
                ('Сбросить', app.reset_sensor_history)):
            button = RoundedButton(actions, text=label, command=command,
                                   font=('Segoe UI', 9), padx=12, pady=6,
                                   bg=app.BG, fg=app.TEXT, border=border,
                                   surrounding=app.BG)
            button.pack(side='left', padx=(0, 8), pady=(4, 8))
            self.action_buttons.append(button)
        self.status_label = ttk.Label(frame, style='Sensors.TLabel', textvariable=self.status, wraplength=550)
        self.table_panel = RoundedPanel(frame, bg=app.BG, border=border, radius=6,
                                        surrounding=app.BG)
        self.table_panel.pack(fill='both', expand=True)
        table = self.table_panel.body
        self.tree = RuledTreeview(table, columns=('name', 'current', 'min', 'max'), show='headings', style='Sensors.Treeview')
        for col, label, width in [('name', 'Датчик', 288), ('current', 'Сейчас', 90), ('min', 'Минимум', 100), ('max', 'Максимум', 100)]:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, minwidth=112 if col == 'name' else 72, stretch=(col == 'name'))
        self.scroll = ThinScrollbar(table, command=self.tree.yview, width=6)
        scroll = self.scroll
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.wrap_labels = [w for w in frame.winfo_children() if isinstance(w, ttk.Label)]
        frame.bind('<Configure>', self.resize_content)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh(app.sensor_rows, app.sensor_status)
        self.theme()
        self.after_idle(self.position_right)

    def theme(self):
        bg = self.app.BG
        fg = self.app.TEXT
        hover = '#343b43' if self.app.dark_mode.get() else '#e9edf0'
        border = '#515861' if self.app.dark_mode.get() else '#b8bec5'
        self.configure(bg=bg)
        self.update_outline()
        self.window_frame.theme()
        for button in getattr(self, 'action_buttons', ()):
            button.configure(bg=bg, fg=fg, border=border, surrounding=bg,
                             activebackground=hover, activeforeground=fg)
        table_panel = getattr(self, 'table_panel', None)
        if table_panel is not None:
            table_panel.set_colors(bg, border, surrounding=bg)
        style = ttk.Style(self)
        style.configure('Sensors.TFrame', background=bg)
        style.configure('Sensors.TLabel', background=bg, foreground=fg)
        for name in ('Sensors.Treeview.Heading',):
            style.configure(name, background=bg, foreground=fg, fieldbackground=bg,
                            bordercolor=border, lightcolor=bg, darkcolor=bg,
                            arrowcolor=fg, troughcolor=bg)
            style.map(name, background=[('pressed', hover), ('active', hover), ('!disabled', bg)],
                      foreground=[('disabled', '#808890'), ('!disabled', fg)],
                      fieldbackground=[('readonly', bg)],
                      lightcolor=[('!disabled', bg)], darkcolor=[('!disabled', bg)])
        style.map('Sensors.Treeview', background=[('selected', hover)], foreground=[('selected', fg)])
        style.configure('Sensors.Treeview', background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=25, borderwidth=0, relief='flat')

        self.scroll.set_color('#555555' if self.app.dark_mode.get() else '#b8b8b8', bg)
        style.configure('Sensors.Treeview.Heading', font=('Segoe UI', 9, 'bold'))
        self.tree.set_rule_color(border)

    def update_outline(self, active=None):
        if active is None:
            native = self.window_frame.native
            if native:
                active = native.user.GetForegroundWindow() == native.hwnd
            else:
                focused = self.focus_displayof()
                active = focused is not None and focused.winfo_toplevel() is self
        color = ('#ffffff' if self.app.dark_mode.get() else '#30363d') if active else self.app.BG
        # Keep the inset constant so switching focus doesn't shift the content.
        self.configure(highlightbackground=color, highlightcolor=color)

    def position_right(self):
        if not self.winfo_exists():
            return
        app = self.app
        app.update_idletasks()
        native = app.window_frame.native
        x, y, width, height = native.rect() if native else (app.winfo_rootx(), app.winfo_rooty(), app.winfo_width(), app.winfo_height())
        area = native.work_area() if native else (0, 0, app.winfo_screenwidth(), app.winfo_screenheight())
        left, top, screen_width, screen_height = area
        available = left + screen_width - (x + width + 12)
        # Keep the preferred table width on Full HD-class work areas; on
        # smaller screens conserve space. The user can still resize down to 460.
        sensor_width = 680 if screen_width >= 1800 else max(460, min(600, available))
        sensor_height = max(320, min(height, screen_height))
        # Use the free space on the right; otherwise overlap only enough to stay reachable.
        sx = max(left, min(x + width + 12, left + screen_width - sensor_width))
        sy = max(top, min(y, top + screen_height - sensor_height))
        rect = (sx, sy, sensor_width, sensor_height)
        if self.window_frame.native:
            self.window_frame.native.place(rect)
        else:
            self.geometry('%dx%d%+d%+d' % (sensor_width, sensor_height, sx, sy))

    def resize_content(self, event):
        available = max(180, event.width - 28)
        for label in self.wrap_labels:
            label.configure(wraplength=available)

    def launch(self, kind):
        self.app.sensor_service.command(kind, self.app.hwinfo_path)
        self.app.sensor_rows = []
        self.refresh([], 'Запуск HWiNFO из папки hwinfo…')

    def refresh(self, rows, status):
        notice = 'Датчики подключены · обновление каждую секунду'
        status = status[len(notice):].lstrip(' ·') if status.startswith(notice) else status
        connected = not status
        self.status.set(status)
        if connected:
            self.status_label.pack_forget()
        else:
            self.status_label.pack(anchor='w', fill='x', pady=(10, 8),
                                   before=getattr(self, 'table_panel', self.tree.master))
        clocks, temps = cpu_rows(rows)
        voltage = select_voltage(rows, self.app.sensor_voltage_key)
        voltages = ([voltage] if voltage_role(voltage) == 'board_vcore' else
                    [r for r in rows if voltage_role(r) == 'vid' and usable_voltage(r)]) if voltage else []
        displayed = temps + clocks + cpu_load_rows(rows) + voltages + memory_temperature_rows(rows) + whea_rows(rows)
        displayed = list({r.key: r for r in displayed}.values())
        present = set()
        for row in displayed:
            present.add(row.key)
            low, high = self.app.sensor_history.extrema(row)
            values = (display_name(row), value_text(row, row.value), value_text(row, low), value_text(row, high))
            if self.tree.exists(row.key): self.tree.item(row.key, values=values)
            else: self.tree.insert('', 'end', iid=row.key, values=values)
        for key in self.tree.get_children():
            if key not in present: self.tree.delete(key)
        self.tree.redraw_rules()

    def close(self):
        self.app.sensor_window = None
        self.destroy()
