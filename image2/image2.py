#!/usr/bin/env python3
"""Image2 — a simple image viewer for GNOME, built with GTK 4 and libadwaita."""

import filecmp
import json
import math
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from xml.sax.saxutils import escape as xml_escape

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango

APP_ID = "io.github.imagery.Image2"
APP_NAME = "Image2"
VERSION = "1.0"

THUMB_SIZE = 40
ZOOM_STEP = 1.25
ZOOM_MIN = 0.02
ZOOM_MAX = 32.0

_BOLD = Pango.AttrList()
_BOLD.insert(Pango.attr_weight_new(Pango.Weight.BOLD))

WALLPAPER_SCHEMA = "org.gnome.desktop.background"

# (group, [(description, accelerators)]) for the Keyboard Shortcuts window
SHORTCUTS = [
    ("General", [
        ("Open image", "<Control>o"),
        ("Open folder", "<Control><Shift>o"),
        ("Preferences", "<Control>comma"),
        ("Keyboard shortcuts", "<Control>question"),
        ("Help", "F1"),
        ("Close window", "<Control>w"),
        ("Quit", "<Control>q"),
    ]),
    ("Navigation", [
        ("Next image", "Right Page_Down space"),
        ("Previous image", "Left Page_Up BackSpace"),
        ("First / last image", "Home End"),
        ("Toggle sidebar", "F9"),
        ("Fullscreen", "F11"),
        ("Leave fullscreen", "Escape"),
    ]),
    ("Zoom", [
        ("Zoom in", "plus <Control>plus"),
        ("Zoom out", "minus <Control>minus"),
        ("Best fit", "0 <Control>0"),
        ("Original size", "1 <Control>1"),
    ]),
    ("Files", [
        ("Select all images", "<Control>a"),
        ("Copy to folder", "<Control><Shift>c"),
        ("Move to folder", "<Control><Shift>m"),
        ("Move to pinned folder 1–9", "<Alt>1...<Alt>9"),
        ("Copy to pinned folder 1–9", "<Control><Alt>1...<Control><Alt>9"),
        ("Rename", "F2"),
        ("Delete", "Delete"),
        ("Open in external editor", "<Control>e"),
    ]),
]

# (page title, icon, [(group title, [(row title, row text)])]) for the Help dialog
HELP = [
    ("Viewing", "image-x-generic-symbolic", [
        ("Opening Images", [
            ("Open an image or folder",
             "Use the open button (Ctrl+O), Open Folder… in the main menu (Ctrl+Shift+O), drag an image "
             "or folder onto the window, or choose Image2 under Open With… in Files."),
            ("Browse the folder",
             "Every image in the opened image's folder is listed in the sidebar. Use the arrow buttons, "
             "Left/Right, Page Up/Down or Space/Backspace to step through them, Home/End for first/last."),
            ("Image details",
             "The title bar shows the file name, its position in the folder, its size in pixels and on disk."),
        ]),
        ("Zooming", [
            ("Zoom in and out",
             "Use the zoom buttons, + and −, Ctrl+mouse wheel (zooms towards the pointer) or pinch on a touchpad."),
            ("Best fit and original size",
             "Press 0 for best fit or 1 for 100 %. Clicking the percentage or double-clicking the image "
             "switches between the two."),
            ("Pan around", "When zoomed in, drag the image or use the scrollbars."),
        ]),
        ("Window", [
            ("Sidebar", "Show or hide the sidebar with its button or F9. It collapses automatically in narrow windows."),
            ("Fullscreen", "Press F11 or choose Fullscreen in the main menu. Escape leaves fullscreen."),
        ]),
    ]),
    ("Organising", "folder-symbolic", [
        ("Pinned Folders", [
            ("Pin folders",
             "Click + above Folders, choose Pin Current Folder to Sidebar in the main menu, or drag folders "
             "from Files onto the Folders list. Your Pictures folder is pinned the first time Image2 starts."),
            ("Browse subfolders", "Expand a folder with its arrow; click any folder to show its images."),
            ("Folder menu",
             "Right-click a folder to open it, show it in Files, move or copy the selected images into it, "
             "create a new subfolder, or pin/unpin it."),
        ]),
        ("Moving and Copying", [
            ("Select several images", "Ctrl-click or Shift-click in the image list, or press Ctrl+A to select all."),
            ("Drag onto a folder",
             "Drag images from the list onto a folder to move them there. Hold Ctrl while dropping to copy."),
            ("Quick keys", "Alt+1…9 moves the selection into pinned folder 1–9; Ctrl+Alt+1…9 copies it."),
            ("Copy or move to any folder",
             "Use the buttons under the image list, the right-click menu, Ctrl+Shift+C or Ctrl+Shift+M."),
            ("Undo", "After a move, click Undo in the message at the bottom of the window to put the images back."),
            ("No overwriting",
             "If a file with the same name already exists, the new one is saved as “name (2).jpg” instead."),
        ]),
        ("Renaming and Deleting", [
            ("Rename", "Select one image and press F2 or use the rename button."),
            ("Delete",
             "Press Delete or use the trash button. Images go to the Trash unless you turn that off in Preferences; "
             "permanent deletion always asks first."),
        ]),
    ]),
    ("Editing & More", "document-edit-symbolic", [
        ("External Editor", [
            ("Choose an editor",
             "In Preferences, pick an installed graphics app or enter a custom command such as “gimp %f”."),
            ("Open in the editor",
             "Press Ctrl+E or use the edit button. When the editor saves, Image2 reloads the image automatically."),
            ("Other apps", "Open With… lets you pick any installed app for the current image."),
        ]),
        ("Wallpaper", [
            ("Set as wallpaper",
             "Choose Set as Wallpaper in the main menu or the right-click menu. Image2 copies the image to "
             "~/.local/share/backgrounds and uses it for both the light and dark desktop style, so the "
             "wallpaper keeps working if you later move or delete the original."),
        ]),
        ("Preferences", [
            ("Image list", "Turn thumbnails on or off, show hidden files, and sort by name or date modified."),
            ("Supported formats",
             "Image2 opens every format your system's image loaders support, such as JPEG, PNG, WebP, AVIF, "
             "HEIC, TIFF, GIF, SVG and BMP."),
        ]),
    ]),
]


def build_shortcuts_window():
    def attr(name, value):
        return f'<property name="{name}">{xml_escape(value)}</property>'

    groups = []
    for title, shortcuts in SHORTCUTS:
        items = "".join(
            f'<child><object class="GtkShortcutsShortcut">{attr("title", desc)}{attr("accelerator", accel)}'
            f'</object></child>' for desc, accel in shortcuts)
        groups.append(f'<child><object class="GtkShortcutsGroup">{attr("title", title)}{items}</object></child>')
    ui = ('<interface><object class="GtkShortcutsWindow" id="shortcuts"><property name="modal">1</property>'
          '<child><object class="GtkShortcutsSection"><property name="section-name">shortcuts</property>'
          '<property name="max-height">12</property>' + "".join(groups) +
          '</object></child></object></interface>')
    return Gtk.Builder.new_from_string(ui, -1).get_object("shortcuts")


class HelpDialog(Adw.PreferencesDialog):
    """Help topics, laid out like Preferences so they get a built-in search."""

    def __init__(self):
        super().__init__(title="Help", search_enabled=True)
        for page_title, icon, groups in HELP:
            page = Adw.PreferencesPage(title=page_title, icon_name=icon)
            for group_title, rows in groups:
                group = Adw.PreferencesGroup(title=group_title)
                for title, text in rows:
                    row = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(text),
                                        subtitle_selectable=True)
                    group.add(row)
                page.add(group)
            self.add(page)


CSS = """
.folder-row:drop(active) {
    background-color: alpha(@accent_bg_color, 0.3);
    border-radius: 6px;
}
"""


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _supported_extensions():
    exts = set()
    for fmt in GdkPixbuf.Pixbuf.get_formats():
        exts.update("." + ext.lower() for ext in fmt.get_extensions())
    # Longest first so ".svg.gz" wins over ".gz"-style ambiguities.
    return tuple(sorted(exts, key=len, reverse=True))


IMAGE_EXTENSIONS = _supported_extensions()


def is_image(name):
    return name.lower().endswith(IMAGE_EXTENSIONS)


def plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def unique_path(folder, name):
    """Return a path in folder for name that doesn't exist yet ("x (2).png")."""
    dest = os.path.join(folder, name)
    if not os.path.lexists(dest):
        return dest
    stem, ext = os.path.splitext(name)
    n = 2
    while True:
        dest = os.path.join(folder, f"{stem} ({n}){ext}")
        if not os.path.lexists(dest):
            return dest
        n += 1


def load_pixbuf(path):
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    return pixbuf.apply_embedded_orientation() or pixbuf


def load_thumbnail(path, size):
    # Prefer the thumbnail GNOME Files already generated, if it is fresh.
    try:
        info = Gio.File.new_for_path(path).query_info(
            "thumbnail::path,thumbnail::is-valid", Gio.FileQueryInfoFlags.NONE, None)
        thumb = info.get_attribute_byte_string("thumbnail::path")
        if thumb and info.get_attribute_boolean("thumbnail::is-valid"):
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(thumb, size, size, True)
    except GLib.Error:
        pass
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
    return pixbuf.apply_embedded_orientation() or pixbuf


def image_editor_candidates():
    """Installed applications that can open common image types."""
    found = {}
    for mime in ("image/png", "image/jpeg", "image/tiff", "image/webp", "image/gif", "image/svg+xml"):
        for app in Gio.AppInfo.get_all_for_type(mime):
            app_id = app.get_id()
            if app_id and app_id not in found and app_id != APP_ID + ".desktop" and app.should_show():
                found[app_id] = app
    # Browsers and office suites also claim images; keep to graphics apps when possible.
    graphics = {k: a for k, a in found.items()
                if "Graphics" in ((a.get_categories() or "") if hasattr(a, "get_categories") else "")}
    return sorted((graphics or found).values(), key=lambda a: a.get_display_name().lower())


def find_app(app_id):
    for app in Gio.AppInfo.get_all():
        if app.get_id() == app_id:
            return app
    return None


def run_in_thread(work, done):
    """Run work() in a thread, then done(result, error) on the main loop."""
    def worker():
        try:
            result, error = work(), None
        except Exception as e:  # reported to the user via done()
            result, error = None, e
        GLib.idle_add(lambda: done(result, error) and False)
    threading.Thread(target=worker, daemon=True).start()


# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------

class Settings(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    DEFAULTS = {
        "editor_mode": "none",          # none | app | custom
        "editor_app": "",               # desktop file id
        "editor_command": "",           # e.g. "gimp %f"
        "use_trash": True,
        "confirm_delete": True,
        "show_thumbnails": True,
        "show_hidden": False,
        "sort_by": "name",              # name | date
        "last_target_folder": "",
        "last_folder": "",
        "folders": [],                  # folders pinned in the sidebar
    }

    def __init__(self):
        super().__init__()
        self.path = os.path.join(GLib.get_user_config_dir(), "image2", "settings.json")
        self.values = dict(self.DEFAULTS)
        # On first run, pin the Pictures folder so the Folders section isn't empty.
        pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
        if pictures and os.path.isdir(pictures):
            self.values["folders"] = [pictures]
        try:
            with open(self.path) as f:
                stored = json.load(f)
            self.values.update({k: v for k, v in stored.items() if k in self.DEFAULTS})
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self.values[key]

    def __setitem__(self, key, value):
        if self.values.get(key) == value:
            return
        self.values[key] = value
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w") as f:
                json.dump(self.values, f, indent=2)
        except OSError as e:
            print(f"Could not save settings: {e}", file=sys.stderr)
        self.emit("changed", key)


# ----------------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------------

class ImageItem(GObject.Object):
    def __init__(self, path, mtime, size):
        super().__init__()
        self.path = path
        self.name = os.path.basename(path)
        self.display_name = GLib.filename_display_name(self.name)
        self.mtime = mtime
        self.size = size


def scan_folder(folder, sort_by, show_hidden):
    items = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return items
    for entry in entries:
        if (not show_hidden and entry.name.startswith(".")) or not is_image(entry.name):
            continue
        try:
            if not entry.is_file():
                continue
            st = entry.stat()
        except OSError:
            continue
        items.append(ImageItem(entry.path, st.st_mtime_ns, st.st_size))

    def name_key(item):
        return GLib.utf8_collate_key_for_filename(item.display_name, -1)

    if sort_by == "date":
        items.sort(key=lambda i: (i.mtime, name_key(i)))
    else:
        items.sort(key=name_key)
    return items


class FolderNode(GObject.Object):
    def __init__(self, path, is_root=False):
        super().__init__()
        self.path = path
        self.is_root = is_root
        self.name = GLib.filename_display_basename(path)


def list_subfolders(path, show_hidden):
    try:
        entries = list(os.scandir(path))
    except OSError:
        return []
    folders = []
    for entry in entries:
        if not show_hidden and entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                folders.append(FolderNode(entry.path))
        except OSError:
            continue
    folders.sort(key=lambda f: GLib.utf8_collate_key_for_filename(f.name, -1))
    return folders


class ThumbnailCache:
    def __init__(self):
        self.cache = {}
        self.pending = {}
        self.pool = ThreadPoolExecutor(max_workers=3)

    def get(self, item, callback):
        """Return a cached texture, or None and call callback(texture) later."""
        key = (item.path, item.mtime)
        if key in self.cache:
            return self.cache[key]
        if key in self.pending:
            self.pending[key].append(callback)
            return None
        self.pending[key] = [callback]
        self.pool.submit(self._work, key)
        return None

    def _work(self, key):
        try:
            pixbuf = load_thumbnail(key[0], THUMB_SIZE * 2)
        except GLib.Error:
            pixbuf = None
        GLib.idle_add(self._done, key, pixbuf)

    def _done(self, key, pixbuf):
        texture = Gdk.Texture.new_for_pixbuf(pixbuf) if pixbuf else None
        self.cache[key] = texture
        for callback in self.pending.pop(key, []):
            if texture:
                callback(texture)
        return False

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


# ----------------------------------------------------------------------------
# Image widget
# ----------------------------------------------------------------------------

class ImageView(Gtk.Widget):
    """Draws a texture either fitted to the available space or at a fixed zoom."""

    __gtype_name__ = "ImageryImageView"
    __gsignals__ = {"scale-changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.texture = None
        self.fit = True
        self.zoom = 1.0
        self.fit_scale = 1.0
        self.set_overflow(Gtk.Overflow.HIDDEN)

    @property
    def scale(self):
        return self.fit_scale if self.fit else self.zoom

    def set_texture(self, texture, keep_zoom=False):
        self.texture = texture
        if not keep_zoom:
            self.fit = True
        self.queue_resize()
        self.emit("scale-changed")

    def set_zoom(self, zoom):
        self.fit = False
        self.zoom = zoom
        self.queue_resize()
        self.emit("scale-changed")

    def set_fit(self):
        self.fit = True
        self.queue_resize()
        self.emit("scale-changed")

    def do_measure(self, orientation, for_size):
        if self.texture is None or self.fit:
            return 0, 0, -1, -1
        if orientation == Gtk.Orientation.HORIZONTAL:
            size = self.texture.get_width()
        else:
            size = self.texture.get_height()
        n = math.ceil(size * self.zoom)
        return n, n, -1, -1

    def do_size_allocate(self, width, height, baseline):
        if self.texture is None or not self.fit:
            return
        tw, th = self.texture.get_width(), self.texture.get_height()
        scale = min(1.0, width / tw, height / th) if tw and th else 1.0
        if abs(scale - self.fit_scale) > 1e-6:
            self.fit_scale = scale
            # Don't touch other widgets in the middle of a layout pass.
            GLib.idle_add(lambda: self.emit("scale-changed") and False)

    def do_snapshot(self, snapshot):
        if self.texture is None:
            return
        scale = self.scale
        dw = self.texture.get_width() * scale
        dh = self.texture.get_height() * scale
        x = round(max(0.0, (self.get_width() - dw) / 2))
        y = round(max(0.0, (self.get_height() - dh) / 2))
        if scale < 1:
            filt = Gsk.ScalingFilter.TRILINEAR
        elif scale >= 3:
            filt = Gsk.ScalingFilter.NEAREST
        else:
            filt = Gsk.ScalingFilter.LINEAR
        rect = Graphene.Rect().init(x, y, dw, dh)
        snapshot.append_scaled_texture(self.texture, filt, rect)


# ----------------------------------------------------------------------------
# Preferences
# ----------------------------------------------------------------------------

class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, settings):
        super().__init__(title="Preferences")
        self.settings = settings
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        self.add(page)

        # External editor
        editor = Adw.PreferencesGroup(
            title="External Editor",
            description="Used by “Open in Editor” (Ctrl+E). The image reloads automatically after you save.")
        page.add(editor)

        self.apps = image_editor_candidates()
        names = ["None"] + [a.get_display_name() for a in self.apps] + ["Custom Command"]
        self.editor_row = Adw.ComboRow(title="Editor", model=Gtk.StringList.new(names))
        self.editor_row.set_selected(self._editor_index())
        self.editor_row.connect("notify::selected", self._on_editor_selected)
        editor.add(self.editor_row)

        self.command_row = Adw.EntryRow(title="Command  (%f is replaced by the file path)")
        self.command_row.set_text(settings["editor_command"])
        self.command_row.connect("changed", lambda row: settings.__setitem__("editor_command", row.get_text()))
        editor.add(self.command_row)
        self._update_command_row()

        # Files
        files = Adw.PreferencesGroup(title="Deleting")
        page.add(files)
        files.add(self._switch("Move to Trash", "When off, deleted images are removed permanently", "use_trash"))
        files.add(self._switch("Confirm Before Deleting", "Permanent deletion is always confirmed", "confirm_delete"))

        # Sidebar
        sidebar = Adw.PreferencesGroup(title="Image List")
        page.add(sidebar)
        sidebar.add(self._switch("Show Thumbnails", None, "show_thumbnails"))
        sidebar.add(self._switch("Show Hidden Files", None, "show_hidden"))
        sort_row = Adw.ComboRow(title="Sort By", model=Gtk.StringList.new(["Name", "Date Modified"]))
        sort_row.set_selected(1 if settings["sort_by"] == "date" else 0)
        sort_row.connect("notify::selected",
                         lambda row, _: settings.__setitem__("sort_by", "date" if row.get_selected() else "name"))
        sidebar.add(sort_row)

    def _switch(self, title, subtitle, key):
        row = Adw.SwitchRow(title=title, active=self.settings[key])
        if subtitle:
            row.set_subtitle(subtitle)
        row.connect("notify::active", lambda r, _: self.settings.__setitem__(key, r.get_active()))
        return row

    def _editor_index(self):
        mode = self.settings["editor_mode"]
        if mode == "custom":
            return len(self.apps) + 1
        if mode == "app":
            for i, app in enumerate(self.apps):
                if app.get_id() == self.settings["editor_app"]:
                    return i + 1
        return 0

    def _on_editor_selected(self, row, _):
        i = row.get_selected()
        if i == 0:
            self.settings["editor_mode"] = "none"
        elif i <= len(self.apps):
            self.settings["editor_app"] = self.apps[i - 1].get_id()
            self.settings["editor_mode"] = "app"
        else:
            self.settings["editor_mode"] = "custom"
        self._update_command_row()

    def _update_command_row(self):
        self.command_row.set_visible(self.settings["editor_mode"] == "custom")


# ----------------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------------

class ImageWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=1100, default_height=720)
        self.set_size_request(360, 300)
        self.settings = app.settings
        self.thumbs = app.thumbs

        self.folder = None
        self.monitor = None
        self.rescan_source = 0
        self.current_path = None
        self.current_mtime = None
        self.load_generation = 0
        self.path_index = {}
        self.updating = False
        self.pointer = None
        self.sidebar_before_fullscreen = True

        self._build_ui()
        self._install_actions()
        self._install_input()
        self.settings_handler = self.settings.connect("changed", self._on_settings_changed)
        self.connect("close-request", self._on_close)
        self.connect("notify::fullscreened", self._on_fullscreen_changed)
        self.rebuild_folders()
        self.update_actions()

    # ---------------------------------------------------------------- UI ---

    def _build_ui(self):
        self.store = Gio.ListStore(item_type=ImageItem)
        self.selection = Gtk.MultiSelection(model=self.store)
        self.selection.connect("selection-changed", self._on_selection_changed)

        # Sidebar: list of images in the folder
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._row_setup)
        factory.connect("bind", self._row_bind)
        factory.connect("unbind", self._row_unbind)
        # No rubberband: dragging a row starts drag-and-drop onto a folder instead.
        self.listview = Gtk.ListView(model=self.selection, factory=factory)
        self.listview.add_css_class("navigation-sidebar")
        list_scroller = Gtk.ScrolledWindow(child=self.listview, vexpand=True,
                                           hscrollbar_policy=Gtk.PolicyType.NEVER)

        # Sidebar: pinned folders, each expandable into its subfolders
        self.folder_roots = Gio.ListStore(item_type=FolderNode)
        self.folder_tree = Gtk.TreeListModel.new(self.folder_roots, False, False, self._folder_children)
        self.folder_selection = Gtk.SingleSelection(model=self.folder_tree, autoselect=False, can_unselect=True)
        self.folder_selection.connect("selection-changed", self._on_folder_selected)
        self.folder_updating = False
        folder_factory = Gtk.SignalListItemFactory()
        folder_factory.connect("setup", self._folder_row_setup)
        folder_factory.connect("bind", self._folder_row_bind)
        folder_factory.connect("unbind", lambda f, li: setattr(li.get_child().get_child(), "node", None))
        self.folder_view = Gtk.ListView(model=self.folder_selection, factory=folder_factory)
        self.folder_view.add_css_class("navigation-sidebar")

        placeholder = Gtk.Label(label="Click + to pin folders here, then drag images onto them to organise",
                                wrap=True, justify=Gtk.Justification.CENTER, valign=Gtk.Align.CENTER,
                                margin_start=18, margin_end=18, css_classes=["dim-label", "caption"])
        self.folder_stack = Gtk.Stack(vexpand=True)
        self.folder_stack.add_named(Gtk.ScrolledWindow(child=self.folder_view,
                                                       hscrollbar_policy=Gtk.PolicyType.NEVER), "list")
        self.folder_stack.add_named(placeholder, "empty")

        folder_header = Gtk.Box(margin_start=12, margin_end=6, margin_top=6)
        folder_header.append(Gtk.Label(label="Folders", xalign=0, hexpand=True, css_classes=["heading"]))
        folder_header.append(Gtk.Button(icon_name="list-add-symbolic", action_name="win.add-folder",
                                        tooltip_text="Pin Folders to Sidebar…", css_classes=["flat"]))
        folders_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, height_request=90)
        folders_box.append(folder_header)
        folders_box.append(self.folder_stack)
        # Folders dropped here from Files get pinned
        pin_drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        pin_drop.connect("drop", lambda t, value, x, y: self.pin_folders(
            [f.get_path() for f in value.get_files() if f.get_path()]) > 0)
        folders_box.add_controller(pin_drop)

        images_header = Gtk.Label(label="Images", xalign=0, margin_start=12, margin_top=6,
                                  css_classes=["heading"])
        images_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, height_request=120)
        images_box.append(images_header)
        images_box.append(list_scroller)

        sidebar_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, start_child=folders_box,
                                  end_child=images_box, position=230,
                                  shrink_start_child=False, shrink_end_child=False)

        self.sidebar_title = Adw.WindowTitle(title=APP_NAME)
        sidebar_header = Adw.HeaderBar(title_widget=self.sidebar_title)
        open_button = Gtk.Button(icon_name="document-open-symbolic", action_name="win.open",
                                 tooltip_text="Open Image… (Ctrl+O)")
        sidebar_header.pack_start(open_button)

        action_bar = Gtk.ActionBar()
        for icon, action, tip in (
                ("edit-copy-symbolic", "win.copy-to", "Copy to Folder… (Ctrl+Shift+C)"),
                ("folder-symbolic", "win.move-to", "Move to Folder… (Ctrl+Shift+M)"),
                ("document-edit-symbolic", "win.rename", "Rename… (F2)"),
                ("user-trash-symbolic", "win.delete", "Delete (Delete)")):
            action_bar.pack_start(Gtk.Button(icon_name=icon, action_name=action, tooltip_text=tip))
        self.selection_label = Gtk.Label(css_classes=["dim-label", "caption"])
        action_bar.pack_end(self.selection_label)

        sidebar = Adw.ToolbarView(content=sidebar_paned)
        sidebar.add_top_bar(sidebar_header)
        sidebar.add_bottom_bar(action_bar)

        # Content: the image
        self.view = ImageView()
        self.view.connect("scale-changed", lambda *_: self._update_zoom_label())
        self.scroller = Gtk.ScrolledWindow(child=self.view)

        welcome = Adw.StatusPage(icon_name="image-x-generic-symbolic", title="Open an Image",
                                 description="Or drag an image or folder onto this window")
        welcome_button = Gtk.Button(label="_Open Image…", use_underline=True, action_name="win.open",
                                    halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        welcome.set_child(welcome_button)
        self.empty_page = Adw.StatusPage(icon_name="folder-symbolic", title="No Images",
                                         description="This folder contains no images")
        self.error_page = Adw.StatusPage(icon_name="image-missing-symbolic", title="Could Not Load Image")

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(welcome, "welcome")
        self.stack.add_named(self.empty_page, "empty")
        self.stack.add_named(self.error_page, "error")
        self.stack.add_named(self.scroller, "image")
        self.stack.add_named(Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True,
                             halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, width_request=32,
                             height_request=32), "loading")

        self.title = Adw.WindowTitle(title=APP_NAME)
        header = Adw.HeaderBar(title_widget=self.title)
        self.sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", active=True,
                                               tooltip_text="Toggle Image List (F9)")
        header.pack_start(self.sidebar_toggle)
        nav = Gtk.Box(css_classes=["linked"])
        nav.append(Gtk.Button(icon_name="go-previous-symbolic", action_name="win.previous",
                              tooltip_text="Previous Image (Left)"))
        nav.append(Gtk.Button(icon_name="go-next-symbolic", action_name="win.next",
                              tooltip_text="Next Image (Right)"))
        header.pack_start(nav)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append("Open Image…", "win.open")
        section.append("Open Folder…", "win.open-folder")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Pin Folders to Sidebar…", "win.add-folder")
        section.append("Pin Current Folder to Sidebar", "win.pin-current")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Open in Editor", "win.edit")
        section.append("Open With…", "win.open-with")
        section.append("Set as Wallpaper", "win.wallpaper")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Fullscreen", "win.fullscreen")
        section.append("Preferences", "app.preferences")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Keyboard Shortcuts", "app.shortcuts")
        section.append("Help", "app.help")
        section.append("About Image2", "app.about")
        menu.append_section(None, section)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True,
                                       tooltip_text="Main Menu"))
        header.pack_end(Gtk.Button(icon_name="document-edit-symbolic", action_name="win.edit",
                                   tooltip_text="Open in External Editor (Ctrl+E)"))

        zoom = Gtk.Box(css_classes=["linked"])
        zoom.append(Gtk.Button(icon_name="zoom-out-symbolic", action_name="win.zoom-out",
                               tooltip_text="Zoom Out (−)"))
        self.zoom_button = Gtk.Button(label="100%", action_name="win.zoom-toggle", width_request=64,
                                      tooltip_text="Toggle Best Fit / Original Size")
        zoom.append(self.zoom_button)
        zoom.append(Gtk.Button(icon_name="zoom-in-symbolic", action_name="win.zoom-in",
                               tooltip_text="Zoom In (+)"))
        header.pack_end(zoom)

        self.content = Adw.ToolbarView(content=self.stack)
        self.content.add_top_bar(header)

        self.split = Adw.OverlaySplitView(sidebar=sidebar, content=self.content,
                                          min_sidebar_width=220, max_sidebar_width=360)
        self.split.bind_property("show-sidebar", self.sidebar_toggle, "active",
                                 GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL)

        self.toasts = Adw.ToastOverlay(child=self.split)
        self.set_content(self.toasts)

        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint)

        # Context menus
        file_menu = Gio.Menu()
        section = Gio.Menu()
        section.append("Open in Editor", "win.edit")
        section.append("Open With…", "win.open-with")
        section.append("Set as Wallpaper", "win.wallpaper")
        file_menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Copy to Folder…", "win.copy-to")
        section.append("Move to Folder…", "win.move-to")
        section.append("Rename…", "win.rename")
        file_menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Delete", "win.delete")
        file_menu.append_section(None, section)

        self.list_menu = Gtk.PopoverMenu(menu_model=file_menu, has_arrow=False, halign=Gtk.Align.START)
        self.list_menu.set_parent(self.listview)
        self.image_menu = Gtk.PopoverMenu(menu_model=file_menu, has_arrow=False, halign=Gtk.Align.START)
        self.image_menu.set_parent(self.scroller)
        self.folder_menu = Gtk.PopoverMenu(has_arrow=False, halign=Gtk.Align.START)
        self.folder_menu.set_parent(self.folder_view)

        self.stack.set_visible_child_name("welcome")

    # ---------------------------------------------------------- list rows ---

    def _row_setup(self, factory, list_item):
        box = Gtk.Box(spacing=10)
        box.thumb = Gtk.Image(pixel_size=THUMB_SIZE)
        box.label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
        box.item = None
        box.append(box.thumb)
        box.append(box.label)
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._on_row_context, list_item, box)
        box.add_controller(click)
        drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        drag.connect("prepare", self._on_row_drag_prepare, list_item)
        drag.connect("drag-begin", lambda source, d: source.set_icon(Gtk.WidgetPaintable(widget=box), 0, 0))
        box.add_controller(drag)
        list_item.set_child(box)

    def _on_row_drag_prepare(self, source, x, y, list_item):
        pos = list_item.get_position()
        if not self.selection.is_selected(pos):
            self.selection.select_item(pos, True)
        files = [Gio.File.new_for_path(p) for p in self.selected_paths()]
        value = GObject.Value(Gdk.FileList, Gdk.FileList.new_from_list(files))
        return Gdk.ContentProvider.new_for_value(value)

    def _row_bind(self, factory, list_item):
        box = list_item.get_child()
        item = list_item.get_item()
        box.item = item
        box.label.set_label(item.display_name)
        box.set_tooltip_text(item.display_name)
        show = self.settings["show_thumbnails"]
        box.thumb.set_visible(show)
        if not show:
            return

        def ready(texture):
            if box.item is item:
                box.thumb.set_from_paintable(texture)

        texture = self.thumbs.get(item, ready)
        if texture:
            box.thumb.set_from_paintable(texture)
        else:
            box.thumb.set_from_icon_name("image-x-generic-symbolic")

    def _row_unbind(self, factory, list_item):
        list_item.get_child().item = None

    def _on_row_context(self, gesture, n_press, x, y, list_item, box):
        pos = list_item.get_position()
        if not self.selection.is_selected(pos):
            self.selection.select_item(pos, True)
        ok, point = box.compute_point(self.listview, Graphene.Point().init(x, y))
        self._popup(self.list_menu, point.x if ok else x, point.y if ok else y)

    # -------------------------------------------------------- folder tree ---

    def _folder_children(self, node):
        children = list_subfolders(node.path, self.settings["show_hidden"])
        if not children:
            return None
        store = Gio.ListStore(item_type=FolderNode)
        store.splice(0, 0, children)
        return store

    def _folder_row_setup(self, factory, list_item):
        expander = Gtk.TreeExpander(css_classes=["folder-row"])
        box = Gtk.Box(spacing=8)
        box.icon = Gtk.Image(icon_name="folder-symbolic")
        box.label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
        box.node = None
        box.append(box.icon)
        box.append(box.label)
        expander.set_child(box)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        drop.connect("enter", lambda t, x, y: self._drop_action(t))
        drop.connect("motion", lambda t, x, y: self._drop_action(t))
        drop.connect("drop", self._on_folder_drop, box)
        expander.add_controller(drop)

        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._on_folder_context, box)
        expander.add_controller(click)
        list_item.set_child(expander)

    def _folder_row_bind(self, factory, list_item):
        row = list_item.get_item()
        node = row.get_item()
        expander = list_item.get_child()
        expander.set_list_row(row)
        box = expander.get_child()
        box.node = node
        box.label.set_label(node.name)
        tooltip = GLib.filename_display_name(node.path)
        if node.is_root:
            exists = os.path.isdir(node.path)
            box.icon.set_from_icon_name("folder-symbolic" if exists else "dialog-warning-symbolic")
            if not exists:
                tooltip += "\nFolder not found"
            index = self.settings["folders"].index(node.path) if node.path in self.settings["folders"] else 9
            if index < 9:
                tooltip += f"\nAlt+{index + 1} moves the selected images here, Ctrl+Alt+{index + 1} copies"
        else:
            box.icon.set_from_icon_name("folder-symbolic")
        box.label.set_attributes(_BOLD if node.is_root else None)
        box.set_tooltip_text(tooltip)

    def _drop_action(self, target):
        """Move by default; copy when the source only offers copy or Ctrl is held."""
        actions = target.get_current_drop().get_actions()
        ctrl = target.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK
        if actions == Gdk.DragAction.COPY or (ctrl and actions & Gdk.DragAction.COPY):
            return Gdk.DragAction.COPY
        return Gdk.DragAction.MOVE

    def _on_folder_drop(self, target, value, x, y, box):
        node = box.node
        if node is None:
            return False
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        images = [p for p in paths if os.path.isfile(p) and is_image(os.path.basename(p))]
        if not images:
            self.toast("Only images can be dropped onto a folder")
            return False
        self._transfer(images, node.path, self._drop_action(target) == Gdk.DragAction.MOVE)
        return True

    def _on_folder_context(self, gesture, n_press, x, y, box):
        node = box.node
        if node is None:
            return
        target = GLib.Variant("s", node.path)

        def section(*entries):
            menu = Gio.Menu()
            for label, action in entries:
                item = Gio.MenuItem.new(label, None)
                item.set_action_and_target_value(action, target)
                menu.append_item(item)
            return menu

        menu = Gio.Menu()
        menu.append_section(None, section(("Open", "win.folder-open"), ("Show in Files", "win.folder-show")))
        menu.append_section(None, section(("Move Selected Images Here", "win.move-here"),
                                          ("Copy Selected Images Here", "win.copy-here")))
        menu.append_section(None, section(("New Folder…", "win.new-folder")))
        if node.is_root:
            menu.append_section(None, section(("Unpin from Sidebar", "win.unpin-folder")))
        else:
            menu.append_section(None, section(("Pin to Sidebar", "win.pin-folder")))
        self.folder_menu.set_menu_model(menu)
        ok, point = box.compute_point(self.folder_view, Graphene.Point().init(x, y))
        self._popup(self.folder_menu, point.x if ok else x, point.y if ok else y)

    def rebuild_folders(self, also_expand=()):
        """Reload the folder tree, keeping expanded folders expanded."""
        expanded = set(also_expand)
        for i in range(self.folder_tree.get_n_items()):
            row = self.folder_tree.get_item(i)
            if row.get_expanded():
                expanded.add(row.get_item().path)
        self.folder_roots.splice(0, self.folder_roots.get_n_items(),
                                 [FolderNode(p, is_root=True) for p in self.settings["folders"]])
        i = 0
        while i < self.folder_tree.get_n_items():  # expanding inserts rows, so loop by index
            row = self.folder_tree.get_item(i)
            if row.get_item().path in expanded:
                row.set_expanded(True)
            i += 1
        self.folder_stack.set_visible_child_name("list" if self.settings["folders"] else "empty")
        self._sync_folder_selection()

    def _sync_folder_selection(self):
        self.folder_updating = True
        try:
            for i in range(self.folder_tree.get_n_items()):
                if self.folder_tree.get_item(i).get_item().path == self.folder:
                    self.folder_selection.set_selected(i)
                    break
            else:
                self.folder_selection.set_selected(Gtk.INVALID_LIST_POSITION)
        finally:
            self.folder_updating = False

    def _on_folder_selected(self, selection, position, n_items):
        if self.folder_updating:
            return
        row = selection.get_selected_item()
        if row is not None and row.get_item().path != self.folder:
            self.open_path(row.get_item().path)

    def pin_folders(self, paths):
        folders = list(self.settings["folders"])
        added = 0
        for path in paths:
            path = os.path.abspath(path)
            if os.path.isdir(path) and path not in folders:
                folders.append(path)
                added += 1
        if added:
            self.settings["folders"] = folders
        return added

    def on_add_folder(self, *_):
        dialog = Gtk.FileDialog(title="Pin Folders to Sidebar", accept_label="_Pin", modal=True)

        def done(dialog, result):
            try:
                files = dialog.select_multiple_folders_finish(result)
            except GLib.Error:
                return
            self.pin_folders([files.get_item(i).get_path() for i in range(files.get_n_items())
                              if files.get_item(i).get_path()])

        dialog.select_multiple_folders(self, None, done)

    def on_unpin_folder(self, action, param):
        path = param.get_string()
        self.settings["folders"] = [p for p in self.settings["folders"] if p != path]

    def on_new_folder(self, action, param):
        parent = param.get_string()

        def create(name):
            try:
                Gio.File.new_for_path(os.path.join(parent, name)).make_directory(None)
            except GLib.Error as e:
                self.toast(f"Could not create folder: {e.message}")
                return
            self.rebuild_folders(also_expand=[parent])

        self._name_dialog("New Folder", parent, "", "_Create", create)

    @staticmethod
    def _popup(popover, x, y):
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    # ------------------------------------------------------------ actions ---

    def _install_actions(self):
        actions = {
            "open": self.on_open,
            "open-folder": self.on_open_folder,
            "previous": lambda *_: self.go(-1),
            "next": lambda *_: self.go(1),
            "first": lambda *_: self.show_index(0),
            "last": lambda *_: self.show_index(self.store.get_n_items() - 1),
            "zoom-in": lambda *_: self.zoom_to(self.view.scale * ZOOM_STEP),
            "zoom-out": lambda *_: self.zoom_to(self.view.scale / ZOOM_STEP),
            "zoom-fit": lambda *_: self.view.set_fit(),
            "zoom-original": lambda *_: self.zoom_to(1.0),
            "zoom-toggle": lambda *_: self.toggle_fit(),
            "edit": self.on_edit,
            "open-with": self.on_open_with,
            "wallpaper": self.on_set_wallpaper,
            "copy-to": lambda *_: self.on_transfer(move=False),
            "move-to": lambda *_: self.on_transfer(move=True),
            "rename": self.on_rename,
            "delete": self.on_delete,
            "select-all": lambda *_: self.selection.select_all(),
            "toggle-sidebar": lambda *_: self.split.set_show_sidebar(not self.split.get_show_sidebar()),
            "fullscreen": lambda *_: self.unfullscreen() if self.is_fullscreen() else self.fullscreen(),
            "add-folder": self.on_add_folder,
            "pin-current": lambda *_: self.pin_folders([self.folder]) or self.toast("Folder is already pinned"),
        }
        # Folder context-menu actions take the folder path as their parameter
        folder_actions = {
            "folder-open": lambda a, p: self.open_path(p.get_string()),
            "folder-show": lambda a, p: Gtk.FileLauncher(
                file=Gio.File.new_for_path(p.get_string())).launch(self, None, None),
            "move-here": lambda a, p: self._transfer(self.targets(), p.get_string(), True),
            "copy-here": lambda a, p: self._transfer(self.targets(), p.get_string(), False),
            "new-folder": self.on_new_folder,
            "pin-folder": lambda a, p: self.pin_folders([p.get_string()]),
            "unpin-folder": self.on_unpin_folder,
        }
        self.actions = {}
        for table, param_type in ((actions, None), (folder_actions, GLib.VariantType.new("s"))):
            for name, callback in table.items():
                action = Gio.SimpleAction.new(name, param_type)
                action.connect("activate", callback)
                self.add_action(action)
                self.actions[name] = action

    def _install_input(self):
        # Plain keys are handled here, in the capture phase, so they work whatever
        # has focus — except while typing in a text field or when a dialog is open.
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)

        # Ctrl+scroll zooms around the pointer
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda c, x, y: setattr(self, "pointer", (x, y)))
        motion.connect("leave", lambda c: setattr(self, "pointer", None))
        self.scroller.add_controller(motion)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL,
                                           propagation_phase=Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self._on_scroll)
        self.scroller.add_controller(scroll)

        # Pinch to zoom
        pinch = Gtk.GestureZoom()
        pinch.connect("begin", lambda g, s: setattr(self, "pinch_start", self.view.scale))
        pinch.connect("scale-changed", self._on_pinch)
        self.scroller.add_controller(pinch)

        # Drag to pan
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", lambda *_: self.scroller.set_cursor(None))
        self.scroller.add_controller(drag)

        # Double click toggles fit / 100 %, right click opens the menu
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_image_click)
        self.scroller.add_controller(click)
        context = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        context.connect("pressed", lambda g, n, x, y: self.current_path and self._popup(self.image_menu, x, y))
        self.scroller.add_controller(context)

        # Drag and drop files from Files
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.content.add_controller(drop)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if self.get_visible_dialog() is not None or isinstance(self.get_focus(), Gtk.Editable):
            return False
        mods = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.ALT_MASK)
        if mods == Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_a, Gdk.KEY_A):
            self.selection.select_all()
            return True
        # Alt+1…9 moves the selection to a pinned folder, Ctrl+Alt+1…9 copies it
        if mods & Gdk.ModifierType.ALT_MASK and Gdk.KEY_1 <= keyval <= Gdk.KEY_9:
            folders = self.settings["folders"]
            index = keyval - Gdk.KEY_1
            if index < len(folders) and self.targets():
                self._transfer(self.targets(), folders[index], not mods & Gdk.ModifierType.CONTROL_MASK)
            return True
        if mods & ~Gdk.ModifierType.SHIFT_MASK:
            return False
        name = Gdk.keyval_name(keyval)
        plain = {
            "Left": "previous", "Page_Up": "previous", "BackSpace": "previous",
            "Right": "next", "Page_Down": "next", "space": "next",
            "Home": "first", "End": "last",
            "Delete": "delete", "KP_Delete": "delete",
            "0": "zoom-fit", "KP_0": "zoom-fit",
            "1": "zoom-original", "KP_1": "zoom-original",
        }
        if name in ("plus", "equal", "KP_Add"):
            action = "zoom-in"
        elif name in ("minus", "underscore", "KP_Subtract"):
            action = "zoom-out"
        elif name == "Escape" and self.is_fullscreen():
            self.unfullscreen()
            return True
        elif mods or name not in plain:
            return False
        else:
            action = plain[name]
        if self.actions[action].get_enabled():
            self.actions[action].activate(None)
        return True

    def _on_scroll(self, controller, dx, dy):
        if not controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            return False
        if self.view.texture is not None and dy:
            self.zoom_to(self.view.scale * ZOOM_STEP ** (-dy), self.pointer)
        return True

    def _on_pinch(self, gesture, scale):
        ok, x, y = gesture.get_bounding_box_center()
        self.zoom_to(getattr(self, "pinch_start", self.view.scale) * scale, (x, y) if ok else None)

    def _on_drag_begin(self, gesture, x, y):
        if self.view.fit or self.view.texture is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        self.drag_start = (self.scroller.get_hadjustment().get_value(),
                           self.scroller.get_vadjustment().get_value())
        self.scroller.set_cursor(Gdk.Cursor.new_from_name("grabbing"))

    def _on_drag_update(self, gesture, dx, dy):
        self.scroller.get_hadjustment().set_value(self.drag_start[0] - dx)
        self.scroller.get_vadjustment().set_value(self.drag_start[1] - dy)

    def _on_image_click(self, gesture, n_press, x, y):
        if n_press == 2 and self.view.texture is not None:
            self.toggle_fit((x, y))

    def _on_drop(self, target, value, x, y):
        files = value.get_files()
        if files and files[0].get_path():
            self.open_path(files[0].get_path())
            return True
        return False

    # ------------------------------------------------------------ opening ---

    def on_open(self, *_):
        dialog = Gtk.FileDialog(title="Open Image", modal=True)
        image_filter = Gtk.FileFilter(name="Images")
        image_filter.add_pixbuf_formats()
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(image_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(image_filter)
        if self.folder:
            dialog.set_initial_folder(Gio.File.new_for_path(self.folder))

        def done(dialog, result):
            try:
                file = dialog.open_finish(result)
            except GLib.Error:
                return
            if file.get_path():
                self.open_path(file.get_path())

        dialog.open(self, None, done)

    def on_open_folder(self, *_):
        dialog = Gtk.FileDialog(title="Open Folder", modal=True)
        if self.folder:
            dialog.set_initial_folder(Gio.File.new_for_path(self.folder))

        def done(dialog, result):
            try:
                file = dialog.select_folder_finish(result)
            except GLib.Error:
                return
            if file.get_path():
                self.open_path(file.get_path())

        dialog.select_folder(self, None, done)

    def open_path(self, path):
        path = os.path.abspath(path)
        if os.path.isdir(path):
            folder, target = path, None
        elif os.path.isfile(path):
            folder, target = os.path.dirname(path), path
        else:
            self.toast(f"“{GLib.filename_display_name(path)}” no longer exists")
            self._sync_folder_selection()
            return
        if folder != self.folder:
            self.folder = folder
            self.current_path = None
            self.path_index = {}
            self._watch_folder()
            self.settings["last_folder"] = folder
        self.rescan(select_path=target)
        self._sync_folder_selection()

    def _watch_folder(self):
        if self.monitor:
            self.monitor.cancel()
        try:
            self.monitor = Gio.File.new_for_path(self.folder).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
            self.monitor.connect("changed", self._on_folder_changed)
        except GLib.Error:
            self.monitor = None

    def _on_folder_changed(self, *_):
        if self.rescan_source:
            GLib.source_remove(self.rescan_source)

        def fire():
            self.rescan_source = 0
            self.rescan()
            return False

        self.rescan_source = GLib.timeout_add(300, fire)

    def rescan(self, select_path=None):
        if not self.folder:
            return
        keep = [] if select_path else self.selected_paths()
        old_index = self.path_index.get(self.current_path, 0)
        items = scan_folder(self.folder, self.settings["sort_by"], self.settings["show_hidden"])

        self.updating = True
        try:
            self.store.splice(0, self.store.get_n_items(), items)
        finally:
            self.updating = False
        self.path_index = {item.path: i for i, item in enumerate(items)}

        self.sidebar_title.set_title(GLib.filename_display_basename(self.folder))
        self.sidebar_title.set_subtitle(plural(len(items), "image"))

        if not items:
            self.current_path = None
            self.view.set_texture(None)
            self.stack.set_visible_child_name("empty")
            self.update_title()
            self.update_actions()
            return

        if select_path in self.path_index:
            target = select_path
        elif self.current_path in self.path_index:
            target = self.current_path
        else:
            target = items[min(old_index, len(items) - 1)].path

        keep = [p for p in keep if p in self.path_index]
        if len(keep) > 1 and target in keep:
            for p in keep:
                self.selection.select_item(self.path_index[p], False)
        else:
            self.selection.select_item(self.path_index[target], True)
        self.listview.scroll_to(self.path_index[target], Gtk.ListScrollFlags.NONE, None)
        self.display(target)
        self.update_actions()

    # ---------------------------------------------------------- selection ---

    def selected_paths(self):
        return [self.store.get_item(i).path for i in range(self.store.get_n_items())
                if self.selection.is_selected(i)]

    def targets(self):
        """Images a file operation applies to: the selection, else the shown image."""
        selected = self.selected_paths()
        if selected:
            return selected
        return [self.current_path] if self.current_path else []

    def _on_selection_changed(self, selection, position, n_items):
        if self.updating:
            return
        selected = self.selected_paths()
        if len(selected) == 1:
            self.display(selected[0])
        self.update_actions()

    def show_index(self, index):
        n = self.store.get_n_items()
        if n == 0:
            return
        index = max(0, min(index, n - 1))
        self.listview.scroll_to(index, Gtk.ListScrollFlags.SELECT | Gtk.ListScrollFlags.FOCUS, None)

    def go(self, delta):
        index = self.path_index.get(self.current_path)
        self.show_index(0 if index is None else index + delta)

    # ------------------------------------------------------------ display ---

    def display(self, path):
        index = self.path_index.get(path)
        if index is None:
            return
        item = self.store.get_item(index)
        same = path == self.current_path
        if same and item.mtime == self.current_mtime:
            self.update_title()
            return
        self.current_path = path
        self.current_mtime = item.mtime
        self.load_generation += 1
        generation = self.load_generation
        if not same:
            self.view.set_texture(None)
            self.stack.set_visible_child_name("loading")
        self.update_title()

        def done(pixbuf, error):
            if generation != self.load_generation:
                return
            if error is not None:
                self.view.set_texture(None)
                message = error.message if isinstance(error, GLib.Error) else str(error)
                self.error_page.set_description(GLib.markup_escape_text(message))
                self.stack.set_visible_child_name("error")
            else:
                self.view.set_texture(Gdk.Texture.new_for_pixbuf(pixbuf), keep_zoom=same)
                self.stack.set_visible_child_name("image")
            self.update_title()
            self.update_actions()

        run_in_thread(lambda: load_pixbuf(path), done)

    def update_title(self):
        index = self.path_index.get(self.current_path)
        if index is None:
            self.title.set_title(APP_NAME)
            self.title.set_subtitle("")
            self.set_title(APP_NAME)
            return
        item = self.store.get_item(index)
        parts = [f"{index + 1} / {self.store.get_n_items()}"]
        if self.view.texture is not None:
            parts.append(f"{self.view.texture.get_width()} × {self.view.texture.get_height()}")
        parts.append(GLib.format_size(item.size))
        self.title.set_title(item.display_name)
        self.title.set_subtitle("  ·  ".join(parts))
        self.set_title(f"{item.display_name} — {APP_NAME}")

    def update_actions(self):
        has_image = self.view.texture is not None
        index = self.path_index.get(self.current_path)
        n = self.store.get_n_items()
        targets = self.targets()
        selected = len(self.selected_paths())
        enabled = {
            "previous": index is not None and index > 0,
            "next": index is not None and index < n - 1,
            "first": n > 0,
            "last": n > 0,
            "zoom-in": has_image,
            "zoom-out": has_image,
            "zoom-fit": has_image,
            "zoom-original": has_image,
            "zoom-toggle": has_image,
            "edit": self.current_path is not None,
            "open-with": self.current_path is not None,
            "wallpaper": has_image,
            "copy-to": bool(targets),
            "move-to": bool(targets),
            "delete": bool(targets),
            "rename": len(targets) == 1,
            "select-all": n > 0,
            "move-here": bool(targets),
            "copy-here": bool(targets),
            "pin-current": self.folder is not None,
        }
        for name, value in enabled.items():
            self.actions[name].set_enabled(value)
        self.selection_label.set_label(f"{selected} selected" if selected > 1 else "")

    # --------------------------------------------------------------- zoom ---

    def _update_zoom_label(self):
        if self.view.texture is None:
            self.zoom_button.set_label("100%")
        else:
            self.zoom_button.set_label(f"{round(self.view.scale * 100)}%")
        self.update_title()

    def toggle_fit(self, anchor=None):
        if self.view.fit and abs(self.view.fit_scale - 1.0) > 1e-6:
            self.zoom_to(1.0, anchor)
        elif self.view.fit:
            self.zoom_to(2.0, anchor)  # image already fits at 100 %: zoom in instead
        else:
            self.view.set_fit()

    def zoom_to(self, zoom, anchor=None):
        view = self.view
        if view.texture is None:
            return
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, zoom))
        old = view.scale
        hadj = self.scroller.get_hadjustment()
        vadj = self.scroller.get_vadjustment()
        if anchor is None:
            anchor = (hadj.get_page_size() / 2, vadj.get_page_size() / 2)

        # Keep the image point under the anchor fixed. Pre-configuring the
        # adjustments works because the viewport keeps in-range values on relayout.
        def adjust(adj, size, a):
            page = adj.get_page_size()
            offset_old = max(0.0, (page - size * old) / 2)
            point = (adj.get_value() + a - offset_old) / old
            new_size = size * zoom
            offset_new = max(0.0, (page - new_size) / 2)
            upper = max(page, math.ceil(new_size))
            value = max(0.0, min(point * zoom + offset_new - a, upper - page))
            adj.configure(value, 0, upper, adj.get_step_increment(), adj.get_page_increment(), page)

        adjust(hadj, view.texture.get_width(), anchor[0])
        adjust(vadj, view.texture.get_height(), anchor[1])
        view.set_zoom(zoom)

    # ------------------------------------------------------ external apps ---

    def on_edit(self, *_):
        path = self.current_path
        if not path:
            return
        mode = self.settings["editor_mode"]
        try:
            if mode == "app" and (app := find_app(self.settings["editor_app"])):
                app.launch([Gio.File.new_for_path(path)], self.get_display().get_app_launch_context())
                name = app.get_display_name()
            elif mode == "custom" and self.settings["editor_command"].strip():
                argv = GLib.shell_parse_argv(self.settings["editor_command"])[1]
                if any("%f" in arg for arg in argv):
                    argv = [arg.replace("%f", path) for arg in argv]
                else:
                    argv.append(path)
                Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE)
                name = os.path.basename(argv[0])
            else:
                toast = Adw.Toast(title="No external editor configured", button_label="Preferences",
                                  action_name="app.preferences")
                self.toasts.add_toast(toast)
                return
        except GLib.Error as e:
            self.toast(f"Could not start editor: {e.message}")
            return
        self.toast(f"Opened in {name}")

    def on_open_with(self, *_):
        if not self.current_path:
            return
        launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(self.current_path), always_ask=True)

        def done(launcher, result):
            try:
                launcher.launch_finish(result)
            except GLib.Error:
                pass

        launcher.launch(self, None, done)

    # ---------------------------------------------------- file operations ---

    def on_set_wallpaper(self, *_):
        path = self.current_path
        if not path:
            return
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup(WALLPAPER_SCHEMA, True) if source else None
        if schema is None:
            self.toast("Setting the wallpaper isn't supported on this desktop")
            return

        # Use a private copy, like GNOME Settings does, so the wallpaper survives
        # the original being moved, renamed or deleted while organising.
        def work():
            folder = os.path.join(GLib.get_user_data_dir(), "backgrounds")
            os.makedirs(folder, exist_ok=True)
            dest = os.path.join(folder, os.path.basename(path))
            if os.path.exists(dest) and filecmp.cmp(path, dest, shallow=False):
                return dest
            dest = unique_path(folder, os.path.basename(path))
            Gio.File.new_for_path(path).copy(Gio.File.new_for_path(dest), Gio.FileCopyFlags.NONE,
                                             None, None, None)
            return dest

        def done(dest, error):
            if error is not None:
                message = error.message if isinstance(error, GLib.Error) else str(error)
                self.toast(f"Could not set wallpaper: {message}")
                return
            background = Gio.Settings.new(WALLPAPER_SCHEMA)
            uri = Gio.File.new_for_path(dest).get_uri()
            background.set_string("picture-uri", uri)
            if schema.has_key("picture-uri-dark"):
                background.set_string("picture-uri-dark", uri)
            background.set_string("picture-options", "zoom")
            Gio.Settings.sync()
            self.toast("Set as wallpaper")

        run_in_thread(work, done)

    def toast(self, title, timeout=3):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(title), timeout=timeout))

    def on_transfer(self, move):
        paths = self.targets()
        if not paths:
            return
        verb = "Move" if move else "Copy"
        dialog = Gtk.FileDialog(title=f"{verb} {plural(len(paths), 'Image')} to Folder", modal=True,
                                accept_label=f"_{verb} Here")
        last = self.settings["last_target_folder"]
        if last and os.path.isdir(last):
            dialog.set_initial_folder(Gio.File.new_for_path(last))
        elif self.folder:
            dialog.set_initial_folder(Gio.File.new_for_path(self.folder))

        def chosen(dialog, result):
            try:
                folder = dialog.select_folder_finish(result)
            except GLib.Error:
                return
            dest_dir = folder.get_path()
            if not dest_dir:
                self.toast("Please choose a local folder")
                return
            self.settings["last_target_folder"] = dest_dir
            self._transfer(paths, dest_dir, move)

        dialog.select_folder(self, None, chosen)

    def _transfer(self, paths, dest_dir, move):
        if not paths:
            return
        real_dest = os.path.realpath(dest_dir)

        def work():
            moved, skipped, renamed, errors = [], 0, 0, []
            for path in paths:
                if move and os.path.realpath(os.path.dirname(path)) == real_dest:
                    skipped += 1
                    continue
                name = os.path.basename(path)
                dest = unique_path(dest_dir, name)
                src, dst = Gio.File.new_for_path(path), Gio.File.new_for_path(dest)
                try:
                    if move:
                        src.move(dst, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS, None, None, None)
                    else:
                        src.copy(dst, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS, None, None, None)
                    moved.append((path, dest))
                    renamed += os.path.basename(dest) != name
                except GLib.Error as e:
                    errors.append(f"{GLib.filename_display_name(name)}: {e.message}")
            return moved, skipped, renamed, errors

        def finished(result, error):
            if error is not None:
                self.toast(f"Failed: {error}")
                return
            moved, skipped, renamed, errors = result
            if not moved and skipped and not errors:
                self.toast("The images are already in that folder")
                return
            folder_name = GLib.filename_display_basename(dest_dir)
            message = f"{'Moved' if move else 'Copied'} {plural(len(moved), 'image')} to “{folder_name}”"
            if renamed:
                message += f" ({renamed} renamed to avoid overwriting)"
            if errors:
                message += f" — {len(errors)} failed: {errors[0]}"
            toast = Adw.Toast(title=GLib.markup_escape_text(message), timeout=5,
                              button_label="Undo" if move and moved else "Show Folder")
            if move and moved:
                toast.connect("button-clicked", lambda *_: self._undo_move(moved))
            else:
                toast.connect("button-clicked", lambda *_: Gtk.FileLauncher(
                    file=Gio.File.new_for_path(dest_dir)).launch(self, None, None))
            self.toasts.add_toast(toast)
            if move:
                self.rescan()

        run_in_thread(work, finished)

    def _undo_move(self, moved):
        def work():
            restored = 0
            for original, dest in moved:
                if os.path.lexists(original):
                    continue  # never overwrite something created since
                try:
                    Gio.File.new_for_path(dest).move(Gio.File.new_for_path(original),
                                                     Gio.FileCopyFlags.NOFOLLOW_SYMLINKS, None, None, None)
                    restored += 1
                except GLib.Error:
                    pass
            return restored

        def finished(restored, error):
            self.toast(f"Moved {plural(restored or 0, 'image')} back")
            self.rescan(select_path=moved[0][0] if restored else None)

        run_in_thread(work, finished)

    def on_rename(self, *_):
        targets = self.targets()
        if len(targets) != 1:
            return
        path = targets[0]
        folder = os.path.dirname(path)

        def rename(new_name):
            new_path = os.path.join(folder, new_name)
            try:
                Gio.File.new_for_path(path).move(Gio.File.new_for_path(new_path),
                                                 Gio.FileCopyFlags.NONE, None, None, None)
            except GLib.Error as e:
                self.toast(f"Could not rename: {e.message}")
                return
            self.rescan(select_path=new_path)

        self._name_dialog("Rename Image", folder, os.path.basename(path), "_Rename", rename)

    def _name_dialog(self, heading, folder, old_name, accept_label, on_accept):
        """Ask for a new file name in folder; on_accept(name) runs for a valid, unused name."""
        dialog = Adw.AlertDialog(heading=heading)
        entry = Gtk.Entry(text=GLib.filename_display_name(old_name), activates_default=True)
        hint = Gtk.Label(css_classes=["caption", "error"], visible=False, wrap=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(entry)
        box.append(hint)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "_Cancel")
        dialog.add_response("accept", accept_label)
        dialog.set_response_appearance("accept", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("accept")
        dialog.set_close_response("cancel")

        def validate(*_):
            name = entry.get_text().strip()
            problem = None
            if not name or name in (".", ".."):
                problem = ""
            elif "/" in name:
                problem = "Names can't contain “/”"
            elif name != old_name and os.path.lexists(os.path.join(folder, name)):
                problem = "Something with that name already exists here"
            hint.set_label(problem or "")
            hint.set_visible(bool(problem))
            if problem is None:
                entry.remove_css_class("error")
            else:
                entry.add_css_class("error")
            dialog.set_response_enabled("accept", problem is None and name != old_name)

        entry.connect("changed", validate)
        validate()
        dialog.connect("response", lambda d, r: r == "accept" and on_accept(entry.get_text().strip()))
        dialog.present(self)

        def select_stem():
            entry.grab_focus()
            stem = os.path.splitext(entry.get_text())[0]
            entry.select_region(0, len(stem))
            return False

        GLib.idle_add(select_stem)

    def on_delete(self, *_):
        paths = self.targets()
        if not paths:
            return
        use_trash = self.settings["use_trash"]
        if not use_trash or self.settings["confirm_delete"]:
            what = (f"“{GLib.filename_display_basename(paths[0])}”" if len(paths) == 1
                    else plural(len(paths), "image"))
            if use_trash:
                dialog = Adw.AlertDialog(heading="Move to Trash?", body=f"{what} will be moved to the trash.")
                label = "_Move to Trash"
            else:
                dialog = Adw.AlertDialog(heading="Permanently Delete?",
                                         body=f"{what} will be deleted permanently. This can't be undone.")
                label = "_Delete"
            dialog.add_response("cancel", "_Cancel")
            dialog.add_response("delete", label)
            dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")
            dialog.connect("response", lambda d, r: r == "delete" and self._delete(paths, use_trash))
            dialog.present(self)
        else:
            self._delete(paths, use_trash)

    def _delete(self, paths, use_trash):
        def work():
            errors = []
            for path in paths:
                file = Gio.File.new_for_path(path)
                try:
                    if use_trash:
                        file.trash(None)
                    else:
                        file.delete(None)
                except GLib.Error as e:
                    errors.append(e)
            return errors

        def finished(errors, error):
            removed = len(paths) - len(errors or [])
            verb = "Moved" if use_trash else "Deleted"
            message = f"{verb} {plural(removed, 'image')}" + (" to the trash" if use_trash else "")
            if error is not None:
                message = f"Delete failed: {error}"
            elif errors:
                message += f" — {len(errors)} failed: {errors[0].message}"
                if use_trash and errors[0].matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_SUPPORTED):
                    message += " (turn off “Move to Trash” in Preferences to delete permanently)"
            self.toast(message, timeout=5)
            self.rescan()

        run_in_thread(work, finished)

    # -------------------------------------------------------------- misc ---

    def _on_settings_changed(self, settings, key):
        if key in ("sort_by", "show_hidden", "show_thumbnails"):
            self.rescan()
        if key in ("folders", "show_hidden"):
            self.rebuild_folders()

    def _on_fullscreen_changed(self, *_):
        fullscreen = self.is_fullscreen()
        self.content.set_reveal_top_bars(not fullscreen)
        if fullscreen:
            self.sidebar_before_fullscreen = self.split.get_show_sidebar()
            self.split.set_show_sidebar(False)
        else:
            self.split.set_show_sidebar(self.sidebar_before_fullscreen)

    def _on_close(self, *_):
        if self.monitor:
            self.monitor.cancel()
        self.settings.disconnect(self.settings_handler)
        return False


# ----------------------------------------------------------------------------
# Application
# ----------------------------------------------------------------------------

class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.settings = None
        self.thumbs = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)
        self.settings = Settings()
        self.thumbs = ThumbnailCache()
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        for name, callback in (("quit", lambda *_: self.quit()),
                               ("about", self.on_about),
                               ("help", self.on_help),
                               ("shortcuts", self.on_shortcuts),
                               ("preferences", self.on_preferences)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        accels = {
            "app.quit": ["<Control>q"],
            "app.preferences": ["<Control>comma"],
            "app.help": ["F1"],
            "app.shortcuts": ["<Control>question"],
            "window.close": ["<Control>w"],
            "win.open": ["<Control>o"],
            "win.open-folder": ["<Control><Shift>o"],
            "win.zoom-in": ["<Control>plus", "<Control>equal", "<Control>KP_Add"],
            "win.zoom-out": ["<Control>minus", "<Control>KP_Subtract"],
            "win.zoom-fit": ["<Control>0", "<Control>KP_0"],
            "win.zoom-original": ["<Control>1", "<Control>KP_1"],
            "win.edit": ["<Control>e"],
            "win.copy-to": ["<Control><Shift>c"],
            "win.move-to": ["<Control><Shift>m"],
            "win.rename": ["F2"],
            "win.toggle-sidebar": ["F9"],
            "win.fullscreen": ["F11"],
        }
        for action, keys in accels.items():
            self.set_accels_for_action(action, keys)

    def do_activate(self):
        window = self.get_active_window()
        if window is None:
            window = ImageWindow(self)
            last = self.settings["last_folder"]
            if last and os.path.isdir(last):
                window.open_path(last)
        window.present()

    def do_open(self, files, n_files, hint):
        window = ImageWindow(self)
        path = files[0].get_path() if files else None
        if path:
            window.open_path(path)
        window.present()

    def do_shutdown(self):
        if self.thumbs:
            self.thumbs.shutdown()
        Adw.Application.do_shutdown(self)

    def on_preferences(self, *_):
        PreferencesDialog(self.settings).present(self.get_active_window())

    def on_help(self, *_):
        HelpDialog().present(self.get_active_window())

    def on_shortcuts(self, *_):
        # Built on demand: GTK 4.14 logs harmless label warnings whenever one is created.
        window = build_shortcuts_window()
        window.set_transient_for(self.get_active_window())
        window.present()

    def on_about(self, *_):
        about = Adw.AboutDialog(application_name=APP_NAME, application_icon="image-x-generic",
                                version=VERSION, comments="A simple image viewer for GNOME",
                                license_type=Gtk.License.GPL_3_0)
        about.present(self.get_active_window())


def main():
    return Application().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
