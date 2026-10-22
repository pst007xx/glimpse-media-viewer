#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Salvatore Turone
"""Glimpse Image Viewer — view and organise images, videos and music, built with GTK 4 and libadwaita."""

import filecmp
import json
import math
import os
import sqlite3
import sys
import threading
import time
import urllib.parse
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

try:
    gi.require_version("GExiv2", "0.10")
    from gi.repository import GExiv2
except (ValueError, ImportError):
    GExiv2 = None  # Properties then shows file details only, without camera info or tags
else:
    GExiv2.initialize()

try:
    gi.require_version("Gst", "1.0")
    gi.require_version("GstPbutils", "1.0")
    from gi.repository import Gst, GstPbutils
except (ValueError, ImportError):
    Gst = None  # videos and music still play (GTK brings its own GStreamer) but get no thumbnails or tags
else:
    Gst.init(None)

# exiv2's XMP support isn't safe to use from several threads at once
METADATA_LOCK = threading.Lock()

APP_ID = "io.github.pst007xx.Glimpse"
APP_NAME = "Glimpse Image Viewer"
VERSION = "1.0"

# In the Snap, GTK portals are off (GDK_DEBUG=no-portals in snapcraft.yaml) so file
# dialogs and drag-and-drop give real paths; host apps are reached via portal_open().
IN_SNAP = "SNAP" in os.environ

THUMB_SIZE = 40
SIDEBAR_MIN = 220
CONTENT_MIN = 360     # the sidebar never widens past what leaves this much for the image
TREE_INDENT = 20      # a folder row's indent per level (measured with Adwaita)
TREE_ROW_EXTRA = 76   # a folder row's arrow, icon, spacing and padding around the name (72 measured)
ZOOM_STEP = 1.25
ZOOM_MIN = 0.02
ZOOM_MAX = 32.0

_BOLD = Pango.AttrList()
_BOLD.insert(Pango.attr_weight_new(Pango.Weight.BOLD))

WALLPAPER_SCHEMA = "org.gnome.desktop.background"
# GNOME "picture-options" value → menu label
WALLPAPER_STYLES = {
    "centered": "Original Size",
    "zoom": "Zoom to Fill",
    "stretched": "Stretch to Fill",
}


def wallpaper_menu():
    menu = Gio.Menu()
    for style, label in WALLPAPER_STYLES.items():
        item = Gio.MenuItem.new(label, None)
        item.set_action_and_target_value("win.wallpaper", GLib.Variant("s", style))
        menu.append_item(item)
    return menu

# (group, [(description, accelerators)]) for the Keyboard Shortcuts window
SHORTCUTS = [
    ("General", [
        ("Open file", "<Control>o"),
        ("Open folder", "<Control><Shift>o"),
        ("Preferences", "<Control>comma"),
        ("Keyboard shortcuts", "<Control>question"),
        ("Help", "F1"),
        ("Close window", "<Control>w"),
        ("Quit", "<Control>q"),
    ]),
    ("Navigation", [
        ("Next file", "Right Page_Down space"),
        ("Play or pause a video or song", "space"),
        ("Previous file", "Left Page_Up BackSpace"),
        ("First / last file", "Home End"),
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
        ("Select all files", "<Control>a"),
        ("Copy to folder", "<Control><Shift>c"),
        ("Move to folder", "<Control><Shift>m"),
        ("Move to pinned folder 1–9", "<Alt>1...<Alt>9"),
        ("Copy to pinned folder 1–9", "<Control><Alt>1...<Control><Alt>9"),
        ("Rename", "F2"),
        ("Delete", "Delete"),
        ("Properties and tags", "<Alt>Return"),
        ("Find images by tag", "<Control>f"),
    ]),
]

# (page title, icon, [(group title, [(row title, row text)])]) for the Help dialog
HELP = [
    ("Viewing", "image-x-generic-symbolic", [
        ("Opening Files", [
            ("Open a file or folder",
             "Use the open button (Ctrl+O), Open Folder… in the main menu (Ctrl+Shift+O), drag a file "
             "or folder onto the window, or choose Glimpse Image Viewer under Open With… in Files."),
            ("Browse the folder",
             "Every image, video and music file in the opened file's folder is listed in the sidebar. Use the "
             "arrow buttons, Left/Right, Page Up/Down or Space/Backspace to step through them, Home/End for "
             "first/last."),
            ("Image details",
             "The title bar shows the file name, its position in the folder, its size in pixels and on disk."),
        ]),
        ("Videos", [
            ("Play videos",
             "Videos are listed with the images and start playing when you select them. Use the controls "
             "at the bottom of the video to pause, seek and change the volume, or press Space to play and pause."),
            ("Fullscreen video", "Double-click a video to watch it fullscreen."),
        ]),
        ("Music", [
            ("Play music",
             "Music files are listed with the images and videos, and start playing when you select them. "
             "The player shows the song's title, artist, album and cover art, with controls to pause, seek "
             "and change the volume. Space plays and pauses."),
            ("Play a whole album",
             "When a song ends, the next music file in the list plays, so a folder of songs plays straight "
             "through. Step to another file at any time to stop."),
            ("Cover art",
             "Glimpse shows the picture saved in the song, or else a cover.jpg, folder.jpg, front.jpg or "
             "Windows Media Player AlbumArt picture in the same folder. The cover also appears as the "
             "song's thumbnail."),
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
            ("Sidebar",
             "Show or hide the sidebar with its button or F9. It collapses automatically in narrow windows."),
            ("Sidebar width",
             "Drag the sidebar's right edge to make it wider or narrower. When you open folders inside "
             "folders, it widens by itself to show their full names, and goes back when you close them."),
            ("Fullscreen", "Press F11 or choose Fullscreen in the main menu. Escape leaves fullscreen."),
        ]),
    ]),
    ("Organising", "folder-symbolic", [
        ("Pinned Folders", [
            ("Pin folders",
             "Use the pin button or + at the top of the sidebar, choose Pin Current Folder to Sidebar in the "
             "main menu, or drag folders from Files onto the Folders list. Your Pictures folder is pinned the "
             "first time Glimpse starts."),
            ("Browse subfolders", "Expand a folder with its arrow; click any folder to show its images."),
            ("Manage pinned folders",
             "Click the pin button at the top of the sidebar, or choose Manage Pinned Folders… in the main "
             "menu or a folder's right-click menu. It opens on your pinned folders: Unpin removes one from "
             "the sidebar, the arrows change their order (Alt+1…9 uses it), and a folder on a disconnected "
             "drive is marked with a warning until the drive is back."),
            ("Add pinned folders",
             "Click Add Folders… (or + above Folders in the sidebar). Start from Home Folder, Drives or "
             "Computer, click a folder to open it and Up to go back, then pin the folder you're in with the "
             "big button, or any folder in the list with its pin button; click a blue pin to unpin. The "
             "Pinned Folders button at the top takes you back to the list of pinned folders."),
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
    ("Other Apps & More", "document-edit-symbolic", [
        ("Other Apps", [
            ("Open With…",
             "Pick any installed app, such as an editor, for the current image. When that app saves, "
             "Glimpse reloads the image automatically."),
            ("Open Containing Folder", "Shows the current image in your file manager."),
        ]),
        ("Properties & Tags", [
            ("See an image's details",
             "Use the properties button or press Alt+Enter for the file's size, dimensions and dates, and "
             "camera details such as exposure, aperture and ISO when the photo has them."),
            ("Tag images",
             "In Properties, type tags and press Enter; separate several with commas. Rename a tag with its "
             "pencil button, or remove it with ×. Tags are saved inside the image as standard keywords, so "
             "other photo apps such as darktable, digiKam and Shotwell can read them."),
            ("Tag several images at once",
             "Select images (Ctrl+click, Shift+click or Ctrl+A), then open Properties. Tags you add, rename or "
             "remove change in every selected image. A tag only some of them have shows how many, and + adds "
             "it to the rest."),
            ("Find by tag in all folders",
             "Press Ctrl+F or use the search button by Folders, tick one or more tags, choose whether images "
             "need any or all of them, then Show Images. The list then holds every matching image from your "
             "pinned folders and their subfolders, with each image's folder under its name. Click a folder, "
             "or the × by Images, to go back. The first search on a big collection indexes it in the "
             "background, which can take a while; after that only new and changed images are read."),
            ("Show and sort by tag",
             "Each image's tags appear under its name. Use the menu next to Images to show only the images "
             "with one tag, or the untagged ones, and to sort by name, date modified or tag."),
        ]),
        ("Wallpaper", [
            ("Set as wallpaper",
             "Choose Set as Wallpaper in the main menu or the right-click menu, then how it should fit: "
             "Original Size shows the image at its actual size, centred; Zoom to Fill enlarges it to cover "
             "the screen, cropping the edges if needed; Stretch to Fill makes it exactly fill the screen, "
             "distorting it if the shape differs. Glimpse copies the image to "
             "~/.local/share/backgrounds and uses it for both the light and dark desktop style, so the "
             "wallpaper keeps working if you later move or delete the original."),
        ]),
        ("Preferences", [
            ("Image list", "Turn thumbnails on or off, show hidden files, and sort by name, date modified or tag."),
            ("Supported formats", "See File Types for every image, video and music format Glimpse opens."),
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
                group = Adw.PreferencesGroup(title=GLib.markup_escape_text(group_title))
                for title, text in rows:
                    row = Adw.ActionRow(title=GLib.markup_escape_text(title), subtitle=GLib.markup_escape_text(text),
                                        subtitle_selectable=True)
                    group.add(row)
                page.add(group)
            self.add(page)
        self.add(file_types_page())


# Friendlier names for some of the pixbuf loaders' own descriptions
FORMAT_NAMES = {
    "The WebP image format": "WebP",
    "MacOS X icon": "macOS Icon",
    "HEIF/AVIF Image": "HEIF and AVIF",
    "PNM/PBM/PGM/PPM": "Netpbm (PNM, PBM, PGM, PPM)",
    "QuickTime": "QuickTime Image",
    "Windows animated cursor": "Windows Animated Cursor",
    "Windows icon": "Windows Icon",
    "Scalable Vector Graphics": "SVG",
}


def file_types_page():
    """Help page listing the formats Glimpse can open: images depend on the installed pixbuf loaders."""
    page = Adw.PreferencesPage(title="File Types", icon_name="text-x-generic-symbolic")

    def group(title, description, rows):
        group = Adw.PreferencesGroup(title=title, description=description)
        for name, extensions in rows:
            group.add(Adw.ActionRow(title=GLib.markup_escape_text(name),
                                    subtitle=", ".join("." + ext for ext in extensions), subtitle_selectable=True))
        page.add(group)

    formats = [(FORMAT_NAMES.get(f.get_description(), f.get_description()), f.get_extensions())
               for f in GdkPixbuf.Pixbuf.get_formats()]
    group("Images", "Tags can be saved in JPEG, PNG, TIFF and WebP images.",
          sorted(formats, key=lambda f: f[0].casefold()))
    group("Videos", "Video: H.264, HEVC (H.265), VP8, VP9, MPEG-4, MPEG-2 and Windows Media. "
          "Sound: AAC, MP3, Opus, Vorbis, FLAC and AC-3. AV1 video isn't supported.", VIDEO_FORMATS)
    group("Music", "Titles, artists, albums and cover art are read from the song's tags.", AUDIO_FORMATS)
    return page


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


VIDEO_FORMATS = [
    ("MPEG-4", ["mp4", "m4v"]),
    ("QuickTime", ["mov"]),
    ("Matroska", ["mkv"]),
    ("WebM", ["webm"]),
    ("AVI", ["avi"]),
    ("Ogg Video", ["ogv"]),
    ("MPEG", ["mpg", "mpeg"]),
    ("Windows Media", ["wmv"]),
    ("3GP", ["3gp"]),
    ("Flash Video", ["flv"]),
    ("AVCHD", ["mts", "m2ts"]),
]
VIDEO_EXTENSIONS = tuple("." + ext for _, extensions in VIDEO_FORMATS for ext in extensions)
AUDIO_FORMATS = [
    ("MP3", ["mp3"]),
    ("FLAC", ["flac"]),
    ("Ogg Vorbis", ["ogg", "oga"]),
    ("Opus", ["opus"]),
    ("AAC and Apple Lossless", ["m4a", "aac"]),
    ("WAV", ["wav"]),
    ("AIFF", ["aiff", "aif"]),
    ("Windows Media Audio", ["wma"]),
    ("WavPack", ["wv"]),
    ("Matroska Audio", ["mka"]),
]
AUDIO_EXTENSIONS = tuple("." + ext for _, extensions in AUDIO_FORMATS for ext in extensions)


def is_image(name):
    return name.lower().endswith(IMAGE_EXTENSIONS)


def is_video(name):
    return name.lower().endswith(VIDEO_EXTENSIONS)


def is_audio(name):
    return name.lower().endswith(AUDIO_EXTENSIONS)


def is_media(name):
    return is_image(name) or is_video(name) or is_audio(name)


def format_duration(microseconds):
    seconds = round(microseconds / 1_000_000)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02}:{rest % 60:02}" if hours else f"{rest // 60}:{rest % 60:02}"


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
    if is_video(path):
        return video_frame(path, size)
    if is_audio(path):
        cover = song_info(path).get("cover")
        return cover and scale_to_fit(cover, size)
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
    return pixbuf.apply_embedded_orientation() or pixbuf


def video_frame(path, size):
    """A frame from a tenth of the way into a video, scaled to fit size, or None."""
    if Gst is None:
        return None
    try:
        playbin = Gst.ElementFactory.make("playbin")
        sink = Gst.parse_bin_from_description("videoflip video-direction=auto ! videoconvert ! gdkpixbufsink name=frame", True)
    except GLib.Error:
        return None
    if playbin is None:
        return None
    playbin.set_property("uri", GLib.filename_to_uri(path))
    playbin.set_property("video-sink", sink)
    playbin.set_property("flags", 1)  # video only

    def wait():
        message = playbin.get_bus().timed_pop_filtered(
            10 * Gst.SECOND, Gst.MessageType.ASYNC_DONE | Gst.MessageType.ERROR)
        return message is not None and message.type == Gst.MessageType.ASYNC_DONE

    try:
        playbin.set_state(Gst.State.PAUSED)
        if not wait():
            return None
        ok, duration = playbin.query_duration(Gst.Format.TIME)
        if ok and duration > 0 and playbin.seek_simple(
                Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, duration // 10):
            wait()
        pixbuf = sink.get_by_name("frame").get_property("last-pixbuf")
    finally:
        playbin.set_state(Gst.State.NULL)
    return pixbuf and scale_to_fit(pixbuf, size)


def scale_to_fit(pixbuf, size):
    width, height = pixbuf.get_width(), pixbuf.get_height()
    scale = min(size / width, size / height)
    return pixbuf.scale_simple(max(1, round(width * scale)), max(1, round(height * scale)),
                               GdkPixbuf.InterpType.BILINEAR)


COVER_NAMES = ("cover", "folder", "front", "album", "albumartsmall")
COVER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def song_info(path):
    """{"title", "artist", "album", "cover": pixbuf}, as far as a music file's tags have them."""
    info = {}
    if Gst is not None:
        try:
            found = GstPbutils.Discoverer.new(5 * Gst.SECOND).discover_uri(GLib.filename_to_uri(path))
        except GLib.Error:
            found = None
        for stream in found.get_stream_list() if found else []:
            tags = stream.get_tags()
            if tags is None:
                continue
            for key in ("title", "artist", "album"):
                # ID3 tags separate several values with "/", which GStreamer splits apart: put "AC/DC" back
                values = [tags.get_string_index(key, i)[1] for i in range(tags.get_tag_size(key))]
                if any(values) and key not in info:
                    info[key] = "/".join(v for v in values if v)
            for key in ("image", "preview-image"):
                ok, sample = tags.get_sample(key)
                if ok and "cover" not in info:
                    buffer = sample.get_buffer()
                    try:
                        loader = GdkPixbuf.PixbufLoader()
                        loader.write(buffer.extract_dup(0, buffer.get_size()))
                        loader.close()
                        info["cover"] = loader.get_pixbuf()
                    except GLib.Error:
                        pass
    if "cover" not in info:
        info["cover"] = folder_cover(os.path.dirname(path))
    return info


def folder_cover(folder):
    """An album's cover.jpg, folder.jpg… beside its songs, or None."""
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    def rank(name):
        # Windows Media Player saves "AlbumArt_{…}_Large.jpg"; prefer it to the small copies
        stem = os.path.splitext(name.lower())[0]
        if stem.startswith("albumart") and stem.endswith("large"):
            return 1
        return COVER_NAMES.index(stem) + 2 if stem in COVER_NAMES else None

    found = sorted((rank(n), n) for n in names
                   if os.path.splitext(n.lower())[1] in COVER_EXTENSIONS and rank(n) is not None)
    for _, name in found:
        try:
            return load_pixbuf(os.path.join(folder, name))
        except GLib.Error:
            pass
    return None


_portal_requests = 0


def portal_open(window, path, directory=False, on_error=None):
    """Open path in a host app (asking which) or its folder in Files, via the OpenURI portal.

    With directory=True the file manager opens the folder containing path, with path selected.
    """
    global _portal_requests
    try:
        fds = Gio.UnixFDList.new()
        # Not O_PATH: the portal answers an O_PATH fd with an error and shows nothing
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        try:
            index = fds.append(fd)
        finally:
            os.close(fd)
    except (OSError, GLib.Error) as e:
        if on_error:
            on_error(str(e))
        return
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    _portal_requests += 1
    token = f"glimpse_{_portal_requests}"
    options = {"handle_token": GLib.Variant("s", token)}
    if not directory:
        options["ask"] = GLib.Variant("b", True)

    # The portal reports failure later, on the request object, so listen there first.
    sender = bus.get_unique_name()[1:].replace(".", "_")
    handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    def on_response(conn, _sender, _path, _iface, _signal, params):
        conn.signal_unsubscribe(subscription)
        if params.unpack()[0] == 2 and on_error:  # 1 is the user cancelling
            on_error("no app could open it")

    subscription = bus.signal_subscribe(
        "org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request", "Response", handle,
        None, Gio.DBusSignalFlags.NONE, on_response)

    def done(bus, result):
        try:
            bus.call_with_unix_fd_list_finish(result)
        except GLib.Error as e:
            bus.signal_unsubscribe(subscription)
            if on_error:
                on_error(e.message)

    bus.call_with_unix_fd_list(
        "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop", "org.freedesktop.portal.OpenURI",
        "OpenDirectory" if directory else "OpenFile", GLib.Variant("(sha{sv})", ("", index, options)),
        GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, -1, fds, None, done)


def trash_on_drive(path):
    """Move path into its drive's own .Trash-UID folder (freedesktop Trash spec).

    In the Snap, GLib trashes through the Trash portal, which refuses files on some
    external drives (e.g. NTFS). This does what GLib does outside the Snap; GNOME
    Files shows these files in its Trash as usual.
    """
    real = os.path.realpath(path)
    top = os.path.dirname(real)
    while not os.path.ismount(top):
        top = os.path.dirname(top)
    if top == "/":
        raise OSError("not on an external drive")
    trash = os.path.join(top, f".Trash-{os.getuid()}")
    files, info = os.path.join(trash, "files"), os.path.join(trash, "info")
    os.makedirs(files, mode=0o700, exist_ok=True)
    os.makedirs(info, mode=0o700, exist_ok=True)
    # Claim a free name by creating its .trashinfo exclusively, as the spec asks.
    name = os.path.basename(real)
    stem, ext = os.path.splitext(name)
    n = 1
    while True:
        info_path = os.path.join(info, name + ".trashinfo")
        try:
            fd = os.open(info_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            name = f"{stem}.{n}{ext}"
            n += 1
    with os.fdopen(fd, "w") as f:
        f.write("[Trash Info]\n"
                f"Path={urllib.parse.quote(os.path.relpath(real, top))}\n"
                f"DeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    try:
        os.rename(real, os.path.join(files, name))
    except OSError:
        os.unlink(info_path)
        raise


# Keywords as photo apps store them: XMP first, then the older IPTC field
TAG_KEYS = ("Xmp.dc.subject", "Iptc.Application2.Keywords")


def open_metadata(path):
    """GExiv2 metadata for path, or None if GExiv2 is missing or can't read the file."""
    if GExiv2 is None or is_video(path) or is_audio(path):  # tags go in images only
        return None
    metadata = GExiv2.Metadata()
    try:
        with METADATA_LOCK:
            metadata.open_path(path)
    except GLib.Error:
        return None
    return metadata


def read_tags(metadata):
    tags = []
    for key in TAG_KEYS:
        try:
            values = metadata.try_get_tag_multiple(key) if metadata.try_has_tag(key) else []
        except GLib.Error:
            continue
        for tag in values or []:
            tag = tag.strip()
            if tag and tag not in tags:
                tags.append(tag)
    return tags


def write_tags(path, tags):
    """Save tags as XMP keywords, and as IPTC keywords too if the file already has those."""
    with METADATA_LOCK:
        _write_tags(path, tags)


def _write_tags(path, tags):
    metadata = GExiv2.Metadata()
    metadata.open_path(path)
    if not metadata.get_supports_xmp():
        raise OSError("this file type can't hold tags")
    keys = ["Xmp.dc.subject"]
    if metadata.get_supports_iptc() and metadata.try_has_tag("Iptc.Application2.Keywords"):
        keys.append("Iptc.Application2.Keywords")
    for key in keys:
        if tags:
            metadata.try_set_tag_multiple(key, tags)
        elif metadata.try_has_tag(key):
            metadata.try_clear_tag(key)
    metadata.save_file(path)


def camera_details(metadata):
    """[(label, value)] for the EXIF details worth showing, skipping missing ones."""
    def text(key):
        try:
            if metadata.try_has_tag(key):
                return (metadata.try_get_tag_interpreted_string(key) or "").strip()
        except GLib.Error:
            pass
        return ""

    def number(getter):
        try:
            return getter()
        except GLib.Error:
            return None

    rows = []
    taken = text("Exif.Photo.DateTimeOriginal") or text("Exif.Image.DateTime")
    try:
        date, clock = taken.split(" ", 1)
        stamp = GLib.DateTime.new_local(*map(int, date.split(":")), *map(int, clock.split(":")))
        taken = stamp.format("%-d %B %Y, %H:%M:%S") if stamp else taken
    except ValueError:
        pass
    rows.append(("Date Taken", taken))
    make, model = text("Exif.Image.Make"), text("Exif.Image.Model")
    rows.append(("Camera", model if model.lower().startswith(make.lower()) else f"{make} {model}".strip()))
    rows.append(("Lens", text("Exif.Photo.LensModel")))
    if metadata.try_has_tag("Exif.Photo.ExposureTime") and (exposure := number(metadata.try_get_exposure_time)):
        num, den = exposure
        if num and den:
            rows.append(("Exposure", f"1/{round(den / num)} s" if num < den else f"{num / den:g} s"))
    if metadata.try_has_tag("Exif.Photo.FNumber") and (fnumber := number(metadata.try_get_fnumber)):
        rows.append(("Aperture", f"f/{fnumber:g}"))
    if iso := number(metadata.try_get_iso_speed):
        rows.append(("ISO", str(iso)))
    if metadata.try_has_tag("Exif.Photo.FocalLength") and (focal := number(metadata.try_get_focal_length)):
        rows.append(("Focal Length", f"{focal:g} mm"))
    rows.append(("Flash", text("Exif.Photo.Flash")))
    if metadata.try_has_tag("Exif.GPSInfo.GPSLatitude") and (gps := number(metadata.try_get_gps_info)):
        rows.append(("Location", f"{gps[1]:.5f}, {gps[0]:.5f}"))
    return [(label, value) for label, value in rows if value]


def show_containing_folder(window, path):
    """Open the folder holding path in the file manager, with path selected."""
    if IN_SNAP:
        portal_open(window, path, directory=True)
    else:
        Gtk.FileLauncher(file=Gio.File.new_for_path(path)).open_containing_folder(window, None, None)


def show_folder(window, path):
    if IN_SNAP:
        portal_open(window, path, directory=True)
    else:
        Gtk.FileLauncher(file=Gio.File.new_for_path(path)).launch(window, None, None)


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
        "use_trash": True,
        "confirm_delete": True,
        "show_thumbnails": True,
        "show_hidden": False,
        "sort_by": "name",              # name | date | tag
        "last_target_folder": "",
        "last_folder": "",
        "folders": [],                  # folders pinned in the sidebar
        "sidebar_width": 280,           # as dragged; the sidebar widens beyond it to fit open folders
    }

    def __init__(self):
        super().__init__()
        self.path = os.path.join(GLib.get_user_config_dir(), "glimpse", "settings.json")
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
        self.tags = None  # filled in from TagIndex; None until read


def scan_folder(folder, sort_by, show_hidden):
    items = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return items
    for entry in entries:
        if (not show_hidden and entry.name.startswith(".")) or not is_media(entry.name):
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


SORT_OPTIONS = [("name", "Name"), ("date", "Date Modified"), ("tag", "Tag")]
UNTAGGED = "\x01untagged"  # tag-filter value for images without tags; can't be a real tag


def sort_by_tag(items):
    """Group images under their alphabetically first tag; untagged ones go last. Keeps name order within."""
    def key(item):
        tags = sorted(t.casefold() for t in item.tags or [])
        return (0, tags[0]) if tags else (1, "")
    return sorted(items, key=key)


def read_file_tags(path):
    metadata = open_metadata(path)
    if metadata is None:
        return []
    with METADATA_LOCK:
        return read_tags(metadata)


def _storable(path):
    """SQLite text must be valid UTF-8; a few odd file names aren't."""
    try:
        path.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


def _under(roots):
    """SQL condition and parameters matching paths inside any of roots ('/' sorts just before '0')."""
    if not roots:
        return "0", []
    return " OR ".join(["(path >= ? AND path < ?)"] * len(roots)), \
        [bound for root in roots for bound in (root.rstrip("/") + "/", root.rstrip("/") + "0")]


class TagIndex:
    """Every image's tags, saved in a small database between runs.

    Reading tags from thousands of photos on a USB drive is slow, so each file's tags
    are read once and kept until its modified time changes. Only the main thread uses
    the database; background threads just read files.
    """

    def __init__(self):
        self.generation = 0
        # In the Snap, the common dir: it's kept as is across updates, not copied per revision
        folder = os.path.join(os.environ.get("SNAP_USER_COMMON") or GLib.get_user_cache_dir(), "glimpse")
        try:
            os.makedirs(folder, exist_ok=True)
            self.db = self._open(os.path.join(folder, "tags.db"))
        except (OSError, sqlite3.Error):
            self.db = self._open(":memory:")
        try:
            os.remove(os.path.join(GLib.get_user_cache_dir(), "image2", "tags.json"))  # replaced by tags.db
        except OSError:
            pass

    @staticmethod
    def _open(path):
        db = sqlite3.connect(path)
        db.executescript("""
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS images (path TEXT PRIMARY KEY, mtime INTEGER, size INTEGER, tags TEXT);
            CREATE TABLE IF NOT EXISTS image_tags (path TEXT, tag TEXT, folded TEXT);
            CREATE INDEX IF NOT EXISTS image_tags_folded ON image_tags (folded);
            CREATE INDEX IF NOT EXISTS image_tags_path ON image_tags (path);
        """)
        return db

    def close(self):
        self.db.close()

    # Folder view

    def lookup(self, items):
        """{path: tags} for the items whose indexed modified time still matches."""
        wanted = {i.path: i.mtime for i in items}
        found = {path: [] for path in wanted if not _storable(path)}
        paths = [path for path in wanted if path not in found]
        for start in range(0, len(paths), 500):
            chunk = paths[start:start + 500]
            rows = self.db.execute(f"SELECT path, mtime, tags FROM images WHERE path IN "
                                   f"({','.join('?' * len(chunk))})", chunk)
            for path, mtime, tags in rows:
                if wanted[path] == mtime:
                    found[path] = json.loads(tags)
        return found

    def load(self, items, done):
        """Read the tags of items in the background, save them, then call done() unless a newer load started."""
        self.generation += 1
        generation = self.generation
        todo = [(i.path, i.mtime, i.size) for i in items]

        def work():
            found = []
            for path, mtime, size in todo:
                if generation != self.generation:
                    break
                found.append((path, mtime, size, read_file_tags(path)))
            return found

        def finished(found, error):
            if found:
                self.store(found)
            if generation == self.generation and error is None:
                done()

        run_in_thread(work, finished)

    # Changes

    def store(self, entries):
        """Save [(path, mtime, size, tags)]."""
        with self.db:
            for path, mtime, size, tags in entries:
                if not _storable(path):
                    continue
                self.db.execute("INSERT OR REPLACE INTO images VALUES (?, ?, ?, ?)",
                                (path, mtime, size, json.dumps(tags)))
                self.db.execute("DELETE FROM image_tags WHERE path = ?", (path,))
                self.db.executemany("INSERT INTO image_tags VALUES (?, ?, ?)",
                                    [(path, tag, tag.casefold()) for tag in tags])

    def store_paths(self, saved):
        """Save {path: tags} just written, with each file's new modified time."""
        entries = []
        for path, tags in saved.items():
            try:
                st = os.stat(path)
            except OSError:
                continue
            entries.append((path, st.st_mtime_ns, st.st_size, tags))
        self.store(entries)

    def remove(self, paths):
        with self.db:
            for path in paths:
                if _storable(path):
                    self.db.execute("DELETE FROM images WHERE path = ?", (path,))
                    self.db.execute("DELETE FROM image_tags WHERE path = ?", (path,))

    def move(self, pairs):
        """Images moved or renamed keep their tags: [(old path, new path)]."""
        with self.db:
            for old, new in pairs:
                if _storable(old) and _storable(new):
                    self.db.execute("DELETE FROM images WHERE path = ?", (new,))
                    self.db.execute("DELETE FROM image_tags WHERE path = ?", (new,))
                    self.db.execute("UPDATE images SET path = ? WHERE path = ?", (new, old))
                    self.db.execute("UPDATE image_tags SET path = ? WHERE path = ?", (new, old))

    # Library

    def mtimes(self, roots):
        where, params = _under(roots)
        return dict(self.db.execute(f"SELECT path, mtime FROM images WHERE {where}", params))

    def count_images(self, roots):
        where, params = _under(roots)
        return self.db.execute(f"SELECT COUNT(*) FROM images WHERE {where}", params).fetchone()[0]

    def tag_counts(self, roots):
        """[(tag, folded, number of images)] in roots, alphabetically."""
        where, params = _under(roots)
        return self.db.execute(f"SELECT MIN(tag), folded, COUNT(DISTINCT path) FROM image_tags WHERE {where} "
                               f"GROUP BY folded ORDER BY folded", params).fetchall()

    def _matching(self, folded, match_all, roots):
        where, params = _under(roots)
        return (f"SELECT path FROM image_tags WHERE folded IN ({','.join('?' * len(folded))}) AND ({where}) "
                f"GROUP BY path HAVING COUNT(DISTINCT folded) >= ?",
                list(folded) + params + [len(folded) if match_all else 1])

    def count(self, folded, match_all, roots):
        query, params = self._matching(folded, match_all, roots)
        return self.db.execute(f"SELECT COUNT(*) FROM ({query})", params).fetchone()[0]

    def search(self, folded, match_all, roots):
        """[(path, mtime, size, tags)] of the images with any (or all) of the folded tags."""
        query, params = self._matching(folded, match_all, roots)
        rows = self.db.execute(f"SELECT path, mtime, size, tags FROM images WHERE path IN ({query})", params)
        return [(path, mtime, size, json.loads(tags)) for path, mtime, size, tags in rows]


class Library:
    """Keeps the tag index up to date for every image under the pinned folders, in the background."""

    def __init__(self, index):
        self.index = index
        self.generation = 0
        self.running = False
        self.roots_scanned = None
        self.checked = 0  # images looked at so far in this pass
        self.listeners = []  # called on the main loop as the index changes

    @staticmethod
    def roots(folders):
        """The pinned folders that exist, leaving out any inside another one."""
        roots = []
        for folder in sorted({os.path.abspath(f) for f in folders}):
            if os.path.isdir(folder) and not any(folder.startswith(r.rstrip("/") + "/") for r in roots):
                roots.append(folder)
        return roots

    def refresh(self, folders, show_hidden):
        """Look through every image under folders; read tags of new and changed ones."""
        roots = self.roots(folders)
        if self.running and roots == self.roots_scanned:
            return
        self.generation += 1
        generation = self.generation
        self.running, self.roots_scanned, self.checked = True, roots, 0
        known = self.index.mtimes(roots)

        def work():
            batch, missing, checked, last = [], set(known), 0, time.monotonic()
            for root in roots:
                for folder, dirs, files in os.walk(root):
                    if generation != self.generation:
                        return None
                    dirs[:] = [d for d in dirs
                               if not d.startswith(".Trash") and (show_hidden or not d.startswith("."))]
                    for name in files:
                        if (not show_hidden and name.startswith(".")) or not is_image(name):
                            continue
                        path = os.path.join(folder, name)
                        try:
                            st = os.stat(path)
                        except OSError:
                            continue
                        checked += 1
                        missing.discard(path)
                        if known.get(path) != st.st_mtime_ns:
                            batch.append((path, st.st_mtime_ns, st.st_size, read_file_tags(path)))
                        if time.monotonic() - last > 1:
                            GLib.idle_add(self._merge, generation, batch, checked, None)
                            batch, last = [], time.monotonic()
            return batch, checked, missing

        def finished(result, error):
            if generation != self.generation:
                return
            if result is None or error is not None:
                self.running = False
                self._notify()
                return
            self._merge(generation, *result)

        run_in_thread(work, finished)

    def _merge(self, generation, batch, checked, missing):
        if generation == self.generation:
            if batch:
                self.index.store(batch)
            self.checked = checked
            if missing is not None:  # the pass is complete: forget images that are gone
                self.index.remove(missing)
                self.running = False
            self._notify()
        return False

    def _notify(self):
        for listener in list(self.listeners):
            listener()


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
        sort_row = Adw.ComboRow(title="Sort By", model=Gtk.StringList.new([label for _, label in SORT_OPTIONS]))
        keys = [key for key, _ in SORT_OPTIONS]
        sort_row.set_selected(keys.index(settings["sort_by"]) if settings["sort_by"] in keys else 0)
        sort_row.connect("notify::selected",
                         lambda row, _: settings.__setitem__("sort_by", keys[row.get_selected()]))
        sidebar.add(sort_row)

    def _switch(self, title, subtitle, key):
        row = Adw.SwitchRow(title=title, active=self.settings[key])
        if subtitle:
            row.set_subtitle(subtitle)
        row.connect("notify::active", lambda r, _: self.settings.__setitem__(key, r.get_active()))
        return row


class PinnedFoldersDialog(Adw.Dialog):
    """Two pages: the pinned folders (unpin, reorder, Add Folders), and a browser to pin more,
    whose Home button goes back to the pinned folders."""

    def __init__(self, settings, browse=False):
        super().__init__(title="Pinned Folders", content_width=620, content_height=600)
        self.settings = settings
        self.folder = None
        self.toasts = Adw.ToastOverlay()
        self.pages = Adw.NavigationView()
        self.toasts.set_child(self.pages)
        self.set_child(self.toasts)
        self.pages.add(self._pinned_page())
        self.pages.add(self._browse_page())

        handler = settings.connect("changed", self._on_settings_changed)
        self.connect("closed", lambda *_: settings.disconnect(handler))
        self._show_pinned()
        if browse:
            self.add_folders()

    # -- home: the pinned folders

    def _pinned_page(self):
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add Folders")
        add.connect("clicked", lambda *_: self.add_folders())
        header = Adw.HeaderBar()
        header.pack_start(add)
        self.pinned = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"],
                                  valign=Gtk.Align.START)
        self.pinned.connect("row-activated", lambda box, row: row.available and self.add_folders(row.path))
        add_button = Gtk.Button(label="_Add Folders…", use_underline=True, halign=Gtk.Align.CENTER,
                                css_classes=["pill", "suggested-action"])
        add_button.connect("clicked", lambda *_: self.add_folders())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=18, margin_end=18, margin_top=6, margin_bottom=18)
        box.append(Gtk.Label(
            label="These folders are shown under Folders in the sidebar. Alt+1…9 moves files into the "
                  "folder with that number; Ctrl+Alt+1…9 copies them.",
            xalign=0, wrap=True, css_classes=["dim-label"]))
        box.append(Gtk.ScrolledWindow(child=self.pinned, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        box.append(add_button)
        view = Adw.ToolbarView(content=box)
        view.add_top_bar(header)
        return Adw.NavigationPage(title="Pinned Folders", tag="pinned", child=view)

    def _show_pinned(self):
        self.pinned.remove_all()
        folders = self.settings["folders"]
        if not folders:
            self.pinned.append(Adw.ActionRow(title="No pinned folders",
                                             subtitle="Click Add Folders to pin the folders you use most.",
                                             activatable=False))
        for index, path in enumerate(folders):
            available = os.path.isdir(path)
            row = Adw.ActionRow(title=GLib.markup_escape_text(GLib.filename_display_basename(path) or path),
                                subtitle=GLib.markup_escape_text(
                                    GLib.filename_display_name(path)
                                    + ("" if available else " — not available; is the drive connected?")),
                                activatable=available, title_lines=1, subtitle_lines=2,
                                tooltip_text=GLib.filename_display_name(path))
            row.path, row.available = path, available
            row.add_prefix(Gtk.Label(label=str(index + 1) if index < 9 else "", width_chars=1,
                                     css_classes=["dim-label", "numeric"]))
            row.add_prefix(Gtk.Image(icon_name="folder-symbolic" if available else "dialog-warning-symbolic"))
            for icon, tip, target, sensitive in (
                    ("go-up-symbolic", "Move Up", index - 1, index > 0),
                    ("go-down-symbolic", "Move Down", index + 1, index < len(folders) - 1)):
                button = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER,
                                    sensitive=sensitive, css_classes=["flat"])
                button.connect("clicked", lambda b, i=index, t=target: self._move(i, t))
                row.add_suffix(button)
            unpin = Gtk.Button(label="Unpin", valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
            unpin.connect("clicked", lambda b, p=path: self.toggle_pin(p))
            row.add_suffix(unpin)
            self.pinned.append(row)

    def _move(self, index, target):
        folders = list(self.settings["folders"])
        folders.insert(target, folders.pop(index))
        self.settings["folders"] = folders

    # -- add folders: browse and pin

    def _browse_page(self):
        home_button = Gtk.Button(child=Adw.ButtonContent(icon_name="go-home-symbolic", label="Pinned Folders"),
                                 tooltip_text="Back to the Pinned Folders")
        home_button.connect("clicked", lambda *_: self.pages.pop_to_tag("pinned"))
        header = Adw.HeaderBar(show_back_button=False)
        header.pack_start(home_button)

        places = Gtk.Box(css_classes=["linked"], halign=Gtk.Align.START)
        home = GLib.get_home_dir()
        user_media = os.path.join("/media", GLib.get_user_name())
        for icon, label, path in (("user-home-symbolic", "Home Folder", home),
                                  ("drive-harddisk-symbolic", "Drives",
                                   user_media if os.path.isdir(user_media) else "/media"),
                                  ("computer-symbolic", "Computer", "/")):
            button = Gtk.Button(child=Adw.ButtonContent(icon_name=icon, label=label))
            button.connect("clicked", lambda b, p=path: self.browse(p))
            places.append(button)
        self.up_button = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Up to the Parent Folder")
        self.up_button.connect("clicked", lambda *_: self.browse(os.path.dirname(self.folder)))
        self.path_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.START,
                                    css_classes=["dim-label"])
        path_bar = Gtk.Box(spacing=6)
        path_bar.append(self.up_button)
        path_bar.append(self.path_label)

        self.subfolders = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"],
                                      valign=Gtk.Align.START)
        self.subfolders.connect("row-activated", lambda box, row: hasattr(row, "path") and self.browse(row.path))
        self.pin_button = Gtk.Button(halign=Gtk.Align.CENTER)
        self.pin_button.connect("clicked", lambda *_: self.toggle_pin(self.folder))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=18, margin_end=18, margin_top=6, margin_bottom=18)
        box.append(Gtk.Label(label="Open a folder by clicking it, then pin it, or pin a folder in the "
                                   "list with its pin button.", xalign=0, wrap=True, css_classes=["dim-label"]))
        box.append(places)
        box.append(path_bar)
        box.append(Gtk.ScrolledWindow(child=self.subfolders, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        box.append(self.pin_button)
        view = Adw.ToolbarView(content=box)
        view.add_top_bar(header)
        return Adw.NavigationPage(title="Add Folders", tag="browse", child=view)

    def add_folders(self, start=None):
        """Show the browser, at start or the folder last viewed."""
        if start is None:
            last = self.settings["last_folder"]
            start = self.folder or (last if last and os.path.isdir(last) else GLib.get_home_dir())
        self.browse(start)
        if self.pages.get_visible_page().get_tag() != "browse":
            self.pages.push_by_tag("browse")

    def browse(self, path):
        self.folder = path
        self.path_label.set_label(GLib.filename_display_name(path))
        self.path_label.set_tooltip_text(GLib.filename_display_name(path))
        self.up_button.set_sensitive(path != "/")
        pinned = path in self.settings["folders"]
        name = GLib.filename_display_basename(path) if path != "/" else "Computer"
        self.pin_button.set_child(Adw.ButtonContent(icon_name="view-pin-symbolic",
                                                    label=f"Unpin “{name}”" if pinned else f"Pin “{name}”"))
        self.pin_button.set_css_classes(["pill", "destructive-action"] if pinned else ["pill", "suggested-action"])

        self.subfolders.remove_all()
        children = list_subfolders(path, self.settings["show_hidden"])
        if not children:
            self.subfolders.append(Adw.ActionRow(title="No folders inside", activatable=False))
        for node in children:
            row = Adw.ActionRow(title=GLib.markup_escape_text(node.name), activatable=True)
            row.path = node.path
            row.add_prefix(Gtk.Image(icon_name="folder-symbolic"))
            pinned = node.path in self.settings["folders"]
            button = Gtk.Button(icon_name="view-pin-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text="Unpin" if pinned else "Pin",
                                css_classes=["circular", "suggested-action"] if pinned else ["circular", "flat"])
            button.connect("clicked", lambda b, p=node.path: self.toggle_pin(p))
            row.add_suffix(button)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic", css_classes=["dim-label"]))
            self.subfolders.append(row)

    def toggle_pin(self, path):
        folders = self.settings["folders"]
        name = GLib.filename_display_basename(path) if path != "/" else "Computer"
        if path in folders:
            self.settings["folders"] = [f for f in folders if f != path]
            self.toasts.add_toast(Adw.Toast(title=f"Unpinned “{name}”", timeout=2))
        else:
            self.settings["folders"] = folders + [path]
            self.toasts.add_toast(Adw.Toast(title=f"Pinned “{name}”", timeout=2))

    def _on_settings_changed(self, settings, key):
        if key == "folders":
            self._show_pinned()
            if self.folder:
                self.browse(self.folder)  # refresh the pin buttons
        elif key == "show_hidden" and self.folder:
            self.browse(self.folder)


def edit_tags(tags, add=(), remove=(), rename=None):
    """tags with rename=(old, new) applied, remove dropped and add appended; ignores case, no duplicates."""
    drop = {t.casefold() for t in remove}
    result, seen = [], set()
    for tag in list(tags) + list(add):
        if rename and tag.casefold() == rename[0].casefold():
            tag = rename[1]
        key = tag.casefold()
        if key not in drop and key not in seen:
            seen.add(key)
            result.append(tag)
    return result


class PropertiesDialog(Adw.PreferencesDialog):
    """Details of one image, or a summary of several, with their tags to edit."""

    def __init__(self, paths, known_tags, on_saved=None):
        """known_tags: {path: tags} already read for the image list; missing ones are read here.
        on_saved({path: tags}) runs after tags are written."""
        super().__init__(title="Properties")
        self.paths = paths
        self.on_saved = on_saved
        self.tags = {}  # path → tags, for the images that can hold tags
        page = Adw.PreferencesPage()
        self.add(page)

        # Tags first: they're the part you edit
        self.tags_group = Adw.PreferencesGroup(title="Tags")
        page.add(self.tags_group)
        self.tag_rows = []
        self.entry = None
        if GExiv2 is None:
            self.tags_group.set_description("Editing tags needs GExiv2 (package gir1.2-gexiv2-0.10).")
        else:
            self.tags_group.set_description("Reading tags…")
            run_in_thread(lambda: self._read_tags(known_tags), self._on_tags_read)

        if len(paths) == 1:
            self._add_details(page, paths[0])
        else:
            summary = Adw.PreferencesGroup(title="Selection")
            page.add(summary)
            size = 0
            for path in paths:
                try:
                    size += os.stat(path).st_size
                except OSError:
                    pass
            summary.add(self._property("Images", str(len(paths))))
            summary.add(self._property("Total Size", GLib.format_size(size)))

    def _add_details(self, page, path):
        metadata = open_metadata(path)
        file_group = Adw.PreferencesGroup(title="File")
        page.add(file_group)
        file = Gio.File.new_for_path(path)
        try:
            info = file.query_info("standard::content-type,standard::size,time::modified",
                                   Gio.FileQueryInfoFlags.NONE, None)
        except GLib.Error:
            info = None
        rows = [("Name", GLib.filename_display_basename(path)),
                ("Folder", GLib.filename_display_name(os.path.dirname(path)))]
        if info:
            rows.append(("Type", Gio.content_type_get_description(info.get_content_type() or "")))
            rows.append(("Size", GLib.format_size(info.get_size())))
        fmt = GdkPixbuf.Pixbuf.get_file_info(path)
        if fmt and fmt[0] and fmt[1] > 0:
            rows.append(("Dimensions", f"{fmt[1]} × {fmt[2]} pixels"))
        if info and (modified := info.get_modification_date_time()):
            rows.append(("Modified", modified.to_local().format("%-d %B %Y, %H:%M")))
        for label, value in rows:
            file_group.add(self._property(label, value))

        details = camera_details(metadata) if metadata else []
        if details:
            camera = Adw.PreferencesGroup(title="Camera")
            page.add(camera)
            for label, value in details:
                camera.add(self._property(label, value))

    @staticmethod
    def _property(label, value):
        row = Adw.ActionRow(title=label, subtitle=GLib.markup_escape_text(value), subtitle_selectable=True)
        row.add_css_class("property")
        return row

    # ---------------------------------------------------------------- tags

    def _read_tags(self, known_tags):
        found = {}
        for path in self.paths:
            tags = known_tags.get(path)
            if tags is None or len(self.paths) == 1:  # one image: also check it can hold tags
                metadata = open_metadata(path)
                if metadata is None or not metadata.get_supports_xmp():
                    continue
                with METADATA_LOCK:
                    tags = read_tags(metadata)
            found[path] = list(tags)
        return found

    def _on_tags_read(self, found, error):
        if error is not None or not found:
            self.tags_group.set_description("These images can't hold tags." if len(self.paths) > 1
                                            else "This file type can't hold tags.")
            return
        self.tags = found
        if len(self.paths) == 1:
            self.tags_group.set_description("Saved in the image, so other photo apps can read them too.")
        else:
            skipped = len(self.paths) - len(found)
            self.tags_group.set_description(
                f"Changes apply to all {plural(len(found), 'selected image')}."
                + (f" {plural(skipped, 'image')} can't hold tags and won't change." if skipped else ""))
        self.entry = Adw.EntryRow(title="Add Tags (separate with commas)", show_apply_button=True)
        self.entry.connect("apply", self._on_add)
        self.entry.connect("entry-activated", self._on_add)
        self.tags_group.add(self.entry)
        self._show_tags()

    def _show_tags(self):
        for row in self.tag_rows:
            self.tags_group.remove(row)
        self.tag_rows = []
        counts, names = {}, {}
        for tags in self.tags.values():
            for tag in tags:
                counts[tag.casefold()] = counts.get(tag.casefold(), 0) + 1
                names.setdefault(tag.casefold(), tag)
        total = len(self.tags)
        for key in sorted(counts, key=lambda k: GLib.utf8_collate_key(k, -1)) if total > 1 else counts:
            tag, count = names[key], counts[key]
            row = Adw.ActionRow(title=GLib.markup_escape_text(tag))
            if count < total:
                row.set_subtitle(f"{count} of {total} images")
                row.add_suffix(self._button("list-add-symbolic", "Add to All Selected Images",
                                            lambda t=tag: self._apply(lambda tags: edit_tags(tags, add=[t]))))
            row.add_suffix(self._button("document-edit-symbolic", "Rename Tag", lambda t=tag: self._ask_rename(t)))
            row.add_suffix(self._button("window-close-symbolic", "Remove Tag",
                                        lambda t=tag: self._apply(lambda tags: edit_tags(tags, remove=[t]))))
            self.tags_group.add(row)
            self.tag_rows.append(row)

    @staticmethod
    def _button(icon, tooltip, callback):
        button = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, tooltip_text=tooltip, css_classes=["flat"])
        button.connect("clicked", lambda _: callback())
        return button

    def _on_add(self, entry):
        new = [t.strip() for t in entry.get_text().split(",") if t.strip()]
        entry.set_text("")
        if new:
            self._apply(lambda tags: edit_tags(tags, add=new))

    def _ask_rename(self, old):
        count = sum(any(t.casefold() == old.casefold() for t in tags) for tags in self.tags.values())
        dialog = Adw.AlertDialog(heading="Rename Tag",
                                 body=f"Renames “{old}” in {plural(count, 'image')}." if len(self.tags) > 1 else None)
        entry = Gtk.Entry(text=old, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "_Cancel")
        dialog.add_response("rename", "_Rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")

        def validate(*_):
            new = entry.get_text().strip()
            dialog.set_response_enabled("rename", bool(new) and "," not in new and new != old)

        def respond(_, response):
            if response == "rename":
                new = entry.get_text().strip()
                self._apply(lambda tags: edit_tags(tags, rename=(old, new)))

        entry.connect("changed", validate)
        dialog.connect("response", respond)
        validate()
        dialog.present(self)
        entry.grab_focus()

    def _apply(self, change):
        """Write change(tags) to every image whose tags it alters."""
        writes = {path: change(tags) for path, tags in self.tags.items()}
        writes = {path: tags for path, tags in writes.items() if tags != self.tags[path]}
        if not writes:
            return
        self.tags_group.set_sensitive(False)

        def work():
            saved, errors = {}, []
            for path, tags in writes.items():
                try:
                    write_tags(path, tags)
                    saved[path] = tags
                except (GLib.Error, OSError) as e:
                    errors.append(e.message if isinstance(e, GLib.Error) else str(e))
            return saved, errors

        def done(result, error):
            self.tags_group.set_sensitive(True)
            saved, errors = result if result else ({}, [str(error)])
            self.tags.update(saved)
            if saved and self.on_saved:
                self.on_saved(saved)
            self._show_tags()
            if errors:
                what = "" if len(writes) == 1 else f" in {plural(len(errors), 'image')}"
                self.add_toast(Adw.Toast(title=GLib.markup_escape_text(f"Couldn't save tags{what}: {errors[0]}")))
            elif len(saved) > 1:
                self.add_toast(Adw.Toast(title=f"Updated {plural(len(saved), 'image')}"))
            if self.entry:
                self.entry.grab_focus()

        run_in_thread(work, done)


class FindByTagDialog(Adw.PreferencesDialog):
    """Pick tags to list every image that has them, from all pinned folders."""

    def __init__(self, window):
        super().__init__(title="Find by Tag", search_enabled=True)
        self.window = window
        app = window.get_application()
        self.index, self.library, self.settings = app.tag_index, app.library, window.settings
        self.roots = Library.roots(self.settings["folders"])
        search = window.search or {}
        self.checked = dict(zip(search.get("folded", []), search.get("tags", [])))  # folded → tag
        self.rows = []
        self.fill_source = 0

        page = Adw.PreferencesPage()
        self.add(page)
        status = Adw.PreferencesGroup()
        page.add(status)
        self.status_row = Adw.ActionRow()
        self.spinner = Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER)
        self.status_row.add_suffix(self.spinner)
        status.add(self.status_row)

        options = Adw.PreferencesGroup()
        page.add(options)
        self.match_row = Adw.ComboRow(title="Show Images With",
                                      model=Gtk.StringList.new(["Any Selected Tag", "All Selected Tags"]),
                                      selected=1 if search.get("all") else 0)
        self.match_row.connect("notify::selected", lambda *_: self._update_button())
        options.add(self.match_row)

        self.tags_group = Adw.PreferencesGroup(title="Tags")
        page.add(self.tags_group)
        self.show_button = Gtk.Button(label="Show Images", valign=Gtk.Align.CENTER,
                                      css_classes=["suggested-action"])
        self.show_button.connect("clicked", self._on_show)
        self.tags_group.set_header_suffix(self.show_button)

        self.library.listeners.append(self._on_library_changed)
        self.connect("closed", self._on_closed)
        self._fill()
        if self.roots:
            self.library.refresh(self.settings["folders"], self.settings["show_hidden"])
        self._update_status()

    def _on_closed(self, *_):
        self.library.listeners.remove(self._on_library_changed)
        if self.fill_source:
            GLib.source_remove(self.fill_source)

    def _on_library_changed(self):
        self._update_status()
        if not self.fill_source:  # refill the tag list at most every two seconds while indexing
            self.fill_source = GLib.timeout_add(0 if not self.library.running else 2000, self._fill_later)

    def _fill_later(self):
        self.fill_source = 0
        self._fill()
        return False

    def _update_status(self):
        running = self.library.running
        self.spinner.set_visible(running)
        if not self.roots:
            self.status_row.set_title("No pinned folders")
            self.status_row.set_subtitle("Pin folders in the sidebar to find their images by tag.")
        elif running:
            self.status_row.set_title("Indexing your pinned folders…")
            self.status_row.set_subtitle(
                f"{self.library.checked:,} images checked. Tags appear here as they're found; the first "
                "time on a large collection can take a while, and you can keep using Glimpse.")
        else:
            total = self.index.count_images(self.roots)
            self.status_row.set_title(f"{plural(total, 'image')} indexed")
            self.status_row.set_subtitle("In your pinned folders and their subfolders.")

    def _fill(self):
        for row in self.rows:
            self.tags_group.remove(row)
        self.rows = []
        counts = self.index.tag_counts(self.roots)
        if not counts:
            self.tags_group.set_description(
                "No tagged images found yet." + (" Still looking…" if self.library.running
                                                 else " Add tags in Properties (Alt+Enter)."))
        else:
            self.tags_group.set_description(None)
        for tag, folded, count in counts:
            row = Adw.ActionRow(title=GLib.markup_escape_text(tag), subtitle=plural(count, "image"))
            check = Gtk.CheckButton(active=folded in self.checked, valign=Gtk.Align.CENTER)
            check.connect("toggled", self._on_toggled, tag, folded)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            self.tags_group.add(row)
            self.rows.append(row)
        self._update_button()

    def _on_toggled(self, check, tag, folded):
        if check.get_active():
            self.checked[folded] = tag
        else:
            self.checked.pop(folded, None)
        self._update_button()

    def _update_button(self):
        if not self.checked:
            self.show_button.set_label("Show Images")
            self.show_button.set_sensitive(False)
            return
        count = self.index.count(list(self.checked), self.match_row.get_selected() == 1, self.roots)
        self.show_button.set_label(f"Show {plural(count, 'Image')}")
        self.show_button.set_sensitive(count > 0)

    def _on_show(self, *_):
        self.window.show_tag_search(list(self.checked.values()), self.match_row.get_selected() == 1)
        self.close()


# ----------------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------------

class ImageWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=1100, default_height=720)
        self.set_size_request(360, 300)
        self.settings = app.settings
        self.thumbs = app.thumbs
        self.tag_index = app.tag_index
        self.library = app.library
        self.search = None  # {"tags", "folded", "all"} while showing a Find by Tag result
        self.search_source = 0

        self.folder = None
        self.tag_filter = ""  # "" for all images, a tag, or UNTAGGED
        self.tags_loading = False
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
        self.library.listeners.append(self._on_library_changed)
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
        folder_header.append(Gtk.Button(icon_name="system-search-symbolic", action_name="win.find-tags",
                                        tooltip_text="Find by Tag… (Ctrl+F)", css_classes=["flat"]))
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

        images_header = Gtk.Box(margin_start=12, margin_end=6, margin_top=6)
        images_header.append(Gtk.Label(label="Files", xalign=0, hexpand=True, css_classes=["heading"]))
        self.tag_menu = Gio.Menu()
        self.view_button = Gtk.MenuButton(label="All", menu_model=self.tag_menu, css_classes=["flat"],
                                          always_show_arrow=True, tooltip_text="Show and Sort Images")
        images_header.append(self.view_button)
        self.search_close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="End Tag Search",
                                       css_classes=["flat"], visible=False)
        self.search_close.connect("clicked", lambda *_: self.end_tag_search())
        images_header.append(self.search_close)
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
        sidebar_header.pack_start(Gtk.Button(icon_name="view-pin-symbolic", action_name="win.manage-folders",
                                             tooltip_text="Manage Pinned Folders"))

        action_bar = Gtk.ActionBar()
        for icon, action, tip in (
                ("edit-copy-symbolic", "win.copy-to", "Copy to Folder… (Ctrl+Shift+C)"),
                ("folder-symbolic", "win.move-to", "Move to Folder… (Ctrl+Shift+M)"),
                ("document-edit-symbolic", "win.rename", "Rename… (F2)"),
                ("user-trash-symbolic", "win.delete", "Delete (Delete)")):
            action_bar.pack_start(Gtk.Button(icon_name=icon, action_name=action, tooltip_text=tip))
        self.selection_label = Gtk.Label(css_classes=["dim-label", "caption"])
        action_bar.pack_end(self.selection_label)

        sidebar_view = Adw.ToolbarView(content=sidebar_paned)
        sidebar_view.add_top_bar(sidebar_header)
        sidebar_view.add_bottom_bar(action_bar)

        # A handle on the sidebar's edge: drag it to resize the sidebar
        handle = Gtk.Box(width_request=8, halign=Gtk.Align.END, cursor=Gdk.Cursor.new_from_name("col-resize"))
        resize = Gtk.GestureDrag()
        resize.connect("drag-begin", lambda g, x, y: setattr(self, "resize_start", self.sidebar_width))
        resize.connect("drag-update", self._on_sidebar_drag)
        resize.connect("drag-end", lambda *_: self.settings.__setitem__("sidebar_width", self.sidebar_width))
        handle.add_controller(resize)
        sidebar = Gtk.Overlay(child=sidebar_view)
        sidebar.add_overlay(handle)

        # Content: the image
        self.view = ImageView()
        self.view.connect("scale-changed", lambda *_: self._update_zoom_label())
        self.scroller = Gtk.ScrolledWindow(child=self.view)
        self.stream = None  # the playing video or song
        self.video = Gtk.Video(hexpand=True, vexpand=True)

        # Music: cover art (or a note), the song's tags, and the playback controls
        self.cover = Gtk.Image(pixel_size=256, margin_bottom=12, css_classes=["icon-dropshadow"])
        self.song_title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, css_classes=["title-2"])
        self.song_artist = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, css_classes=["title-4"])
        self.song_album = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, css_classes=["dim-label"])
        self.music_controls = Gtk.MediaControls(margin_top=12)
        song = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER,
                       margin_start=24, margin_end=24, margin_top=24, margin_bottom=24)
        for widget in (self.cover, self.song_title, self.song_artist, self.song_album, self.music_controls):
            song.append(widget)
        self.music = Gtk.ScrolledWindow(child=Adw.Clamp(maximum_size=480, child=song),
                                        hscrollbar_policy=Gtk.PolicyType.NEVER)

        welcome = Adw.StatusPage(icon_name="image-x-generic-symbolic", title="Open a File",
                                 description="Images, videos and music. Or drag a file or folder onto this window")
        welcome_button = Gtk.Button(label="_Open File…", use_underline=True, action_name="win.open",
                                    halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        welcome.set_child(welcome_button)
        self.empty_page = Adw.StatusPage(icon_name="folder-symbolic", title="Nothing to Show",
                                         description="This folder has no images, videos or music")
        self.error_page = Adw.StatusPage(icon_name="image-missing-symbolic", title="Could Not Load Image")

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(welcome, "welcome")
        self.stack.add_named(self.empty_page, "empty")
        self.stack.add_named(self.error_page, "error")
        self.stack.add_named(self.scroller, "image")
        self.stack.add_named(self.video, "video")
        self.stack.add_named(self.music, "music")
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
        section.append("Find by Tag…", "win.find-tags")
        section.append("Pin Folders to Sidebar…", "win.add-folder")
        section.append("Pin Current Folder to Sidebar", "win.pin-current")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Open With…", "win.open-with")
        section.append("Open Containing Folder", "win.show-containing")
        section.append("Properties", "win.properties")
        section.append_submenu("Set as Wallpaper", wallpaper_menu())
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Fullscreen", "win.fullscreen")
        section.append("Manage Pinned Folders…", "win.manage-folders")
        section.append("Preferences", "app.preferences")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Keyboard Shortcuts", "app.shortcuts")
        section.append("Help", "app.help")
        section.append(f"About {APP_NAME}", "app.about")
        menu.append_section(None, section)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True,
                                       tooltip_text="Main Menu"))
        header.pack_end(Gtk.Button(icon_name="document-properties-symbolic", action_name="win.properties",
                                   tooltip_text="Properties (Alt+Enter)"))

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

        # The sidebar's width is set exactly (min = max), from the dragged width and the open folders
        self.sidebar_width = self.settings["sidebar_width"]
        self.split = Adw.OverlaySplitView(sidebar=sidebar, content=self.content, sidebar_width_fraction=1)
        self.fit_source = None
        self.folder_tree.connect("items-changed", lambda *_: self._queue_fit_sidebar())
        for signal in ("map", "notify::default-width", "notify::maximized", "notify::fullscreened"):
            self.connect(signal, lambda *_: self._queue_fit_sidebar())
        self._fit_sidebar()
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
        section.append("Open With…", "win.open-with")
        section.append("Open Containing Folder", "win.show-containing")
        section.append("Properties", "win.properties")
        section.append_submenu("Set as Wallpaper", wallpaper_menu())
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

    # ------------------------------------------------------------ sidebar ---

    def _on_sidebar_drag(self, gesture, dx, dy):
        self.sidebar_width = max(SIDEBAR_MIN, min(round(self.resize_start + dx), self._sidebar_limit()))
        self._fit_sidebar()

    def _sidebar_limit(self):
        """Widest the sidebar may get, leaving room for the image."""
        return max(SIDEBAR_MIN, (self.get_width() or self.get_default_size()[0]) - CONTENT_MIN)

    def _queue_fit_sidebar(self):
        # Expanding a folder changes the tree several times in a row: fit once afterwards
        # (and a resized window has its new width only after a moment)
        if self.fit_source is None:
            self.fit_source = GLib.timeout_add(50, self._on_fit_timeout)

    def _on_fit_timeout(self):
        self.fit_source = None
        self._fit_sidebar()
        return False

    def _fit_sidebar(self):
        width = max(self.sidebar_width, min(self._tree_width(), self._sidebar_limit()))
        self.split.set_min_sidebar_width(width)
        self.split.set_max_sidebar_width(width)

    def _tree_width(self):
        """Width the sidebar needs to show every visible folder's name in full."""
        layout = self.folder_view.create_pango_layout("")
        widest = 0
        for i in range(self.folder_tree.get_n_items()):
            row = self.folder_tree.get_item(i)
            layout.set_attributes(_BOLD if row.get_depth() == 0 else None)
            layout.set_text(row.get_item().name, -1)
            widest = max(widest, row.get_depth() * TREE_INDENT + layout.get_pixel_size()[0])
        return widest + TREE_ROW_EXTRA

    # ---------------------------------------------------------- list rows ---

    def _row_setup(self, factory, list_item):
        box = Gtk.Box(spacing=10)
        box.thumb = Gtk.Image(pixel_size=THUMB_SIZE)
        box.label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        box.tags = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["dim-label", "caption"])
        box.item = None
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        text.append(box.label)
        text.append(box.tags)
        box.append(box.thumb)
        box.append(text)
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
        caption = ", ".join(item.tags or [])
        if self.search:
            folder = GLib.filename_display_basename(os.path.dirname(item.path))
            caption = f"{folder} · {caption}" if caption else folder
        box.tags.set_label(caption)
        box.tags.set_visible(bool(caption))
        tooltip = GLib.filename_display_name(item.path) if self.search else item.display_name
        box.set_tooltip_text(tooltip + (f"\n{', '.join(item.tags)}" if item.tags else ""))
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
            box.thumb.set_from_icon_name("video-x-generic-symbolic" if is_video(item.path)
                                         else "audio-x-generic-symbolic" if is_audio(item.path)
                                         else "image-x-generic-symbolic")

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
        images = [p for p in paths if os.path.isfile(p) and is_media(os.path.basename(p))]
        if not images:
            self.toast("Only images, videos and music can be dropped onto a folder")
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
        manage = Gio.Menu()
        manage.append("Manage Pinned Folders…", "win.manage-folders")
        menu.append_section(None, manage)
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
        PinnedFoldersDialog(self.settings, browse=True).present(self)

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
            "open-with": self.on_open_with,
            "show-containing": lambda *_: show_containing_folder(self, self.current_path),
            "properties": self.on_properties,
            "find-tags": lambda *_: FindByTagDialog(self).present(self),
            "copy-to": lambda *_: self.on_transfer(move=False),
            "move-to": lambda *_: self.on_transfer(move=True),
            "rename": self.on_rename,
            "delete": self.on_delete,
            "select-all": lambda *_: self.selection.select_all(),
            "toggle-sidebar": lambda *_: self.split.set_show_sidebar(not self.split.get_show_sidebar()),
            "fullscreen": lambda *_: self.unfullscreen() if self.is_fullscreen() else self.fullscreen(),
            "add-folder": self.on_add_folder,
            "manage-folders": lambda *_: PinnedFoldersDialog(self.settings).present(self),
            "pin-current": lambda *_: self.pin_folders([self.folder]) or self.toast("Folder is already pinned"),
        }
        # Actions with a string parameter: a folder path, or for wallpaper a style
        folder_actions = {
            "folder-open": lambda a, p: self.open_path(p.get_string()),
            "wallpaper": lambda a, p: self.on_set_wallpaper(p.get_string()),
            "folder-show": lambda a, p: show_folder(self, p.get_string()),
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

        # Radio choices in the Images menu
        tag_filter = Gio.SimpleAction.new_stateful("tag-filter", GLib.VariantType.new("s"), GLib.Variant("s", ""))
        tag_filter.connect("change-state", lambda a, value: self.set_tag_filter(value.get_string()))
        self.add_action(tag_filter)
        sort_by = Gio.SimpleAction.new_stateful("sort-by", GLib.VariantType.new("s"),
                                                GLib.Variant("s", self.settings["sort_by"]))
        sort_by.connect("change-state", lambda a, value: self.settings.__setitem__("sort_by", value.get_string()))
        self.add_action(sort_by)

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
        click = Gtk.GestureClick()
        click.connect("pressed", lambda g, n, x, y: n == 2 and self.actions["fullscreen"].activate(None))
        self.video.add_controller(click)
        for widget in (self.video, self.music):
            context = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            context.connect("pressed", lambda g, n, x, y: self.current_path and self._popup(self.image_menu, x, y))
            widget.add_controller(context)

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
        elif name == "space" and self.stream:
            self.stream.set_playing(not self.stream.get_playing())
            return True
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
        dialog = Gtk.FileDialog(title="Open File", modal=True)
        image_filter = Gtk.FileFilter(name="Images, Videos and Music")
        image_filter.add_pixbuf_formats()
        image_filter.add_mime_type("video/*")
        image_filter.add_mime_type("audio/*")
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
        if self.search:
            self._leave_search()
        if folder != self.folder:
            self.folder = folder
            self.set_tag_filter("", rescan=False)
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
        if not self.folder and not self.search:
            return
        keep = [] if select_path else self.selected_paths()
        old_index = self.path_index.get(self.current_path, 0)
        if self.search:
            items = self._search_items()
            unknown = []
        else:
            items = scan_folder(self.folder, self.settings["sort_by"], self.settings["show_hidden"])
            known = self.tag_index.lookup(items)
            for item in items:
                item.tags = known.get(item.path)
            unknown = [item for item in items if item.tags is None]
        total = len(items)
        self.tags_loading = bool(unknown) and GExiv2 is not None
        if self.tags_loading:
            self.tag_index.load(unknown, self.rescan)
        elif self.tag_filter and not self._filter_matches_any(items):
            self.set_tag_filter("", rescan=False)  # its last image lost the tag, or moved away
        self._update_tag_menu(items)
        if self.tag_filter:
            items = [i for i in items if self._matches_filter(i)]
        if self.settings["sort_by"] == "tag":
            items = sort_by_tag(items)

        self.updating = True
        try:
            self.store.splice(0, self.store.get_n_items(), items)
        finally:
            self.updating = False
        self.path_index = {item.path: i for i, item in enumerate(items)}

        noun = "image" if self.search else "file"  # tag searches only find images
        count = f"{len(items)} of {plural(total, noun)}" if self.tag_filter else plural(total, noun)
        if self.search:
            joiner = " + " if self.search["all"] else " or "
            self.sidebar_title.set_title(joiner.join(self.search["tags"]))
            folders = len({os.path.dirname(item.path) for item in items})
            count += f" in {plural(folders, 'folder')}" + (" · indexing…" if self.library.running else "")
        else:
            self.sidebar_title.set_title(GLib.filename_display_basename(self.folder))
        self.sidebar_title.set_subtitle(count + (" · reading tags…" if self.tags_loading else ""))

        if not items:
            self.current_path = None
            self.view.set_texture(None)
            self.stop_media()
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

    # --------------------------------------------------------------- tags ---

    def on_properties(self, *_):
        paths = self.targets()
        if not paths:
            return
        known = {}
        for i in range(self.store.get_n_items()):
            item = self.store.get_item(i)
            known[item.path] = item.tags
        PropertiesDialog(paths, known, on_saved=self._on_tags_saved).present(self)

    def _on_tags_saved(self, saved):
        self.tag_index.store_paths(saved)
        if self.search:
            self.rescan()  # no folder monitor while showing search results

    # -------------------------------------------------------- tag search ---

    def show_tag_search(self, tags, match_all):
        """List every image in the pinned folders with any (or all) of tags."""
        if self.monitor:
            self.monitor.cancel()
            self.monitor = None
        self.search = {"tags": tags, "folded": [t.casefold() for t in tags], "all": match_all}
        self.folder = None
        self.current_path = None
        self.path_index = {}
        self.set_tag_filter("", rescan=False)
        self.search_close.set_visible(True)
        self._sync_folder_selection()
        self.rescan()

    def _leave_search(self):
        self.search = None
        self.search_close.set_visible(False)
        self.current_path = None
        self.path_index = {}

    def end_tag_search(self):
        self._leave_search()
        last = self.settings["last_folder"]
        if last and os.path.isdir(last):
            self.open_path(last)
            return
        self.updating = True
        try:
            self.store.remove_all()
        finally:
            self.updating = False
        self.sidebar_title.set_title(APP_NAME)
        self.sidebar_title.set_subtitle("")
        self.view.set_texture(None)
        self.stop_media()
        self.stack.set_visible_child_name("welcome")
        self.update_title()
        self.update_actions()

    def _search_items(self):
        rows = self.tag_index.search(self.search["folded"], self.search["all"],
                                     Library.roots(self.settings["folders"]))
        items = []
        for path, mtime, size, tags in rows:
            item = ImageItem(path, mtime, size)
            item.tags = tags
            items.append(item)
        if self.settings["sort_by"] == "date":
            items.sort(key=lambda i: (i.mtime, i.path))
        else:
            items.sort(key=lambda i: (GLib.utf8_collate_key_for_filename(i.display_name, -1), i.path))
        return items

    def _on_library_changed(self):
        if self.search and not self.search_source:  # show new matches as indexing finds them
            self.search_source = GLib.timeout_add(1500, self._rescan_search)

    def _rescan_search(self):
        self.search_source = 0
        if self.search:
            self.rescan()
        return False

    def _matches_filter(self, item):
        if self.tag_filter == UNTAGGED:
            return not item.tags
        wanted = self.tag_filter.casefold()
        return any(t.casefold() == wanted for t in item.tags or [])

    def _filter_matches_any(self, items):
        return any(self._matches_filter(i) for i in items)

    def set_tag_filter(self, value, rescan=True):
        self.tag_filter = value
        self.lookup_action("tag-filter").set_state(GLib.Variant("s", value))
        label = {"": "All", UNTAGGED: "Untagged"}.get(value, value)
        self.view_button.set_label(label)
        self.view_button.set_tooltip_text("Show and Sort Images" if not value else f"Showing: {label}")
        if rescan:
            self.rescan()

    def _update_tag_menu(self, items):
        """Rebuild the Images menu: All, Untagged and each tag in this folder with its count, then Sort By."""
        counts, names = {}, {}
        untagged = 0
        for item in items:
            if not item.tags:
                untagged += item.tags is not None
            for tag in {t.casefold(): t for t in item.tags or []}.items():
                counts[tag[0]] = counts.get(tag[0], 0) + 1
                names.setdefault(tag[0], tag[1])
        self.tag_menu.remove_all()
        show = Gio.Menu()
        show.append(f"All Images ({len(items)})", "win.tag-filter::")
        if counts and untagged:
            show.append_item(self._menu_item(f"Untagged ({untagged})", "tag-filter", UNTAGGED))
        for key in sorted(counts, key=lambda k: GLib.utf8_collate_key(k, -1)):
            show.append_item(self._menu_item(f"{names[key]} ({counts[key]})", "tag-filter", names[key]))
        self.tag_menu.append_section("Show", show)
        sort = Gio.Menu()
        for key, label in SORT_OPTIONS:
            sort.append_item(self._menu_item(label, "sort-by", key))
        self.tag_menu.append_section("Sort By", sort)

    @staticmethod
    def _menu_item(label, action, value):
        # Built with a Variant target, so tags with odd characters can't break a detailed action name
        item = Gio.MenuItem.new(label, None)
        item.set_action_and_target_value(f"win.{action}", GLib.Variant("s", value))
        return item

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
        self.stop_media()
        if is_video(path) or is_audio(path):
            self.view.set_texture(None)
            self.play_media(path)
            self.update_title()
            self.update_actions()
            return
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
                self.error_page.set_title("Could Not Load Image")
                self.error_page.set_description(GLib.markup_escape_text(message))
                self.stack.set_visible_child_name("error")
            else:
                self.view.set_texture(Gdk.Texture.new_for_pixbuf(pixbuf), keep_zoom=same)
                self.stack.set_visible_child_name("image")
            self.update_title()
            self.update_actions()

        run_in_thread(lambda: load_pixbuf(path), done)

    def play_media(self, path):
        stream = self.stream = Gtk.MediaFile.new_for_filename(path)
        stream.connect("notify::error", self._on_media_error)
        stream.connect("notify::prepared", lambda *_: self.update_title())
        if is_audio(path):
            stream.connect("notify::ended", self._on_song_ended)
            self.music_controls.set_media_stream(stream)
            self._show_song(path)
            self.stack.set_visible_child_name("music")
        else:
            self.video.set_media_stream(stream)
            self.stack.set_visible_child_name("video")
        stream.play()

    def _show_song(self, path):
        self.cover.set_from_icon_name("audio-x-generic-symbolic")
        self.song_title.set_label(os.path.splitext(GLib.filename_display_basename(path))[0])
        self.song_artist.set_visible(False)
        self.song_album.set_visible(False)

        def done(info, error):
            if path != self.current_path or error is not None:
                return
            if info.get("cover"):
                self.cover.set_from_paintable(Gdk.Texture.new_for_pixbuf(info["cover"]))
            if info.get("title"):
                self.song_title.set_label(info["title"])
            for label, key in ((self.song_artist, "artist"), (self.song_album, "album")):
                label.set_label(info.get(key, ""))
                label.set_visible(bool(info.get(key)))

        run_in_thread(lambda: song_info(path), done)

    def _on_song_ended(self, stream, _pspec):
        # Play on through the folder's songs, like an album
        if stream is not self.stream or not stream.get_ended():
            return
        stream_path = self.current_path
        index = self.path_index.get(stream_path)
        if index is not None and index + 1 < self.store.get_n_items() \
                and is_audio(self.store.get_item(index + 1).path):
            # Not from inside the ended signal: a stream started there doesn't play
            GLib.idle_add(lambda: self.current_path == stream_path and self.go(1) and False)

    def _on_media_error(self, stream, _pspec):
        if stream is not self.stream or stream.get_error() is None:
            return
        # GStreamer's message starts "Error from element /GstPlayBin:…: <reason>" and adds debug lines
        reason = stream.get_error().message.split("\n")[0].rpartition(": ")[2]
        self.error_page.set_title("Could Not Play Music" if is_audio(self.current_path or "")
                                  else "Could Not Play Video")
        self.error_page.set_description(GLib.markup_escape_text(reason))
        self.stack.set_visible_child_name("error")

    def stop_media(self):
        if self.stream:
            self.stream.pause()
            self.video.set_media_stream(None)
            self.music_controls.set_media_stream(None)
            self.stream = None

    def update_title(self):
        index = self.path_index.get(self.current_path)
        if index is None:
            self.title.set_title(APP_NAME)
            self.title.set_subtitle("")
            self.set_title(APP_NAME)
            return
        item = self.store.get_item(index)
        parts = [f"{index + 1} / {self.store.get_n_items()}"]
        stream = self.stream
        if self.view.texture is not None:
            parts.append(f"{self.view.texture.get_width()} × {self.view.texture.get_height()}")
        elif stream is not None and stream.is_prepared():
            if stream.get_intrinsic_width():
                parts.append(f"{stream.get_intrinsic_width()} × {stream.get_intrinsic_height()}")
            if stream.get_duration():
                parts.append(format_duration(stream.get_duration()))
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
            "open-with": self.current_path is not None,
            "show-containing": self.current_path is not None,
            "properties": self.current_path is not None,
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

    def on_open_with(self, *_):
        if not self.current_path:
            return
        if IN_SNAP:
            portal_open(self, self.current_path, on_error=lambda m: self.toast(f"Could not open: {m}"))
            return
        launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(self.current_path), always_ask=True)

        def done(launcher, result):
            try:
                launcher.launch_finish(result)
            except GLib.Error:
                pass

        launcher.launch(self, None, done)

    # ---------------------------------------------------- file operations ---

    def on_set_wallpaper(self, style):
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
            # A Snap's data dir changes with every revision; its common dir doesn't
            base = os.environ.get("SNAP_USER_COMMON") or GLib.get_user_data_dir()
            folder = os.path.join(base, "backgrounds")
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
            background.set_string("picture-options", style)
            Gio.Settings.sync()
            self.toast(f"Set as wallpaper ({WALLPAPER_STYLES[style]})")

        run_in_thread(work, done)

    def toast(self, title, timeout=3):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(title), timeout=timeout))

    def on_transfer(self, move):
        paths = self.targets()
        if not paths:
            return
        verb = "Move" if move else "Copy"
        dialog = Gtk.FileDialog(title=f"{verb} {plural(len(paths), 'File')} to Folder", modal=True,
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
            message = f"{'Moved' if move else 'Copied'} {plural(len(moved), 'file')} to “{folder_name}”"
            if renamed:
                message += f" ({renamed} renamed to avoid overwriting)"
            if errors:
                message += f" — {len(errors)} failed: {errors[0]}"
            toast = Adw.Toast(title=GLib.markup_escape_text(message), timeout=5,
                              button_label="Undo" if move and moved else "Show Folder")
            if move and moved:
                toast.connect("button-clicked", lambda *_: self._undo_move(moved))
            else:
                toast.connect("button-clicked", lambda *_: show_folder(self, dest_dir))
            self.toasts.add_toast(toast)
            if move:
                self.tag_index.move(moved)
                self.rescan()

        run_in_thread(work, finished)

    def _undo_move(self, moved):
        def work():
            restored = []
            for original, dest in moved:
                if os.path.lexists(original):
                    continue  # never overwrite something created since
                try:
                    Gio.File.new_for_path(dest).move(Gio.File.new_for_path(original),
                                                     Gio.FileCopyFlags.NOFOLLOW_SYMLINKS, None, None, None)
                    restored.append((dest, original))
                except GLib.Error:
                    pass
            return restored

        def finished(restored, error):
            self.tag_index.move(restored or [])
            self.toast(f"Moved {plural(len(restored or []), 'file')} back")
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
            self.tag_index.move([(path, new_path)])
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
                    else plural(len(paths), "file"))
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
            errors = []  # (message, trash not supported)
            gone = []
            for path in paths:
                file = Gio.File.new_for_path(path)
                try:
                    if use_trash:
                        try:
                            file.trash(None)
                        except GLib.Error:
                            if not IN_SNAP:
                                raise
                            trash_on_drive(path)
                    else:
                        file.delete(None)
                    gone.append(path)
                except GLib.Error as e:
                    errors.append((e.message, e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_SUPPORTED)))
                except OSError as e:
                    errors.append((e.strerror or str(e), False))
            return errors, gone

        def finished(result, error):
            errors, gone = result or ([], [])
            self.tag_index.remove(gone)
            removed = len(paths) - len(errors)
            verb = "Moved" if use_trash else "Deleted"
            message = f"{verb} {plural(removed, 'file')}" + (" to the trash" if use_trash else "")
            if error is not None:
                message = f"Delete failed: {error}"
            elif errors:
                reason, not_supported = errors[0]
                if removed:
                    message += f" — {len(errors)} failed: {reason}"
                else:
                    message = f"Couldn't {'move to the trash' if use_trash else 'delete'}: {reason}"
                if use_trash and not_supported:
                    message += " (turn off “Move to Trash” in Preferences to delete permanently)"
            self.toast(message, timeout=5)
            self.rescan()

        run_in_thread(work, finished)

    # -------------------------------------------------------------- misc ---

    def _on_settings_changed(self, settings, key):
        if key == "sort_by":
            self.lookup_action("sort-by").set_state(GLib.Variant("s", settings["sort_by"]))
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
        self.stop_media()
        if self.monitor:
            self.monitor.cancel()
        self.settings.disconnect(self.settings_handler)
        self.library.listeners.remove(self._on_library_changed)
        if self.search_source:
            GLib.source_remove(self.search_source)
        return False


# ----------------------------------------------------------------------------
# Application
# ----------------------------------------------------------------------------

class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.settings = None
        self.thumbs = None
        self.tag_index = None
        self.library = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)
        self.settings = Settings()
        self.thumbs = ThumbnailCache()
        self.tag_index = TagIndex()
        self.library = Library(self.tag_index)
        # Running from the source folder: find the app icon in ./icons
        icons = os.path.join(os.path.dirname(os.path.realpath(__file__)), "icons")
        if os.path.isdir(icons):
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(icons)
        Gtk.Window.set_default_icon_name(APP_ID)
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
            "win.copy-to": ["<Control><Shift>c"],
            "win.move-to": ["<Control><Shift>m"],
            "win.rename": ["F2"],
            "win.properties": ["<Alt>Return", "<Alt>KP_Enter"],
            "win.find-tags": ["<Control>f"],
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
        if self.tag_index:
            self.library.generation += 1  # stops a running index pass
            self.tag_index.close()
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
        about = Adw.AboutDialog(application_name=APP_NAME, application_icon=APP_ID,
                                version=VERSION, comments="View and organise your images, videos and music",
                                license_type=Gtk.License.GPL_3_0, developer_name="Salvatore Turone",
                                copyright="© 2026 Salvatore Turone",
                                website="https://github.com/pst007xx/glimpse-media-viewer",
                                issue_url="https://github.com/pst007xx/glimpse-media-viewer/issues")
        about.present(self.get_active_window())


def main():
    return Application().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
