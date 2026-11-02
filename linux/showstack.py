#!/usr/bin/env python3
# ShowStack for Linux: floating developer-environment widget. Top: a box per installed developer tool. Below: Docker,
# every localhost server found in your project code (grouped by project, with Start/Restart), and anything else
# listening on a port. GTK 3 through PyGObject, which Linux Mint ships with; the engine is showstack_engine.py.

import datetime
import os
import sys
import threading
import traceback

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import showstack_engine as engine  # noqa: E402

APP_ID = 'io.github.orangeriger1998.ShowStack'
WIDTH = 430
POSITION_FILE = os.path.join(engine.STATE_DIR, 'widget.json')
COLORS = {'Green': '#22C55E', 'Yellow': '#EAB308', 'Red': '#EF4444', 'Text': '#E6E9EF', 'Muted': '#8A93A6',
          'Section': '#6B7385', 'Group': '#AEB6C6', 'Link': '#7AA2F7', 'Chip': '#272C37', 'ChipBorder': '#343A46'}

CSS = '''
window.showstack { background-color: #1E222B; }
window.showstack.rounded { background-color: transparent; }
.showstack-frame { background-color: #1E222B; border: 1px solid #343A46; padding: 10px 14px 12px 14px; font-size: 12px; }
window.rounded .showstack-frame { border-radius: 10px; }
.showstack-frame label { color: #E6E9EF; }
.showstack-frame label.title { font-size: 15px; font-weight: 600; }
.showstack-frame label.small { font-size: 11px; }
.showstack-frame label.tiny { font-size: 10px; }
.showstack-frame label.semibold { font-weight: 600; }
.showstack-frame label.muted { color: #8A93A6; }
.showstack-frame label.section { color: #6B7385; }
.showstack-frame label.group { color: #AEB6C6; }
.showstack-frame label.link { color: #7AA2F7; }
.showstack-frame label.red { color: #EF4444; }
.showstack-frame label.green { color: #22C55E; }
.showstack-frame button { background-image: none; box-shadow: none; text-shadow: none; outline-width: 0; }
.showstack-frame button.btn { background-color: #2C3341; color: #E6E9EF; border: none; border-radius: 6px; padding: 7px 10px; min-height: 0; }
.showstack-frame button.btn label { color: #E6E9EF; }
.showstack-frame button.btn:hover { opacity: 0.82; }
.showstack-frame button.btn:disabled { opacity: 0.4; }
.showstack-frame button.primary { background-color: #2563EB; font-weight: 600; }
.showstack-frame button.restart { font-weight: 600; }
.showstack-frame button.small { padding: 2px 9px; font-size: 11px; }
.showstack-frame button.iconbtn { background-color: transparent; border: none; padding: 0 5px; min-width: 0; min-height: 0; color: #8A93A6; font-size: 15px; }
.showstack-frame button.iconbtn label { color: #8A93A6; font-size: 15px; }
.showstack-frame button.iconbtn:hover { opacity: 0.82; }
.showstack-frame button.folder { padding: 2px 4px 2px 1px; color: #6B7385; -gtk-icon-shadow: none; }
.showstack-frame button.folder:hover { color: #AEB6C6; opacity: 1; }
.showstack-frame button.linkbtn { background-color: transparent; border: none; padding: 0 4px; min-height: 0; }
.showstack-frame button.linkbtn label { color: #7AA2F7; font-size: 11px; }
.showstack-frame button.linkbtn:hover { opacity: 0.82; }
.showstack-frame .chip { background-color: #272C37; border: 1px solid #343A46; border-radius: 5px; padding: 2px 7px 2px 6px; }
.showstack-frame .chip.red { border-color: #EF4444; }
.showstack-frame .viewswitch { border: 1px solid #343A46; border-radius: 4px; }
.showstack-frame .segment { border-radius: 3px; padding: 0 7px 1px 7px; }
.showstack-frame .segment.selected { background-color: #272C37; }
.showstack-frame .splash { border-radius: 14px; background-size: cover; background-position: center; }
.showstack-frame .avatar { border-radius: 8px; background-size: cover; background-position: center; }
.showstack-frame scrolledwindow, .showstack-frame viewport, .showstack-frame eventbox, .showstack-frame grid, .showstack-frame box { background-color: transparent; }
.showstack-frame scrollbar { background-color: transparent; border: none; }
.showstack-frame scrollbar slider { background-color: #3A4150; border-radius: 3px; min-width: 6px; min-height: 24px; border: none; }
.showstack-frame scrollbar slider:hover { background-color: #566074; }
.showstack-frame scrollbar slider:active { background-color: #6B7385; }
tooltip, tooltip.background { background-color: #272C37; border: 1px solid #343A46; border-radius: 6px; }
tooltip * { color: #E6E9EF; }
'''


def asset(name):
    path = os.path.join(engine.ASSETS_DIR, name)
    return path if os.path.isfile(path) else None


def escape(text):
    return GLib.markup_escape_text(str(text if text is not None else ''))


def light_markup(state, size):
    """A coloured dot; size in pixels. (A glyph rather than a drawing: it needs no cairo bindings.)"""
    return '<span foreground="{}" size="{}">●</span>'.format(COLORS[state], int(size * 0.75 * 1024))


class FixedWidthBox(Gtk.Box):
    """The frame: always WIDTH wide, whatever its children would like, so the window never grows sideways and
    long texts ellipsize or wrap instead."""

    def do_get_preferred_width(self):
        return WIDTH, WIDTH


class ShowStackWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='ShowStack')
        self.job = False
        self.splash_text = ''
        self.splash_ticks = 0
        self.expanded = {}
        self.collapsed = {}     # section name -> folded
        self.running_only = False
        self.last_tools = []
        self.last_status = None
        self.menus = []         # keep context menus alive while their rows exist
        self.copy_reset = None

        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(False)
        self.set_wmclass('showstack', 'ShowStack')
        self.get_style_context().add_class('showstack')
        icon = asset('icon-256.png')
        if icon:
            try:
                self.set_icon_from_file(icon)
            except GLib.Error:
                pass
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)
            self.set_app_paintable(True)
            self.get_style_context().add_class('rounded')

        provider = Gtk.CssProvider()
        css = CSS
        for name, cls in (('avatar.png', 'avatar'), ('splash.png', 'splash')):
            path = asset(name)
            if path:
                css += '.showstack-frame .{} {{ background-image: url("{}"); }}\n'.format(cls, Gio.File.new_for_path(path).get_uri())
        provider.load_from_data(css.encode('utf-8'))
        Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.build()
        self.restore_position()
        self.connect('delete-event', self.on_delete)

    # ---- Layout
    def build(self):
        frame = FixedWidthBox(orientation=Gtk.Orientation.VERTICAL)
        frame.get_style_context().add_class('showstack-frame')   # not 'frame': GTK uses that class on its own viewports
        self.add(frame)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header.set_margin_bottom(6)
        avatar = Gtk.Box()
        avatar.set_size_request(36, 36)
        avatar.set_valign(Gtk.Align.CENTER)
        avatar.set_margin_end(10)
        avatar.get_style_context().add_class('avatar')
        header.pack_start(avatar, False, False, 0)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        titles.set_valign(Gtk.Align.CENTER)
        titles.pack_start(self.label('ShowStack', 'title'), False, False, 0)
        self.summary = self.label('Loading...', 'small', 'muted', ellipsize=True, max_chars=50)
        titles.pack_start(self.summary, False, False, 0)
        header.pack_start(titles, True, True, 0)
        self.copy_button = self.icon_button('edit-copy-symbolic', '⎘', 'Copy the whole stack to the clipboard (tools, services, servers)')
        self.copy_button.connect('clicked', self.on_copy)
        header.pack_start(self.copy_button, False, False, 0)
        minimize = self.icon_button(None, '–', 'Minimize')
        minimize.connect('clicked', lambda *_: self.iconify())
        header.pack_start(minimize, False, False, 0)
        close = self.icon_button(None, '×', 'Close')
        close.connect('clicked', lambda *_: self.close())
        header.pack_start(close, False, False, 0)
        drag = Gtk.EventBox()
        drag.add(header)
        drag.connect('button-press-event', self.on_header_press)
        frame.pack_start(drag, False, False, 0)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_propagate_natural_height(True)
        self.scroller.set_max_content_height(max(300, self.workarea().height - 190))
        self.scroller.set_shadow_type(Gtk.ShadowType.NONE)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.scroller.add(body)
        self.scroller.get_child().set_shadow_type(Gtk.ShadowType.NONE)
        frame.pack_start(self.scroller, True, True, 0)

        self.splash = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.splash.set_no_show_all(True)
        self.splash.set_margin_top(10)
        self.splash.set_margin_bottom(6)
        picture = Gtk.Box()
        picture.set_size_request(270, 270)
        picture.set_halign(Gtk.Align.CENTER)
        picture.get_style_context().add_class('splash')
        picture.show()
        self.splash.pack_start(picture, False, False, 0)
        self.splash_label = self.label('Searching for tools and servers', 'group', max_chars=50)
        self.splash_label.set_halign(Gtk.Align.CENTER)
        self.splash_label.set_margin_top(12)
        self.splash_label.show()
        self.splash.pack_start(self.splash_label, False, False, 0)
        body.pack_start(self.splash, False, False, 0)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.pack_start(self.content, False, False, 0)
        tools_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        tools_bar.set_margin_top(4)
        tools_bar.set_margin_bottom(4)
        self.tools_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.tools_header_box = self.clickable(self.tools_header, self.on_tools_header)
        tools_bar.pack_start(self.tools_header_box, True, True, 0)
        self.rescan_button = Gtk.Button.new_with_label('Rescan tools')
        self.style(self.rescan_button, 'linkbtn')
        self.rescan_button.set_relief(Gtk.ReliefStyle.NONE)
        self.rescan_button.set_tooltip_text('Look again for installed developer tools (after you install or remove one)')
        self.rescan_button.connect('clicked', lambda *_: self.run_background(lambda: engine.update_inventory(), 'Searching for installed tools and servers', splash=True))
        tools_bar.pack_end(self.rescan_button, False, False, 0)
        self.content.pack_start(tools_bar, False, False, 0)
        self.chips = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)   # rows of chips, wrapped by show_tools
        self.content.pack_start(self.chips, False, False, 0)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content.pack_start(self.rows, False, False, 0)

        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bottom.set_margin_top(12)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_homogeneous(True)
        self.check_button = Gtk.Button.new_with_label('CHECK STATUS')
        self.style(self.check_button, 'btn', 'primary')
        self.check_button.set_tooltip_text('Look for new servers in your project code and check what is running')
        self.check_button.connect('clicked', lambda *_: self.run_background(lambda: engine.update_inventory(servers_only=True),
                                                                               'Looking for servers in your projects and checking what is running...'))
        buttons.pack_start(self.check_button, True, True, 0)
        self.restart_button = Gtk.Button.new_with_label('RESTART ALL')
        self.style(self.restart_button, 'btn', 'restart')
        self.restart_button.set_tooltip_text('Start Docker if stopped, start or restart every server Claude Code registered, and restart every other server that is running')
        self.restart_button.connect('clicked', lambda *_: self.run_background(engine.restart_all, 'Starting Docker if needed, then restarting servers...'))
        buttons.pack_start(self.restart_button, True, True, 0)
        bottom.pack_start(buttons, False, False, 0)
        self.message = self.label('', 'small', 'muted', wrap=True, max_chars=60)
        self.message.set_no_show_all(True)
        self.message.set_margin_top(8)
        bottom.pack_start(self.message, False, False, 0)
        frame.pack_start(bottom, False, False, 0)

    # ---- Widget helpers
    @staticmethod
    def style(widget, *classes):
        context = widget.get_style_context()
        for cls in classes:
            context.add_class(cls)
        return widget

    def label(self, text, *classes, ellipsize=False, wrap=False, max_chars=None, markup=False):
        widget = Gtk.Label()
        if markup:
            widget.set_markup(text)
        else:
            widget.set_text(text)
        widget.set_xalign(0)
        widget.set_valign(Gtk.Align.CENTER)
        if ellipsize:
            widget.set_ellipsize(Pango.EllipsizeMode.END)
        if wrap:
            widget.set_line_wrap(True)
            widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        if max_chars:
            widget.set_max_width_chars(max_chars)
        self.style(widget, *classes)
        return widget

    def light(self, state, size=10):
        widget = self.label(light_markup(state, size), markup=True)
        widget.set_tooltip_text(state)
        return widget

    def icon_button(self, icon_name, fallback, tooltip, *classes):
        button = Gtk.Button()
        if icon_name and Gtk.IconTheme.get_default().has_icon(icon_name):
            button.set_image(Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON))
            button.set_always_show_image(True)
        else:
            button.set_label(fallback)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_focus_on_click(False)
        button.set_tooltip_text(tooltip)
        self.style(button, 'iconbtn', *classes)
        return button

    def clickable(self, child, on_click, tooltip=None, stop=False):
        """Wraps a widget so a left click calls on_click(widget, event), with a hand cursor while hovering."""
        box = Gtk.EventBox()
        box.add(child)
        box.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        if tooltip:
            box.set_tooltip_text(tooltip)

        def released(widget, event):
            if event.button == 1 and 0 <= event.x <= widget.get_allocated_width() and 0 <= event.y <= widget.get_allocated_height():
                on_click(widget, event)
                return stop
            return False
        box.connect('button-release-event', released)
        box.connect('enter-notify-event', lambda w, e: e.window.set_cursor(Gdk.Cursor.new_from_name(w.get_display(), 'pointer')))
        box.connect('leave-notify-event', lambda w, e: e.window.set_cursor(None))
        return box

    def context_menu(self, widget, entries):
        """Right-click menu: entries are (label, callback)."""
        menu = Gtk.Menu()
        for text, callback in entries:
            item = Gtk.MenuItem.new_with_label(text)
            item.connect('activate', lambda _item, cb=callback: cb())
            menu.append(item)
        menu.show_all()
        menu.attach_to_widget(widget, None)
        self.menus.append(menu)

        def pressed(_widget, event):
            if event.button == 3:
                menu.popup_at_pointer(event)
                return True
            return False
        widget.connect('button-press-event', pressed)

    def folder_icon(self, path):
        button = self.icon_button('folder-symbolic', '▣', 'Open in your file manager:\n{}'.format(path), 'folder')
        button.set_valign(Gtk.Align.CENTER)
        button.connect('clicked', lambda *_: self.open_folder(path))
        return button

    def chevron(self, open_):
        widget = self.label('▾' if open_ else '▸', 'group')
        widget.set_size_request(14, -1)
        return widget

    def set_section_label(self, panel, title, open_, suffix=''):
        """Fills a heading panel: chevron, TITLE, then a muted note such as counts. The whole panel is the click target."""
        for child in panel.get_children():
            panel.remove(child)
        panel.pack_start(self.chevron(open_), False, False, 0)
        panel.pack_start(self.label(title.upper(), 'tiny', 'semibold', 'section'), False, False, 0)
        if suffix:
            panel.pack_start(self.label('  ({})'.format(suffix), 'tiny', 'section'), False, False, 0)
        panel.get_parent().set_tooltip_text('Click to {} {}'.format('hide' if open_ else 'show', title.lower()))
        panel.show_all()

    def section_header(self, section, suffix=''):
        panel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        def toggle(*_):
            self.collapsed[section] = not self.collapsed.get(section)
            self.show_status(self.last_status)
        box = self.clickable(panel, toggle)
        self.set_section_label(panel, section, not self.collapsed.get(section), suffix)
        return box, panel

    def view_switch(self):
        """'All | Running' for the server list. A switch, not a link: it changes what is shown, it doesn't run anything."""
        switch = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.style(switch, 'viewswitch')
        switch.set_margin_start(12)
        switch.set_valign(Gtk.Align.CENTER)
        for text, running, tip in (('All', False, 'Show every project'), ('Running', True, 'Show only servers that are running')):
            selected = self.running_only == running
            segment = self.label(text, 'tiny', 'semibold' if selected else 'muted')
            self.style(segment, 'segment', *(['selected'] if selected else []))

            def choose(_w, _e, value=running):
                self.running_only = value
                self.show_status(self.last_status)
            switch.pack_start(self.clickable(segment, choose, tip, stop=True), False, False, 0)
        return switch

    def row(self, item):
        grid = Gtk.Grid()
        grid.set_margin_top(2)
        grid.set_margin_bottom(2)
        server = item['Server']
        # Columns: folder icon (servers only), light, name, detail, button.
        if server:
            grid.attach(self.folder_icon(self.code_folder(server)), 0, 0, 1, 1)
        light = self.light(item['State'])
        light.set_size_request(16, -1)
        light.set_xalign(0)
        grid.attach(light, 1, 0, 1, 1)

        name = self.label(item['Name'], 'semibold', ellipsize=True, max_chars=26)
        if item['Url']:
            self.style(name, 'link')
            url = item['Url']
            name_box = self.clickable(name, lambda *_: self.open_url(url), 'Open {}'.format(url))
            grid.attach(name_box, 2, 0, 1, 1)
        else:
            grid.attach(name, 2, 0, 1, 1)

        if item['State'] == 'Red':
            # Problems get the full text on a second line instead of being cut off.
            detail = self.label(item['Detail'], 'small', 'red', wrap=True, max_chars=50)
            detail.set_margin_top(1)
            detail.set_margin_bottom(2)
            grid.attach(detail, 2, 1, 3, 1)
        else:
            detail = self.label(item['Detail'], 'small', 'muted', ellipsize=True, max_chars=40)
            detail.set_tooltip_text(item['Detail'])
            detail.set_margin_start(8)
            detail.set_margin_end(6)
            detail.set_hexpand(True)
            grid.attach(detail, 3, 0, 1, 1)

        if item['Action']:
            button = Gtk.Button.new_with_label(item['Action'])
            self.style(button, 'btn', 'small')
            button.set_valign(Gtk.Align.CENTER)
            button.set_focus_on_click(False)
            button.connect('clicked', lambda *_: self.row_action(item))
            grid.attach(button, 4, 0, 1, 1)
        if item['State'] == 'Red':
            spacer = Gtk.Box()
            spacer.set_hexpand(True)
            grid.attach(spacer, 3, 0, 1, 1)

        box = Gtk.EventBox()
        box.add(grid)
        if server:
            self.context_menu(box, [('Hide from list', lambda: self.run_background(lambda: engine.hide_server(server['Key']), 'Hiding {}...'.format(item['Name']))),
                                    ('Open folder', lambda: self.open_folder(self.code_folder(server)))])
        return box

    @staticmethod
    def code_folder(server):
        """Where a server's code lives. A launch.json entry often sits in another folder than the script it runs."""
        signature = server.get('Signature') or ''
        if signature.startswith('/') and os.path.isfile(signature):
            return os.path.dirname(signature)
        return server['Dir']

    # ---- Rendering
    def chip(self, tool):
        panel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        dot = self.light(tool['State'], 7)
        dot.set_tooltip_text(None)
        dot.set_margin_end(5)
        panel.pack_start(dot, False, False, 0)
        panel.pack_start(self.label(tool['Name'], 'small'), False, False, 0)
        chip = Gtk.EventBox()
        chip.add(panel)
        self.style(chip, 'chip', *(['red'] if tool['State'] == 'Red' else []))
        if tool['Open'] or tool['Install']:
            chip = self.clickable(chip, lambda _w, _e, t=tool: self.tool_action(t))
        chip.set_tooltip_markup(self.tool_tooltip(tool))
        chip.set_margin_end(5)
        return chip

    def show_tools(self, tools):
        self.last_tools = tools
        for child in self.chips.get_children():
            self.chips.remove(child)
        installed = sum(1 for t in tools if t['State'] != 'Red')
        attention = sum(1 for t in tools if t['State'] != 'Green')
        folded = bool(self.collapsed.get('Tools'))
        suffix = '{} found'.format(installed) + (', {} need a look'.format(attention) if folded and attention else '')
        self.set_section_label(self.tools_header, 'Tools', not folded, suffix)

        # Wrap the chips into rows ourselves, each at its natural width. They are measured inside the frame so the
        # stylesheet (font sizes, padding) applies.
        chips = [self.chip(tool) for tool in tools]
        staging = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        for chip in chips:
            staging.pack_start(chip, False, False, 0)
        self.chips.pack_start(staging, False, False, 0)
        staging.show_all()
        widths = [chip.get_preferred_width()[1] for chip in chips]
        for chip in chips:
            staging.remove(chip)
        self.chips.remove(staging)
        available = WIDTH - 30   # frame padding and border
        row, used = None, 0
        for chip, width in zip(chips, widths):
            if row is None or used + width > available:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
                row.set_margin_bottom(5)
                self.chips.pack_start(row, False, False, 0)
                used = 0
            row.pack_start(chip, False, False, 0)
            used += width
        self.chips.show_all()
        self.chips.set_visible(not folded)

    @staticmethod
    def tool_tooltip(tool):
        """Hover card for a tool box: name, version (prominent), what it printed, category and location."""
        if tool['State'] == 'Red':
            version = 'Not installed'
        elif tool['Version']:
            version = 'Version {}'.format(tool['Version'])
        else:
            version = 'Version not reported'
        lines = ['<b>{}</b>'.format(escape(tool['Name'])),
                 '<span foreground="{}"><b>{}</b></span>'.format(COLORS[tool['State']], escape(version))]
        if tool['Detail'] and tool['Detail'] != 'Version {}'.format(tool['Version']):
            lines.append(escape(tool['Detail']))
        if tool['Category']:
            lines.append(escape(tool['Category']))
        if tool['Path']:
            lines.append(escape(tool['Path']))
        if tool['Open'] and tool['State'] != 'Red':
            lines.append('Click to open')
        elif tool['Install']:
            lines.append('Click to install')
        return '\n'.join(lines)

    def show_status(self, status):
        if not status:
            return
        self.last_status = status
        self.show_tools(list(status['Tools']))
        for child in self.rows.get_children():
            self.rows.remove(child)
        self.menus = []
        rows = [r for r in status['Rows'] if r]

        for section in ('Services', 'Servers', 'Local apps', 'Other local servers'):
            section_rows = [r for r in rows if r['Section'] == section]
            if not section_rows and not (section == 'Servers' and status['Hidden']):
                continue
            if section == 'Servers':
                suffix = '{} of {} running'.format(sum(1 for r in section_rows if r['State'] == 'Green'), len(section_rows))
            elif section == 'Local apps':
                running = sum(1 for r in section_rows if r['State'] == 'Green')
                suffix = str(len(section_rows)) if running == len(section_rows) else '{} of {} running'.format(running, len(section_rows))
            elif section == 'Other local servers':
                suffix = str(len(section_rows))
            else:
                suffix = ''
            header_box, header = self.section_header(section, suffix)
            header_box.set_margin_top(10)
            header_box.set_margin_bottom(3)
            if section == 'Servers':
                header.pack_start(self.view_switch(), False, False, 0)
                if status['Hidden']:
                    note = self.label('   {} hidden'.format(status['Hidden']), 'tiny', 'section')
                    note.set_tooltip_text('Right-click to show hidden servers again')
                    header.pack_start(note, False, False, 0)
                    self.context_menu(header_box, [('Show hidden servers again',
                                                    lambda: self.run_background(engine.clear_hidden_servers, 'Showing hidden servers...'))])
            self.rows.pack_start(header_box, False, False, 0)
            if self.collapsed.get(section):
                continue

            if section != 'Servers':
                for item in section_rows:
                    self.rows.pack_start(self.row(item), False, False, 0)
                continue

            # Servers: one collapsible group per project. Open by default when something in it runs, needs attention,
            # or was registered by Claude Code.
            idle_projects = 0
            groups = {}
            for item in section_rows:
                groups.setdefault(item['Group'], []).append(item)
            for group, items in groups.items():
                running = sum(1 for i in items if i['State'] == 'Green')
                problem = sum(1 for i in items if i['State'] == 'Red')
                if group not in self.expanded:
                    self.expanded[group] = bool(running or problem or any(i['Server']['Registered'] for i in items))
                open_ = self.expanded[group]
                if self.running_only:
                    # Only projects with something running (or a problem), and only those servers.
                    if not (running or problem):
                        idle_projects += 1
                        continue
                    open_ = True

                panel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
                panel.pack_start(self.chevron(open_), False, False, 0)
                project_folder = next((i['Server']['GroupPath'] for i in items if i['Server'].get('GroupPath')), None)
                if project_folder:
                    panel.pack_start(self.folder_icon(project_folder), False, False, 0)
                state = 'Red' if problem else 'Green' if running else 'Yellow'
                dot = self.light(state, 7)
                dot.set_tooltip_text(None)
                dot.set_margin_start(2)
                dot.set_margin_end(7)
                panel.pack_start(dot, False, False, 0)
                panel.pack_start(self.label(group, 'semibold', 'group', ellipsize=True, max_chars=30), False, False, 0)
                panel.pack_start(self.label('   {} of {} running'.format(running, len(items)), 'small', 'muted'), False, False, 0)

                def toggle(_w, _e, name=group):
                    self.expanded[name] = not self.expanded.get(name)
                    self.show_status(self.last_status)
                group_box = self.clickable(panel, toggle, 'Click to collapse' if open_ else 'Click to expand')
                group_box.set_margin_top(4)
                group_box.set_margin_bottom(1)
                self.rows.pack_start(group_box, False, False, 0)

                if open_:
                    for item in items:
                        if self.running_only and item['State'] not in ('Green', 'Red'):
                            continue
                        widget = self.row(item)
                        widget.set_margin_start(14)   # folder icons line up under the project's
                        self.rows.pack_start(widget, False, False, 0)
            if self.running_only:
                if idle_projects:
                    text = '{} project{} with nothing running hidden'.format(idle_projects, '' if idle_projects == 1 else 's')
                else:
                    text = 'Every project has something running'
                note = self.label(text + '. Choose All to see them.', 'small', 'muted', wrap=True, max_chars=55)
                note.set_margin_start(2)
                note.set_margin_top(6)
                self.rows.pack_start(note, False, False, 0)
        self.rows.show_all()

        servers = [r for r in rows if r['Section'] == 'Servers']
        parts = ['{} tools'.format(sum(1 for t in status['Tools'] if t['State'] != 'Red')),
                 '{} of {} servers running'.format(sum(1 for r in servers if r['State'] == 'Green'), len(servers))]
        apps = sum(1 for r in rows if r['Section'] == 'Local apps')
        if apps:
            parts.append('{} local app{}'.format(apps, '' if apps == 1 else 's'))
        problems = sum(1 for r in rows + list(status['Tools']) if r['State'] == 'Red')
        if problems:
            parts.append('{} need attention'.format(problems))
        self.summary.set_text('Checked {}  -  {}'.format(datetime.datetime.now().strftime('%-I:%M %p'), ', '.join(parts)))

    # ---- Messages, splash, busy state
    def show_message(self, text, color='muted'):
        context = self.message.get_style_context()
        for cls in ('muted', 'red', 'green'):
            context.remove_class(cls)
        context.add_class(color)
        self.message.set_text(text or '')
        self.message.set_visible(bool(text))

    def show_splash(self, text):
        """The splash artwork replaces the lists while ShowStack searches for tools and servers."""
        self.splash_text = text
        self.splash_ticks = 0
        self.splash_label.set_text(text)
        self.splash.show()
        self.content.hide()
        GLib.timeout_add(250, self.on_tick)

    def hide_splash(self):
        self.splash_text = ''
        self.splash.hide()
        self.content.show()

    def on_tick(self):
        if not self.splash_text:
            return False
        self.splash_ticks += 1
        self.splash_label.set_text(self.splash_text + '.' * ((self.splash_ticks // 2) % 4))
        return True

    def set_busy(self, busy, text=''):
        for control in (self.check_button, self.restart_button, self.rescan_button, self.rows, self.chips):
            control.set_sensitive(not busy)
        self.show_message(text, 'muted')

    # ---- Background work: one worker thread at a time with the engine; the result lands back on the GTK thread.
    def run_background(self, body, text, splash=False):
        if self.job:
            return
        self.job = True
        if splash:
            self.show_splash(text)
            self.set_busy(True)
        else:
            self.set_busy(True, text)

        def work():
            failure = None
            status = None
            try:
                body()
            except Exception as error:   # noqa: BLE001 - shown to the user
                failure = str(error) or repr(error)
                engine.log(traceback.format_exc())
            try:
                status = engine.get_status()
            except Exception as error:   # noqa: BLE001
                failure = failure or (str(error) or repr(error))
                engine.log(traceback.format_exc())
            GLib.idle_add(self.finish_job, status, failure)
        threading.Thread(target=work, daemon=True).start()

    def finish_job(self, status, failure):
        self.job = False
        self.set_busy(False)
        self.hide_splash()
        if status:
            self.show_status(status)
        if failure:
            engine.log(failure)
            self.show_message(failure, 'red')
        return False

    # ---- Actions
    def tool_action(self, tool):
        if tool['Open'] and tool['State'] != 'Red':
            try:
                engine.open_tool(tool)
            except Exception as error:   # noqa: BLE001
                self.show_message(str(error), 'red')
            return
        if tool['Install']:
            dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                       buttons=Gtk.ButtonsType.YES_NO, text='Install {}?'.format(tool['Name']))
            dialog.format_secondary_text('This opens a terminal window that runs:\n{}'.format(tool['Install']))
            answer = dialog.run()
            dialog.destroy()
            if answer == Gtk.ResponseType.YES:
                name = tool['Name']
                self.run_background(lambda: (engine.install_tool(name), engine.update_inventory()),
                                    'Installing {} in a terminal window. Follow it there; the boxes update when you close it.'.format(name))

    def row_action(self, item):
        server = item['Server']
        if item['Section'] == 'Local apps':
            name = (item.get('App') or {}).get('Container')
            if name and item['Action'] == 'Start':
                self.run_background(lambda: engine.start_container(name), 'Starting the Docker container {}...'.format(name))
            elif name and item['Action'] == 'Restart':
                self.run_background(lambda: engine.restart_container(name), 'Restarting the Docker container {}...'.format(name))
            return
        if item['Action'] == 'Start':
            if item['Section'] == 'Services':
                self.run_background(engine.start_docker_engine, 'Starting Docker (can take up to 2 minutes)...')
            else:
                self.run_background(lambda: engine.start_server(server), 'Starting {}: {}...'.format(server['Project'], item['Name']))
        elif item['Action'] == 'Restart':
            self.run_background(lambda: engine.start_server(server), 'Restarting {}: {}...'.format(server['Project'], item['Name']))
        elif item['Action'] == 'Install':
            self.run_background(lambda: engine.install_server_dependencies(server),
                                'Installing packages for {}: {} in a terminal window. The list updates when you close it.'.format(server['Project'], item['Name']))

    def on_tools_header(self, *_):
        self.collapsed['Tools'] = not self.collapsed.get('Tools')
        self.show_tools(self.last_tools)

    def on_copy(self, *_):
        """Copy: the whole stack as Markdown on the clipboard; the icon turns into a check mark for a moment."""
        if not self.last_status:
            self.show_message('Nothing to copy yet; wait for the check to finish.', 'muted')
            return
        try:
            export = engine.export_markdown(self.last_status)
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(export, -1)
            clipboard.store()
            servers = sum(1 for r in self.last_status['Rows'] if r['Section'] == 'Servers')
            if Gtk.IconTheme.get_default().has_icon('object-select-symbolic'):
                self.copy_button.set_image(Gtk.Image.new_from_icon_name('object-select-symbolic', Gtk.IconSize.BUTTON))
            else:
                self.copy_button.set_label('✓')
            if self.copy_reset:
                GLib.source_remove(self.copy_reset)
            self.copy_reset = GLib.timeout_add(2000, self.reset_copy_icon)
            self.show_message('Copied the whole stack to the clipboard: {} tools, {} servers, as Markdown.'.format(
                len(self.last_status['Tools']), servers), 'green')
        except Exception as error:   # noqa: BLE001
            engine.log(traceback.format_exc())
            self.show_message("Couldn't copy to the clipboard: {}".format(error), 'red')

    def reset_copy_icon(self):
        self.copy_reset = None
        if Gtk.IconTheme.get_default().has_icon('edit-copy-symbolic'):
            self.copy_button.set_image(Gtk.Image.new_from_icon_name('edit-copy-symbolic', Gtk.IconSize.BUTTON))
        else:
            self.copy_button.set_label('⎘')
        return False

    def open_url(self, url):
        try:
            Gtk.show_uri_on_window(self, url, Gdk.CURRENT_TIME)
        except GLib.Error as error:
            self.show_message("Couldn't open {}: {}".format(url, error.message), 'red')

    def open_folder(self, path):
        if os.path.isdir(path):
            self.open_url(Gio.File.new_for_path(path).get_uri())
        else:
            self.show_message('That folder no longer exists: {}. Click CHECK STATUS to refresh the list.'.format(path), 'red')

    def on_header_press(self, _widget, event):
        if event.button == 1:
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
            return True
        return False

    # ---- Position: last place it was left, otherwise the top-right corner of the screen.
    def workarea(self):
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        return monitor.get_workarea()

    def restore_position(self):
        area = self.workarea()
        left, top = area.x + area.width - WIDTH - 24, area.y + 24
        saved = engine.read_state(POSITION_FILE, None)
        if saved:
            try:
                display = Gdk.Display.get_default()
                bounds = [display.get_monitor(i).get_geometry() for i in range(display.get_n_monitors())]
                min_x = min(b.x for b in bounds)
                min_y = min(b.y for b in bounds)
                max_x = max(b.x + b.width for b in bounds)
                max_y = max(b.y + b.height for b in bounds)
                if min_x <= int(saved['Left']) < max_x - 80 and min_y <= int(saved['Top']) < max_y - 80:
                    left, top = int(saved['Left']), int(saved['Top'])
                for section in saved.get('CollapsedSections') or []:
                    if section:
                        self.collapsed[str(section)] = True
                self.running_only = bool(saved.get('RunningOnly'))
            except (KeyError, TypeError, ValueError):
                pass
        self.move(left, top)

    def on_delete(self, *_):
        try:
            left, top = self.get_position()
            engine.write_state(POSITION_FILE, {'Left': left, 'Top': top, 'RunningOnly': self.running_only,
                                               'CollapsedSections': [k for k, v in self.collapsed.items() if v]})
        except Exception:   # noqa: BLE001
            pass
        return False

    # On open: the splash shows while ShowStack checks status (servers in the code, what is running).
    # Tools are scanned only the first time, or when Rescan tools is clicked; otherwise the saved list is used.
    def start(self):
        cached = None
        try:
            cached = engine.get_inventory()
        except Exception:   # noqa: BLE001
            engine.log(traceback.format_exc())
        if cached and cached['Tools']:
            self.show_tools(cached['Tools'])
            self.run_background(lambda: engine.update_inventory(servers_only=True), 'Searching for servers', splash=True)
        else:
            self.run_background(lambda: engine.update_inventory(), 'Searching for tools and servers', splash=True)
        return False


class ShowStackApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window = None

    def do_activate(self):
        if self.window:
            self.window.present()   # a second launch just brings the open widget forward
            return
        self.window = ShowStackWindow(self)
        self.window.show_all()
        self.window.splash.hide()
        self.window.message.hide()
        GLib.idle_add(self.window.start)


def main():
    GLib.set_prgname('showstack')
    GLib.set_application_name('ShowStack')
    app = ShowStackApp()
    try:
        return app.run(sys.argv)
    except Exception:   # noqa: BLE001
        engine.log(traceback.format_exc())
        raise


if __name__ == '__main__':
    sys.exit(main())
