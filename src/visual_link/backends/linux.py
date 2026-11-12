"""Linux adapters. GUI dependencies are imported only when displaying."""
import sys
import time

import numpy as np
from ..core.image import image_parameters


def display_one(config, image, seconds, ready=None, advance=None):
    """Hold a single image at 1:1 pixels, restore the window on exit."""
    parameters = image_parameters(config, 'tx')
    width, height = parameters.width, parameters.height
    if not sys.platform.startswith('linux'):
        raise ValueError('GTK display requires Linux')
    if image.dtype != np.uint8 or image.shape not in (parameters.shape, (*parameters.shape, 3)):
        raise ValueError(f'display requires native {width}x{height} uint8 gray or BGR')
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk, Gdk, GdkPixbuf, GLib
    except (ImportError, ValueError) as exc:
        raise ValueError('GTK3/PyGObject required; see docs/HARDWARE.md') from exc
    ok, _ = Gtk.init_check()
    if not ok:
        raise ValueError('no GTK desktop; run in the local graphical session')
    display = Gdk.Display.get_default()
    index = config['monitor']
    if index >= display.get_n_monitors():
        raise ValueError('monitor index out of range')
    monitor = display.get_monitor(index)
    rect = monitor.get_geometry()
    if (rect.width, rect.height, monitor.get_scale_factor()) != (width, height, 1):
        raise ValueError(f'monitor must be {width}x{height} at scale 1; scaling is forbidden')
    def make_pixbuf(image):
        if image.dtype != np.uint8 or image.shape not in (parameters.shape, (*parameters.shape, 3)):
            raise ValueError(f'display requires native {width}x{height} pixels')
        rgb = np.repeat(image[:, :, None], 3, axis=2) if image.ndim == 2 else image[:, :, ::-1]
        pixels = GLib.Bytes.new(np.ascontiguousarray(rgb).tobytes())
        return GdkPixbuf.Pixbuf.new_from_bytes(pixels, GdkPixbuf.Colorspace.RGB,
                                           False, 8, width, height, width*3)
    pixbuf = make_pixbuf(image)
    window = Gtk.Window()
    picture = Gtk.Image.new_from_pixbuf(pixbuf)
    window.add(picture)
    window.set_decorated(False)
    window.move(rect.x, rect.y)
    window.fullscreen_on_monitor(window.get_screen(), index)
    state = dict(start=None, error=None, cancelled=False)
    def destroyed(*_):
        if Gtk.main_level():
            state['cancelled'] = True
            Gtk.main_quit()

    window.connect('destroy', destroyed)

    def key_press(widget, event):
        if event.keyval == Gdk.KEY_Escape:
            state['cancelled'] = True
            Gtk.main_quit()

    window.connect('key-press-event', key_press)
    deadline = time.monotonic() + config['timeout']

    def check():
        size = window.get_allocation()
        native = (size.width, size.height, window.get_scale_factor()) == (width, height, 1)
        full = window.get_window().get_state() & Gdk.WindowState.FULLSCREEN
        if state['start'] is None:
            if native and full:
                window.get_window().set_cursor(Gdk.Cursor.new_for_display(display, Gdk.CursorType.BLANK_CURSOR))
                state['start'] = time.monotonic()
                if ready is not None:
                    ready(dict(status='ready', monotonic_ns=time.monotonic_ns(), monitor=index))
            elif time.monotonic() >= deadline:
                state['error'] = 'fullscreen/native geometry not confirmed before timeout'
                Gtk.main_quit()
                return False
        elif not native or not full:
            state['error'] = 'fullscreen/native geometry lost'
            Gtk.main_quit()
            return False
        elif time.monotonic() - state['start'] >= seconds:
            Gtk.main_quit()
            return False
        if state['start'] is not None and advance is not None:
            try:
                next_image = advance()
                if next_image is not None:
                    picture.set_from_pixbuf(make_pixbuf(next_image))
            except Exception as exc:
                state['error'] = str(exc)
                Gtk.main_quit()
                return False
        return True

    window.show_all()
    timer = GLib.timeout_add(5 if advance else 50, check)
    try:
        Gtk.main()
    finally:
        # Source may already have removed itself on normal completion.
        if GLib.MainContext.default().find_source_by_id(timer):
            GLib.source_remove(timer)
        window.destroy()
    if state['error']:
        raise ValueError(state['error'])
    if state['cancelled'] or state['start'] is None:
        raise ValueError('display cancelled')
    return dict(monitor=index, width=width, height=height, scale=1,
                held_seconds=time.monotonic()-state['start'], physical_output_verified=False)
