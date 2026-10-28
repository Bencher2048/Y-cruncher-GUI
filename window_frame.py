"""Custom Tk title bar; native Win32 window movement, sizing and state changes."""
import ctypes
from ctypes import wintypes
import sys
import tkinter as tk

HTCLIENT, HTCAPTION = 1, 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17


def signed_point(packed):
    return ctypes.c_int16(packed & 0xffff).value, ctypes.c_int16((packed >> 16) & 0xffff).value


def hit_test(x, y, width, height, border, title_height, buttons_width, maximized=False):
    if not (0 <= x < width and 0 <= y < height):
        return 0
    if not maximized:
        left, right = x < border, x >= width - border
        top, bottom = y < border, y >= height - border
        if top: return HTTOPLEFT if left else HTTOPRIGHT if right else HTTOP
        if bottom: return HTBOTTOMLEFT if left else HTBOTTOMRIGHT if right else HTBOTTOM
        if left: return HTLEFT
        if right: return HTRIGHT
    return HTCAPTION if y < title_height and x < width - buttons_width else HTCLIENT


def palette(dark, active=True):
    return {'bg': '#20252b' if dark else '#ffffff',
            'fg': ('#c5cbd1' if dark else '#60656a') if active else ('#727980' if dark else '#a6a9ad'),
            'hover': '#343b43' if dark else '#e9edf0', 'close': '#e34c56'}


class RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_int32), ('top', ctypes.c_int32), ('right', ctypes.c_int32), ('bottom', ctypes.c_int32)]


class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_int32), ('y', ctypes.c_int32)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [('size', ctypes.c_uint32), ('monitor', RECT), ('work', RECT), ('flags', ctypes.c_uint32)]


def resized_rect(rect, edge, dx, dy, minimum):
    x, y, width, height = rect
    if edge in (HTLEFT, HTTOPLEFT, HTBOTTOMLEFT):
        amount = min(dx, width-minimum[0]); x += amount; width -= amount
    if edge in (HTRIGHT, HTTOPRIGHT, HTBOTTOMRIGHT): width = max(minimum[0], width+dx)
    if edge in (HTTOP, HTTOPLEFT, HTTOPRIGHT):
        amount = min(dy, height-minimum[1]); y += amount; height -= amount
    if edge in (HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT): height = max(minimum[1], height+dy)
    return x, y, width, height


def snap_target(x, y, work_area, margin=10):
    left, top, width, height = work_area
    if y <= top + margin and left - margin <= x <= left + width + margin:
        return 'maximize', work_area
    if top <= y < top + height:
        half = width // 2
        if x <= left + margin:
            return 'left', (left, top, half, height)
        if x >= left + width - margin - 1:
            return 'right', (left + half, top, width - half, height)
    return None


class NativeFrame:
    """No WNDPROC callback and no nested native mouse/message loop."""
    def __init__(self, root, bar):
        self.root, self.bar = root, bar
        self.hwnd = None
        self.maximized = False
        self.snapped = False
        self.restore_rect = None
        self.gesture = None
        self.map_job = None
        self.minimum = root.minsize()
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        u = self.user
        self.get_long = u.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p)==8 else u.GetWindowLongW
        self.set_long = u.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p)==8 else u.SetWindowLongW
        for fn in (self.get_long, self.set_long): fn.restype = ctypes.c_ssize_t
        self.get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        self.set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        u.GetParent.argtypes=[wintypes.HWND]; u.GetParent.restype=wintypes.HWND
        u.IsWindow.argtypes=[wintypes.HWND]; u.IsWindow.restype=wintypes.BOOL
        u.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(RECT)];u.GetWindowRect.restype=wintypes.BOOL
        u.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT];u.SetWindowPos.restype=wintypes.BOOL
        u.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int];u.ShowWindow.restype=wintypes.BOOL
        u.GetForegroundWindow.restype=wintypes.HWND
        u.MonitorFromPoint.argtypes=[POINT,wintypes.DWORD];u.MonitorFromPoint.restype=wintypes.HANDLE
        u.MonitorFromWindow.argtypes=[wintypes.HWND,wintypes.DWORD];u.MonitorFromWindow.restype=wintypes.HANDLE
        u.GetMonitorInfoW.argtypes=[wintypes.HANDLE,ctypes.POINTER(MONITORINFO)];u.GetMonitorInfoW.restype=wintypes.BOOL

    def install(self):
        self.root.overrideredirect(True)
        self.root.update_idletasks()
        self.apply_styles()
        self.root.bind('<ButtonPress-1>', self._resize_click, add='+')
        self.root.bind('<B1-Motion>', self._drag_motion, add='+')
        self.root.bind('<ButtonRelease-1>', self._release_drag, add='+')
        self.root.bind('<Motion>', self._motion, add='+')
        self.root.bind('<Map>', self._mapped, add='+')
        self.root.bind('<Alt-F4>', lambda e: self._close(), add='+')
        self.root.bind('<Alt-space>', self.system_menu, add='+')

    def apply_styles(self, frameless=True):
        # wm frame explicitly returns the outer Tk wrapper, not the client HWND.
        frame = self.root.tk.call('wm', 'frame', self.root._w)
        hwnd = int(str(frame), 0)
        for _ in range(8):
            if not self.get_long(hwnd, -16) & 0x40000000: break  # WS_CHILD
            hwnd = self.user.GetParent(hwnd)
            if not hwnd: raise RuntimeError('Не найден внешний HWND окна Tk')
        else: raise RuntimeError('Некорректная иерархия HWND')
        if not self.user.IsWindow(hwnd): raise RuntimeError('Окно Tk уже закрыто')
        self.hwnd = hwnd
        # Frameless popup; keep native minimize and system menu. Do not touch WNDPROC.
        style = self.get_long(hwnd, -16)
        extended = self.get_long(hwnd, -20)
        if frameless:
            style = (style & ~0x00c40000) | 0x800a0000
            extended = (extended & ~0x00000381) | 0x00040000
        if getattr(self.bar, 'close_only', False):
            style &= ~0x00070000  # WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_THICKFRAME
        self.set_long(hwnd,-16,style)
        self.set_long(hwnd,-20,extended)
        if not self.user.SetWindowPos(hwnd,None,0,0,0,0,0x37):
            raise ctypes.WinError(ctypes.get_last_error())

    def _mapped(self, event):
        if event.widget is self.root:
            if self.map_job is not None:
                self.root.after_cancel(self.map_job)
            self.map_job = self.root.after_idle(self._reapply)

    def _reapply(self):
        self.map_job = None
        try:
            if self.root.winfo_exists(): self.apply_styles()
        except (OSError, RuntimeError, tk.TclError): pass

    def rect(self):
        rect=RECT()
        if not self.user.GetWindowRect(self.hwnd,ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        return rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top

    def place(self, rect):
        x,y,w,h=rect
        self.root.geometry('%dx%d' % (w,h))
        self.root.update_idletasks()
        if not self.user.SetWindowPos(self.hwnd,None,x,y,w,h,0x14):
            raise ctypes.WinError(ctypes.get_last_error())

    def hit(self, screen_x, screen_y):
        x,y,w,h=self.rect()
        border = 0 if self.bar.close_only else self.bar.border
        return hit_test(screen_x-x,screen_y-y,w,h,border,self.bar.height,
                        self.bar.button_width*len(self.bar.buttons),self.maximized)

    def move_or_resize(self, event):
        code=self.hit(event.x_root,event.y_root)
        if code != HTCAPTION and code < HTLEFT: return
        self.gesture=(code,event.x_root,event.y_root,self.rect())
        return 'break'

    def _resize_click(self,event):
        if self.gesture is None and self.hit(event.x_root,event.y_root)>=HTLEFT:
            return self.move_or_resize(event)

    def _drag_motion(self,event):
        if not self.gesture: return
        edge,start_x,start_y,rect=self.gesture
        dx,dy=event.x_root-start_x,event.y_root-start_y
        if edge==HTCAPTION:
            if self.maximized or getattr(self, 'snapped', False):
                if abs(dx)+abs(dy)<4: return 'break'
                restore=self.restore_rect or rect
                fraction=max(0,min(1,(start_x-rect[0])/max(1,rect[2])))
                new=(round(event.x_root-fraction*restore[2]),event.y_root-min(self.bar.height//2,start_y-rect[1]),restore[2],restore[3])
                self.maximized=False;self.snapped=False;self.root.minsize(*self.minimum);self.place(new)
                self.gesture=(edge,event.x_root,event.y_root,new)
            else:
                # Move only the outer wrapper; never shift Tk's client HWND.
                self.user.SetWindowPos(self.hwnd,None,rect[0]+dx,rect[1]+dy,0,0,0x15)
        elif not self.maximized:
            self.place(resized_rect(rect,edge,dx,dy,self.root.minsize()))
        return 'break'

    def _release_drag(self,event):
        if self.gesture:
            edge, start_x, start_y, original = self.gesture
            self.gesture = None
            if (not getattr(self.bar, 'close_only', False) and
                    event is not None and edge == HTCAPTION and
                    abs(event.x_root-start_x) + abs(event.y_root-start_y) >= 4):
                target = snap_target(event.x_root, event.y_root,
                                     self.work_area_at(event.x_root, event.y_root))
                if target:
                    mode, rect = target
                    if not self.maximized and not getattr(self, 'snapped', False):
                        self.restore_rect = self.rect()
                    self.root.minsize(1, 1)
                    self.maximized = mode == 'maximize'
                    self.snapped = mode != 'maximize'
                    self.place(rect)
            return 'break'

    def _motion(self,event):
        code=self.hit(event.x_root,event.y_root)
        cursor={HTLEFT:'size_we',HTRIGHT:'size_we',HTTOP:'size_ns',HTBOTTOM:'size_ns',HTTOPLEFT:'size_nw_se',HTBOTTOMRIGHT:'size_nw_se',HTTOPRIGHT:'size_ne_sw',HTBOTTOMLEFT:'size_ne_sw'}.get(code,'')
        # Tk configures the native cursor on every call, so only touch it when
        # the edge under the pointer actually changed.
        if getattr(self, '_cursor', None) != cursor:
            self._cursor = cursor
            self.root.configure(cursor=cursor)

    def minimize(self):
        if getattr(self.bar, 'close_only', False): return
        self.gesture=None
        self.user.ShowWindow(self.hwnd,6)

    def work_area(self):
        info=MONITORINFO();info.size=ctypes.sizeof(info)
        handle=self.user.MonitorFromWindow(self.hwnd,2)
        if not handle or not self.user.GetMonitorInfoW(handle,ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        r=info.work
        return r.left,r.top,r.right-r.left,r.bottom-r.top

    def work_area_at(self, x, y):
        info = MONITORINFO()
        info.size = ctypes.sizeof(info)
        handle = self.user.MonitorFromPoint(POINT(x, y), 2)
        if not handle or not self.user.GetMonitorInfoW(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        r = info.work
        return r.left, r.top, r.right-r.left, r.bottom-r.top

    def toggle_maximize(self):
        if getattr(self.bar, 'close_only', False): return
        self.gesture=None
        if self.maximized:
            self.root.minsize(*self.minimum)
            self.place(self.restore_rect)
            self.maximized=False
        else:
            if not getattr(self, 'snapped', False):
                self.restore_rect=self.rect()
            self.root.minsize(1,1)
            self.place(self.work_area())
            self.maximized=True
        self.snapped=False

    def _close(self):
        self.bar.close()
        return 'break'

    def system_menu(self,event):
        menu=tk.Menu(self.root,tearoff=0)
        if not getattr(self.bar, 'close_only', False):
            menu.add_command(label='Восстановить' if self.maximized else 'Развернуть',command=self.toggle_maximize)
            menu.add_command(label='Свернуть',command=self.minimize)
            menu.add_separator()
        menu.add_command(label='Закрыть',command=self.bar.close)
        try: menu.tk_popup(event.x_root,event.y_root)
        finally: menu.grab_release()
        return 'break'

    def dispose(self):
        self.gesture = None
        if self.map_job is not None:
            try: self.root.after_cancel(self.map_job)
            except tk.TclError: pass
            self.map_job = None

    def detach(self):
        self.dispose()
        self.gesture=None
        self.root.overrideredirect(False)
        if getattr(self.bar, 'close_only', False):
            try:
                self.root.update_idletasks()
                self.apply_styles(frameless=False)
            except (OSError, RuntimeError, tk.TclError):
                pass


class WindowFrame:
    """Small blank title bar; each button is drawn without font-dependent glyphs."""
    def __init__(self, root, close, dark, layout='grid', close_only=False, autoinstall=True):
        self.root, self.close, self.dark, self.layout = root, close, dark, layout
        self.close_only = close_only
        if close_only:
            root.resizable(False, False)
            # Win32 controls are managed explicitly below. Tk transient
            # ownership must not compete with the frameless popup wrapper.
            if sys.platform != 'win32':
                root.transient(root.master)
        self.native = None
        self.job = None
        self.install_job = None
        self._drag_offset = None
        self.enabled = sys.platform == 'win32'
        if not self.enabled: return
        scale = max(1.0, float(root.tk.call('tk', 'scaling')) / (96/72))
        self.height = round(32*scale)
        self.button_width = round(46*scale)
        self.border = max(5, round(5*scale))
        self.scale = scale
        self.bar = tk.Frame(root, height=self.height, bd=0)
        if layout == 'grid': self.bar.grid(row=0, column=0, sticky='ew')
        else: self.bar.pack(side='top', fill='x')
        self.bar.pack_propagate(False)
        self.buttons = {}
        self.hovered = None
        self.pressed = None
        for name in (('close',) if close_only else ('close', 'maximize', 'minimize')):
            canvas = tk.Canvas(self.bar, width=self.button_width, height=self.height, highlightthickness=0, bd=0, takefocus=1)
            canvas.pack(side='right')
            canvas.bind('<Enter>', lambda e, n=name: self._hover(n))
            canvas.bind('<Leave>', lambda e: self._hover(None))
            canvas.bind('<ButtonPress-1>', lambda e, n=name: self._press(n, e))
            canvas.bind('<ButtonRelease-1>', lambda e, n=name: self._release(n, e))
            canvas.bind('<Return>', lambda e, n=name: self._click(n))
            canvas.bind('<space>', lambda e, n=name: self._click(n))
            self.buttons[name] = canvas
        self.bar.bind('<ButtonPress-1>', self._drag)
        if close_only:
            self.bar.bind('<B1-Motion>', self._drag_move)
            self.bar.bind('<ButtonRelease-1>', self._drag_end)
        if not close_only:
            self.bar.bind('<Double-Button-1>', lambda e: self.native.toggle_maximize() if self.native else None)
        self.bar.bind('<ButtonPress-3>', lambda e: self.native.system_menu(e) if self.native else None)
        root.bind('<Destroy>', self._destroy, add='+')
        self.theme()
        if autoinstall:
            self.install_job = root.after_idle(self._install)

    def install(self):
        """Install once, after a dialog has finished building and been mapped."""
        if not self.enabled or self.native is not None:
            return
        if self.install_job is not None:
            self.root.after_cancel(self.install_job)
            self.install_job = None
        self._install()

    def _install(self):
        self.install_job = None
        try:
            native = NativeFrame(self.root, self)
            self.native = native
            native.install()
            self._refresh()
        except Exception as exc:
            # Restore a single standard frame if frameless initialization fails.
            if 'native' in locals(): native.detach()
            self.native = None
            self.enabled = False
            if self.layout == 'grid': self.bar.grid_remove()
            else: self.bar.pack_forget()
            self.root.frame_error = str(exc)

    def _drag(self, event):
        if self.close_only:
            self._drag_offset = (event.x_root-self.root.winfo_rootx(),
                                 event.y_root-self.root.winfo_rooty())
            self.bar.grab_set()
            return 'break'
        if self.native: return self.native.move_or_resize(event)

    def _drag_move(self, event):
        if self._drag_offset is None:
            return
        dx, dy = self._drag_offset
        # Keep Tk's wrapper position in sync. A leading '+' also makes negative
        # coordinates absolute, rather than offsets from the screen's far edge.
        self.root.geometry('+%d+%d' % (event.x_root-dx, event.y_root-dy))
        return 'break'

    def _drag_end(self, event):
        if self._drag_offset is None:
            return
        self._drag_offset = None
        if self.bar.grab_current() is self.bar:
            self.bar.grab_release()
        return 'break'

    def _press(self, name, event):
        if self.native and self.native.hit(event.x_root, event.y_root) >= HTLEFT:
            return self.native.move_or_resize(event)
        self.pressed = name
        if getattr(self, 'enabled', False):
            self.theme()

    def _release(self, name, event):
        pressed, self.pressed = self.pressed, None
        if getattr(self, 'enabled', False):
            self.theme()
        if pressed == name and 0 <= event.x < self.button_width and 0 <= event.y < self.height:
            self._click(name)

    def _click(self, name):
        if name == 'close': self.close()
        elif self.native:
            if name == 'minimize': self.native.minimize()
            else: self.native.toggle_maximize()

    def _hover(self, name):
        self.hovered = name
        self.theme()

    def theme(self):
        if not self.enabled: return
        active = not self.native or self.native.user.GetForegroundWindow() == self.native.hwnd
        update_outline = getattr(self.root, 'update_outline', None)
        if update_outline is not None:
            update_outline(active)
        colors = palette(self.dark(), active)
        self.bar.configure(bg=colors['bg'])
        maximized = bool(self.native and self.native.maximized)
        for name, canvas in self.buttons.items():
            hover = name == self.hovered
            bg = colors['close'] if hover and name == 'close' else colors['hover'] if hover else colors['bg']
            if hover and name == self.pressed:
                bg = '#c93e48' if name == 'close' else '#2b323a' if self.dark() else '#dce1e5'
            fg = '#ffffff' if hover and name == 'close' else colors['fg']
            # Keep the whole native button hit area, rounding only its painted surface.
            canvas.configure(bg=colors['bg'])
            canvas.delete('all')
            if hover:
                inset, radius = 2*self.scale, 6*self.scale
                left, top = inset, inset
                right, bottom = self.button_width-inset, self.height-inset
                canvas.create_polygon(
                    left+radius, top, right-radius, top, right, top,
                    right, top+radius, right, bottom-radius, right, bottom,
                    right-radius, bottom, left+radius, bottom, left, bottom,
                    left, bottom-radius, left, top+radius, left, top,
                    fill=bg, outline='', smooth=True, splinesteps=24)
            x, y, half = self.button_width/2, self.height/2, 5*self.scale
            width = max(1, round(self.scale))
            if name == 'minimize':
                canvas.create_line(x-half, y+half/2, x+half, y+half/2, fill=fg, width=width)
            elif name == 'close':
                canvas.create_line(x-half, y-half, x+half, y+half, fill=fg, width=width)
                canvas.create_line(x+half, y-half, x-half, y+half, fill=fg, width=width)
            elif maximized:
                d = 2*self.scale
                canvas.create_rectangle(x-half+d, y-half, x+half, y+half-d, outline=fg, width=width)
                canvas.create_rectangle(x-half, y-half+d, x+half-d, y+half, outline=fg, fill=bg, width=width)
            else:
                canvas.create_rectangle(x-half, y-half, x+half, y+half, outline=fg, width=width)
        self._state = self._state_key()

    def _state_key(self):
        """Everything the title bar paints differently, cheaply detectable."""
        try:
            active = not self.native or self.native.user.GetForegroundWindow() == self.native.hwnd
        except Exception:
            active = True
        return (active, bool(self.native and self.native.maximized), self.dark(),
                self.hovered, self.pressed)

    def _refresh(self):
        if not self.enabled: return
        # Repaint only when the window state, theme or hover actually changed.
        # The previous version redrew all three buttons six times a second.
        if self._state_key() != getattr(self, '_state', None):
            self.theme()
        self.job = self.root.after(400, self._refresh)

    def _destroy(self, event):
        if event.widget is not self.root: return
        if self.install_job is not None:
            try: self.root.after_cancel(self.install_job)
            except tk.TclError: pass
            self.install_job = None
        if self.job:
            try: self.root.after_cancel(self.job)
            except tk.TclError: pass
        if self.native: self.native.dispose()
        # No native Python callback is installed or retained.
