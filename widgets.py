import tkinter as tk
from tkinter import ttk


class ThinScrollbar(tk.Canvas):
    def __init__(self, master, command=None, width=6, **kwargs):
        super().__init__(master, width=width, highlightthickness=0, bd=0,
                         bg=kwargs.pop("bg", "#22252a"))
        self.command = command
        self._first = 0.0
        self._last = 1.0
        self._color = "#555555"
        self._dragging = False
        self._drag_start_y = 0
        self._drag_start_first = 0.0
        self._thumb_y = 0
        self._thumb_h = 24
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<MouseWheel>", self._wheel)

    def set_color(self, color, trough):
        self.configure(bg=trough)
        self._color = color
        self._draw()

    def set(self, first, last):
        self._first = max(0.0, min(1.0, float(first)))
        self._last = max(self._first, min(1.0, float(last)))
        self._draw()

    def _draw(self):
        self.delete("all")
        h = max(self.winfo_height(), 1)
        w = max(self.winfo_width(), 1)
        span = max(0.01, self._last - self._first)
        self._thumb_h = min(h, max(24, int(h * span)))
        track = max(1, h - self._thumb_h)
        self._thumb_y = int(track * self._first / max(0.0001, 1.0 - span)) if span < 1 else 0
        self.create_rectangle(0, self._thumb_y, w, self._thumb_y + self._thumb_h,
                              fill=self._color, width=0, tags="thumb")

    def _press(self, event):
        if self._thumb_y <= event.y <= self._thumb_y + self._thumb_h:
            self._dragging = True
            self._drag_start_y = event.y
            self._drag_start_first = self._first
        elif self.command:
            h = max(self.winfo_height(), 1)
            self.command("moveto", max(0.0, min(1.0, event.y / h)))

    def _drag(self, event):
        if not self._dragging or not self.command:
            return
        h = max(self.winfo_height(), 1)
        track = max(1, h - self._thumb_h)
        span = self._last - self._first
        delta = (event.y - self._drag_start_y) / track * (1.0 - span)
        self.command("moveto", max(0.0, min(1.0, self._drag_start_first + delta)))

    def _release(self, event):
        self._dragging = False

    def _wheel(self, event):
        if self.command:
            self.command("scroll", int(-event.delta / 120), "units")



class RuledTreeview(ttk.Treeview):
    """Treeview with body column rules that follow native column resizing."""
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._rule_color = '#515861'
        self._rule_job = None
        self._rules = [tk.Frame(self, width=1, bd=0, bg=self._rule_color)
                       for _ in self['columns'][:-1]]
        for rule in self._rules:
            rule.bind('<MouseWheel>', lambda e: self.event_generate('<MouseWheel>', delta=e.delta))
        for event in ('<Configure>', '<B1-Motion>', '<ButtonRelease-1>', '<Map>'):
            self.bind(event, self.redraw_rules, add='+')
        self.bind('<Destroy>', self._dispose_rules, add='+')

    def set_rule_color(self, color):
        self._rule_color = color
        for rule in self._rules:
            rule.configure(bg=color)
        self.redraw_rules()

    def redraw_rules(self, event=None):
        if self._rule_job is None:
            self._rule_job = self.after_idle(self._draw_rules)

    def _draw_rules(self):
        self._rule_job = None
        children = self.get_children()
        first = self.bbox(children[0], '#1') if children else ()
        if first:
            x, heading_height = first[0], first[1]
        else:
            from tkinter import font
            heading_font = ttk.Style(self).lookup(self['style'] + '.Heading', 'font') or 'TkDefaultFont'
            heading_height = font.Font(self, font=heading_font).metrics('linespace') + 10
            x = 1
        for rule, column in zip(self._rules, self['columns'][:-1]):
            x += self.column(column, 'width')
            if x < self.winfo_width() - 1:
                rule.place(x=x, y=heading_height, width=1, relheight=1, height=-heading_height)
                rule.lift()
            else:
                rule.place_forget()

    def _dispose_rules(self, event):
        if event.widget is self and self._rule_job is not None:
            self.after_cancel(self._rule_job)
            self._rule_job = None


def _rounded_points(left, top, right, bottom, radius):
    """A rounded outline with straight sides, including at very small sizes."""
    radius = max(0, min(radius, (right - left) / 2, (bottom - top) / 2))
    return [left + radius, top, right - radius, top, right - radius, top,
            right, top, right, top + radius, right, top + radius,
            right, bottom - radius, right, bottom - radius, right, bottom,
            right - radius, bottom, right - radius, bottom,
            left + radius, bottom, left + radius, bottom, left, bottom,
            left, bottom - radius, left, bottom - radius,
            left, top + radius, left, top + radius, left, top,
            left + radius, top]


def _inside_rounded(x, y, width, height, radius):
    """Keep button activation inside both its bounds and rounded corners."""
    if not (0 <= x < width and 0 <= y < height):
        return False
    radius = max(0, min(radius, width / 2, height / 2))
    nearest_x = min(max(x, radius), width - radius)
    nearest_y = min(max(y, radius), height - radius)
    return (x - nearest_x) ** 2 + (y - nearest_y) ** 2 <= radius ** 2


def _parent_background(master):
    for option in ('background', 'bg'):
        try:
            return master.cget(option)
        except tk.TclError:
            pass
    return '#22252a'


class RoundedButton(tk.Canvas):
    """Small rounded button with native keyboard focus and button semantics."""
    _aliases = {'background': 'bg', 'foreground': 'fg',
                'activebackground': 'activebackground',
                'activeforeground': 'activeforeground',
                'borderwidth': 'bd'}
    _visual_options = {'text', 'image', 'command', 'font', 'bg', 'fg',
                       'activebackground', 'activeforeground', 'disabledbackground', 'disabledforeground',
                       'relief', 'bd', 'highlightthickness', 'highlightbackground',
                       'highlightcolor', 'padx', 'pady', 'state', 'width', 'height',
                       'radius', 'border', 'surrounding', 'compound', 'anchor',
                       'offrelief', 'overrelief', 'default'}

    def __init__(self, master, **kwargs):
        surrounding = kwargs.pop('surrounding', _parent_background(master))
        self._button_options = dict(text='', image=None, command=None,
            font='TkDefaultFont', bg=surrounding, fg='#e5e8ed',
            activebackground=None, activeforeground=None,
            disabledbackground=None, disabledforeground='#8c929a', relief='flat', bd=0,
            highlightthickness=0, highlightbackground=None, highlightcolor=None,
            padx=8, pady=3, state='normal', width=0, height=0,
            radius=6, border='#515861', surrounding=surrounding,
            compound='center', anchor='center', offrelief='flat', overrelief='flat',
            default='normal')
        canvas_options = {}
        for name, value in kwargs.items():
            name = self._aliases.get(name, name)
            if name in self._visual_options:
                self._button_options[name] = value
            else:
                canvas_options[name] = value
        self._hovered = False
        self._pressed = False
        self._keyboard_pressed = False
        self._focused = False
        self._font = None
        self._takefocus = canvas_options.get('takefocus', True)
        canvas_options.setdefault('takefocus', self._takefocus if self._button_options['state'] != 'disabled' else False)
        canvas_options.setdefault('cursor', 'hand2')
        super().__init__(master, bg=surrounding, bd=0, highlightthickness=0,
                         **canvas_options)
        self.bind('<Configure>', self._draw)
        self.bind('<Enter>', self._enter)
        self.bind('<Leave>', self._leave)
        self.bind('<ButtonPress-1>', self._press)
        self.bind('<ButtonRelease-1>', self._release)
        self.bind('<FocusIn>', self._focus_in)
        self.bind('<FocusOut>', self._focus_out)
        self.bind('<KeyPress-space>', self._key_press)
        self.bind('<KeyRelease-space>', self._key_release)
        self.bind('<Return>', self._return)
        self._resize_request()

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, str) and not kwargs:
            key = self._aliases.get(cnf, cnf)
            if key in self._button_options:
                value = self._button_options[key]
                return (key, key, key.title(), value, value)
            return super().configure(cnf)
        options = dict(cnf or {})
        options.update(kwargs)
        if not options:
            result = super().configure()
            result.update({key: (key, key, key.title(), value, value)
                           for key, value in self._button_options.items()})
            return result
        native = {}
        for name, value in options.items():
            key = self._aliases.get(name, name)
            if key in self._visual_options:
                self._button_options[key] = value
            else:
                native[name] = value
        if 'takefocus' in native:
            self._takefocus = native['takefocus']
        if native:
            super().configure(**native)
        super().configure(takefocus=self._takefocus if self._button_options['state'] != 'disabled' else False)
        if self._button_options['state'] == 'disabled':
            self._pressed = self._keyboard_pressed = False
        super().configure(bg=self._button_options['surrounding'])
        self._resize_request()

    config = configure

    def cget(self, key):
        name = self._aliases.get(key, key)
        if name in self._button_options:
            return self._button_options[name]
        return super().cget(key)

    __getitem__ = cget

    def set_colors(self, background, foreground, border, surrounding=None):
        options = dict(bg=background, fg=foreground, border=border)
        if surrounding is not None:
            options['surrounding'] = surrounding
        self.configure(**options)

    def _resize_request(self):
        from tkinter import font
        options = self._button_options
        self._font = font.Font(self, font=options['font'])
        image = options['image']
        if image:
            if hasattr(image, 'width'):
                width, height = image.width(), image.height()
            else:
                width = int(self.tk.call('image', 'width', image))
                height = int(self.tk.call('image', 'height', image))
            width = int(options['width']) or width
            height = int(options['height']) or height
        else:
            lines = str(options['text']).split('\n')
            width = (int(options['width']) * self._font.measure('0') if options['width']
                     else max(self._font.measure(line) for line in lines))
            height = (int(options['height']) or len(lines)) * self._font.metrics('linespace')
        width += 2 * int(options['padx']) + 4
        height += 2 * int(options['pady']) + 4
        super().configure(width=max(12, width), height=max(12, height))
        self._draw()

    def _draw(self, event=None):
        options = self._button_options
        width, height = self.winfo_width(), self.winfo_height()
        self.delete('button')
        disabled = options['state'] == 'disabled'
        active = not disabled and (self._hovered or self._pressed or self._keyboard_pressed)
        fill = (options['disabledbackground'] or options['bg']) if disabled else (
            (options['activebackground'] or options['bg']) if active else options['bg'])
        foreground = options['disabledforeground'] if disabled else (
            (options['activeforeground'] or options['fg']) if active else options['fg'])
        self.create_polygon(_rounded_points(1, 1, max(1, width-1), max(1, height-1),
                                            options['radius']),
                            smooth=True, splinesteps=24, fill=fill,
                            outline=options['border'] or fill, width=1, tags='button')
        if self._focused and not disabled:
            self.create_polygon(_rounded_points(3, 3, max(3, width-3), max(3, height-3),
                                                max(1, options['radius']-2)),
                                smooth=True, splinesteps=24, fill='', outline=foreground,
                                width=1, tags='button')
        if options['image']:
            self.create_image(width/2, height/2, image=options['image'], tags='button')
        else:
            self.create_text(width/2, height/2, text=options['text'],
                             font=options['font'], fill=foreground, tags='button')

    def _inside(self, event):
        return _inside_rounded(event.x, event.y, self.winfo_width(), self.winfo_height(),
                               self._button_options['radius'])

    def _enter(self, event=None):
        self._hovered = True
        self._draw()

    def _leave(self, event=None):
        self._hovered = False
        self._draw()

    def _press(self, event):
        if self._button_options['state'] != 'disabled' and self._inside(event):
            self.focus_set()
            self._pressed = True
            self._draw()
        return 'break'

    def _release(self, event):
        activate = self._pressed and self._inside(event)
        self._pressed = False
        self._draw()
        if activate:
            self.invoke()
        return 'break'

    def _focus_in(self, event=None):
        self._focused = True
        self._draw()

    def _focus_out(self, event=None):
        self._focused = self._keyboard_pressed = self._pressed = False
        self._draw()

    def _key_press(self, event=None):
        if self._button_options['state'] != 'disabled':
            self._keyboard_pressed = True
            self._draw()
        return 'break'

    def _key_release(self, event=None):
        activate = self._keyboard_pressed
        self._keyboard_pressed = False
        self._draw()
        if activate:
            self.invoke()
        return 'break'

    def _return(self, event=None):
        self.invoke()
        return 'break'

    def invoke(self):
        if self._button_options['state'] == 'disabled':
            return ''
        command = self._button_options['command']
        if callable(command):
            return command()
        if command:
            return self.tk.call(command)
        return ''


class RoundedEntry(tk.Canvas):
    """A rounded field around a real Entry, preserving text editing and variables."""
    _shell_options = {'border', 'surrounding', 'radius', 'height'}

    def __init__(self, master, **kwargs):
        self._field_options = dict(border=kwargs.pop('border', '#515861'),
            surrounding=kwargs.pop('surrounding', _parent_background(master)),
            radius=kwargs.pop('radius', 6), height=kwargs.pop('height', 31))
        kwargs.pop('style', None)
        kwargs.setdefault('font', ('Segoe UI', 11))
        kwargs.setdefault('bg', '#22252a')
        kwargs.setdefault('fg', '#e5e8ed')
        kwargs.setdefault('insertbackground', kwargs['fg'])
        super().__init__(master, bg=self._field_options['surrounding'],
                         height=self._field_options['height'], bd=0, highlightthickness=0,
                         takefocus=False)
        self.entry = tk.Entry(self, relief='flat', bd=0, highlightthickness=0, **kwargs)
        self.entry.configure(disabledbackground=self.entry.cget('bg'),
                             readonlybackground=self.entry.cget('bg'))
        self._entry_window = self.create_window(7, 0, window=self.entry, anchor='nw')
        super().bind('<Configure>', self._draw_field)
        self.entry.bind('<FocusIn>', self._draw_field, add='+')
        self.entry.bind('<FocusOut>', self._draw_field, add='+')
        self._resize_request()

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, str) and not kwargs:
            if cnf in self._field_options:
                value = self._field_options[cnf]
                return (cnf, cnf, cnf.title(), value, value)
            return self.entry.configure(cnf)
        options = dict(cnf or {})
        options.update(kwargs)
        if not options:
            result = self.entry.configure()
            result.update({key: (key, key, key.title(), value, value)
                           for key, value in self._field_options.items()})
            return result
        for name in tuple(options):
            if name in self._shell_options:
                self._field_options[name] = options.pop(name)
        options.pop('style', None)
        if options:
            self.entry.configure(**options)
        self.entry.configure(disabledbackground=self.entry.cget('bg'),
                             readonlybackground=self.entry.cget('bg'))
        super().configure(bg=self._field_options['surrounding'])
        self._resize_request()

    config = configure

    def cget(self, key):
        if key in self._field_options:
            return self._field_options[key]
        return self.entry.cget(key)

    __getitem__ = cget

    def _resize_request(self):
        self.update_idletasks()
        super().configure(width=self.entry.winfo_reqwidth()+14,
                         height=max(self.entry.winfo_reqheight()+8, self._field_options['height']))
        self._draw_field()

    def _draw_field(self, event=None):
        width, height = self.winfo_width(), self.winfo_height()
        super().delete('field')
        self.create_polygon(_rounded_points(1, 1, max(1, width-1), max(1, height-1),
                                            self._field_options['radius']),
                            smooth=True, splinesteps=24, fill=self.entry.cget('bg'),
                            outline=self._field_options['border'], width=1, tags='field')
        self.tag_lower('field')
        entry_height = self.entry.winfo_reqheight()
        self.coords(self._entry_window, 7, max(1, (height-entry_height)/2))
        self.itemconfigure(self._entry_window, width=max(1, width-14), height=entry_height)

    def bind(self, sequence=None, func=None, add=None):
        return self.entry.bind(sequence, func, add)

    def unbind(self, sequence, funcid=None):
        return self.entry.unbind(sequence, funcid)

    def focus_set(self):
        return self.entry.focus_set()

    focus = focus_set

    def focus_force(self):
        return self.entry.focus_force()

    def get(self):
        return self.entry.get()

    def delete(self, first, last=None):
        return self.entry.delete(first, last)

    def insert(self, index, text):
        return self.entry.insert(index, text)

    def index(self, index):
        return self.entry.index(index)

    def icursor(self, index):
        return self.entry.icursor(index)

    def selection_range(self, start, end):
        return self.entry.selection_range(start, end)

    select_range = selection_range

    def selection_clear(self):
        return self.entry.selection_clear()

    select_clear = selection_clear

    def selection_present(self):
        return self.entry.selection_present()

    select_present = selection_present

    def selection_from(self, index):
        return self.entry.selection_from(index)

    select_from = selection_from

    def selection_to(self, index):
        return self.entry.selection_to(index)

    select_to = selection_to

    def selection_adjust(self, index):
        return self.entry.selection_adjust(index)

    select_adjust = selection_adjust

    def xview(self, *args):
        return self.entry.xview(*args)


class RoundedPanel(tk.Canvas):
    def __init__(self, master, bg, border, radius=6, surrounding=None, **kwargs):
        self.radius = radius
        self.border_color = border
        self.panel_background = bg
        if surrounding is None:
            surrounding = _parent_background(master)
        super().__init__(master, bg=surrounding, bd=0, highlightthickness=0, **kwargs)
        self.body = tk.Frame(self, bg=bg, bd=0)
        self._body_window = self.create_window(radius, radius, window=self.body, anchor='nw')
        self.bind('<Configure>', self._draw_panel)

    def set_colors(self, background, border, surrounding=None):
        if surrounding is not None:
            self.configure(bg=surrounding)
        self.panel_background = background
        self.body.configure(bg=background)
        self.border_color = border
        self._draw_panel()

    def _draw_panel(self, event=None):
        width, height = self.winfo_width(), self.winfo_height()
        self.delete('panel')
        self.create_polygon(_rounded_points(1, 1, max(1, width-1), max(1, height-1), self.radius),
                            smooth=True, splinesteps=24, fill=self.panel_background,
                            outline=self.border_color, width=1, tags='panel')
        self.tag_lower('panel')
        inset = max(2, self.radius)
        self.coords(self._body_window, inset, inset)
        self.itemconfigure(self._body_window, width=max(1, width-2*inset),
                           height=max(1, height-2*inset))
