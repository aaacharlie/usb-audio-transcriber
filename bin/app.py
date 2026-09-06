#!/usr/bin/env python3
"""The desktop window: a native GTK 4 / libadwaita app over the pipeline.

    app.py                       make sure the panel server is running, then
                                 open the window
    app.py --page settings       open on a given page (home, sessions, recordings,
                                 search, settings, tools)
    app.py --connect URL --token-file PATH
                                 (internal) run the window against a running server

The window is a client of the same local API the web panel uses (panel.py),
so every button maps to the same script and the two can be used
interchangeably. It runs with a Python that has the GTK bindings
(python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 on Debian/Ubuntu), which is usually
the system's python3 rather than the pipeline's virtual environment; app.py
finds one itself and falls back to the web page when there is none.
"""
import argparse
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

APP_ID = "io.github.aaacharlie.UsbAudioTranscriber"
TITLE = "USB Audio Transcriber"
APP_ICON_FILE = Path(__file__).resolve().parent.parent / "share" / f"{APP_ID}.svg"
BACKENDS = [("", "Configured backend"), ("command", "Command-line tool (Codex, Claude Code, ...)"),
            ("openai", "Local model (Ollama)"), ("openrouter", "OpenRouter")]
PROFILES = ["fast", "accurate", "both"]
GI_HINT = ("The desktop window needs the GTK bindings: sudo apt install python3-gi "
           "gir1.2-gtk-4.0 gir1.2-adw-1  (opening the web panel instead)")

CSS = """
.pill { border-radius: 999px; padding: 1px 9px; font-size: 0.85em; font-weight: 600;
        background: alpha(@accent_bg_color, 0.16); color: @accent_color; }
.pill.ok { background: alpha(@success_color, 0.18); color: @success_color; }
.pill.warn { background: alpha(@warning_color, 0.18); color: @warning_color; }
.pill.bad { background: alpha(@error_color, 0.18); color: @error_color; }
.statusbar { padding: 6px 14px; border-top: 1px solid alpha(currentColor, 0.12); font-size: 0.92em; }
.output { font-family: monospace; font-size: 0.88em; padding: 8px; }
.note { padding: 10px 14px; }
.big { font-size: 1.9em; font-weight: 700; }
.hint { font-size: 0.9em; opacity: 0.75; }
.failure { color: @error_color; }
.page-title { font-size: 1.45em; font-weight: 800; color: @accent_color; }
.hero { padding: 18px 22px; border-radius: 16px; background: alpha(@accent_bg_color, 0.12); }
.hero-title { font-size: 1.55em; font-weight: 700; }
.dot { border-radius: 999px; min-width: 10px; min-height: 10px; background: alpha(currentColor, 0.35); }
.dot.ok { background: @success_color; }
.dot.busy { background: @accent_bg_color; }
.dot.warn { background: @warning_color; }
.dot.bad { background: @error_color; }
"""


class ApiError(Exception):
    """A request the panel refused or could not serve."""


class Api:
    """The panel's JSON API. Every call blocks; the window runs them in threads."""

    def __init__(self, base_url, token, verbose=False):
        self.base = base_url.rstrip("/")
        self.token = token
        self.verbose = verbose

    def request(self, path, params=None, body=None):
        if self.verbose:
            print(f"api {'POST' if body is not None else 'GET'} {path} {params or ''} {body or ''}",
                  file=sys.stderr, flush=True)
        url = self.base + path
        if params:
            query = {key: value for key, value in params.items() if value not in (None, "")}
            if query:
                url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            url, data=data, method="POST" if data is not None else "GET",
            headers={"X-Panel-Token": self.token, "X-Requested-With": "panel",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=120) as reply:
                text = reply.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            payload = error.read().decode("utf-8", "replace")
            try:
                detail = json.loads(payload)
            except json.JSONDecodeError:
                detail = {}
            if not isinstance(detail, dict):
                detail = {}
            if error.code == 400 and detail.get("failures"):
                return detail  # the settings form shows these
            raise ApiError(detail.get("error") or f"{error.code} {error.reason}") from None
        except (urllib.error.URLError, OSError) as error:
            raise ApiError(f"the panel is not answering ({error})") from None
        return json.loads(text) if text.strip() else None


# --------------------------------------------------------------------------- pure helpers

def when(iso):
    """2026-09-05T09:30:00 -> 2026-09-05 09:30"""
    return (iso or "").replace("T", " ")[:16]


def stamp(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def unit_pill(unit):
    """(text, css class) for a systemd unit's state as the API reports it."""
    if not unit:
        return "no systemd", ""
    active = unit.get("active", "unknown")
    return active, "ok" if active in ("active", "activating") else "warn"


def headline(status, running_label=None, unreachable=False):
    """(text, kind) for the big line on Home and the status bar; kind is ok, busy, warn, bad, or ""."""
    if unreachable:
        return "The panel is not answering", "bad"
    if not status:
        return "Connecting", ""
    progress = status.get("progress") or {}
    if progress.get("active"):
        percent = progress.get("current_percent")
        text = progress.get("phase") or "Working"
        return (text + (f" {percent}%" if percent is not None else "")), "busy"
    if running_label:
        return running_label, "busy"
    timer = (status.get("units") or {}).get("timer")
    if status.get("systemd") and timer and timer.get("active") != "active":
        return "Automatic runs paused", "warn"
    return "Idle, watching for the recorder", "ok"


def note_label(hit):
    note = hit.get("note") or ""
    if note:
        return Path(note).stem
    return Path(hit.get("audio") or "").name


def inline_runs(text):
    """Split a line into (kind, text) runs: text, bold, link (a wikilink target), code."""
    runs = []
    pattern = re.compile(r"\*\*(.+?)\*\*|\[\[([^\]]+)\]\]|`([^`]+)`")
    position = 0
    for match in pattern.finditer(text):
        if match.start() > position:
            runs.append(("text", text[position:match.start()]))
        if match.group(1) is not None:
            runs.append(("bold", match.group(1)))
        elif match.group(2) is not None:
            runs.append(("link", match.group(2)))
        else:
            runs.append(("code", match.group(3)))
        position = match.end()
    if position < len(text):
        runs.append(("text", text[position:]))
    return runs


def parse_note(text):
    """A note as blocks: ("meta", [(key, value)]), ("heading", level, runs),
    ("item", runs), ("quote", runs), ("rule",), ("paragraph", runs)."""
    lines = text.split("\n")
    blocks = []
    index = 0
    if lines and lines[0].strip() == "---":
        meta = []
        index = 1
        while index < len(lines) and lines[index].strip() != "---":
            key, _, value = lines[index].partition(":")
            meta.append((key.strip(), value.strip()))
            index += 1
        index += 1
        blocks.append(("meta", meta))
    for line in lines[index:]:
        heading = re.match(r"^(#{1,3})\s+(.*)", line)
        if heading:
            blocks.append(("heading", len(heading.group(1)), inline_runs(heading.group(2))))
        elif line.startswith("- "):
            blocks.append(("item", inline_runs(line[2:])))
        elif line.startswith("> "):
            blocks.append(("quote", inline_runs(line[2:])))
        elif line.strip() == "---":
            blocks.append(("rule",))
        elif line.strip():
            blocks.append(("paragraph", inline_runs(line)))
    return blocks


def highlight_markup(text, escape):
    """Search hits mark matched words with [brackets]; show them bold."""
    return re.sub(r"\[([^\]]+)\]", lambda m: "<b>" + escape(m.group(1)) + "</b>",
                  escape(text).replace("&#91;", "[").replace("&#93;", "]"))


# --------------------------------------------------------------------------- the window

def run_gui(base_url, token, page=None, verbose=False, on_ready=None):
    """Run the window. `on_ready(window)` is called once it is on screen (for scripted checks)."""
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

    api = Api(base_url, token, verbose)

    def call(work, done):
        """Run `work()` in a thread; `done(result, error)` runs on the main loop."""
        def runner():
            try:
                result, error = work(), None
            except Exception as exc:  # noqa: BLE001 - surfaced to the person
                result, error = None, exc
                if verbose:
                    print(f"api error: {exc}", file=sys.stderr, flush=True)
            GLib.idle_add(lambda: (done(result, error), False)[1])
        threading.Thread(target=runner, daemon=True).start()

    def pill(text, kind=""):
        label = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
        label.add_css_class("pill")
        if kind:
            label.add_css_class(kind)
        return label

    def icon_image(name):
        """A symbolic icon, or None when the theme lacks it (then the row has no picture)."""
        display = Gdk.Display.get_default()
        if not name or display is None or not Gtk.IconTheme.get_for_display(display).has_icon(name):
            return None
        image = Gtk.Image.new_from_icon_name(name)
        image.add_css_class("dim-label")
        return image

    def value_row(title, subtitle=None, icon=None):
        row = Adw.ActionRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        picture = icon_image(icon)
        if picture is not None:
            row.add_prefix(picture)
        value = Gtk.Label(valign=Gtk.Align.CENTER, xalign=1, ellipsize=Pango.EllipsizeMode.MIDDLE,
                          max_width_chars=60)
        value.add_css_class("dim-label")
        row.add_suffix(value)
        return row, value

    def button(label, callback=None, kind=None):
        widget = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        if kind:
            widget.add_css_class(kind)
        if callback:
            widget.connect("clicked", lambda *_: callback())
        return widget

    def clear(container):
        child = container.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            container.remove(child)
            child = following

    def scroll_to(scrolled, widget):
        """Scroll a page so that `widget` is at the top of the view."""
        ok, bounds = widget.compute_bounds(scrolled.get_child())
        if not ok:
            return False
        adjustment = scrolled.get_vadjustment()
        adjustment.set_value(min(bounds.get_y() - 12, adjustment.get_upper() - adjustment.get_page_size()))
        return False

    def page_box():
        """A scrolling page with a comfortable maximum width."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22, hexpand=True,
                      margin_top=22, margin_bottom=30, margin_start=28, margin_end=28)
        clamp = Adw.Clamp(maximum_size=1080, tightening_threshold=1000, child=box, hexpand=True)
        if hasattr(Adw, "LengthUnit"):  # 1.4+: sizes default to scalable points; pixels are predictable
            clamp.set_unit(Adw.LengthUnit.PX)
        scrolled = Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vexpand=True, hexpand=True)
        return scrolled, box

    def group(title, description=None):
        widget = Adw.PreferencesGroup(title=title)
        if description:
            widget.set_description(description)
        return widget

    def output_view():
        view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                            wrap_mode=Gtk.WrapMode.WORD_CHAR)
        view.add_css_class("output")
        scrolled = Gtk.ScrolledWindow(child=view, min_content_height=90, max_content_height=340,
                                      propagate_natural_height=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        return scrolled, view.get_buffer()

    class JobsView:
        """Jobs as expandable rows, newest first, updated in place while they run."""

        def __init__(self, title, limit=None, empty="Nothing running. The result of every button appears here, while it runs."):
            self.limit = limit
            self.rows = {}
            self.group = group(title)
            self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.list.add_css_class("boxed-list")
            self.empty = Gtk.Label(label=empty, xalign=0, wrap=True)
            self.empty.add_css_class("hint")
            self.group.add(self.empty)
            self.group.add(self.list)
            self.list.set_visible(False)

        def update(self, jobs):
            visible = jobs[:self.limit] if self.limit else jobs
            wanted = {job["id"] for job in visible}
            for ident in list(self.rows):
                if ident not in wanted:
                    self.list.remove(self.rows.pop(ident)["row"])
            for job in reversed(visible):  # oldest first so prepend leaves the newest on top
                if job["id"] not in self.rows:
                    self.rows[job["id"]] = self.make_row(job)
                    self.list.prepend(self.rows[job["id"]]["row"])
                self.fill(self.rows[job["id"]], job)
            newest = visible[0]["id"] if visible else None
            for ident, parts in self.rows.items():
                parts["row"].set_expanded(ident == newest)
            self.empty.set_visible(not visible)
            self.list.set_visible(bool(visible))

        def make_row(self, job):
            row = Adw.ExpanderRow(title=job["label"])
            badge = pill("running")
            row.add_prefix(badge)
            scrolled, buffer = output_view()
            row.add_row(scrolled)
            return {"row": row, "badge": badge, "buffer": buffer, "text": None}

        def fill(self, parts, job):
            status = job["status"]
            kind = {"done": "ok", "failed": "bad"}.get(status, "")
            parts["badge"].set_text(status)
            for css in ("ok", "bad"):
                parts["badge"].remove_css_class(css)
            if kind:
                parts["badge"].add_css_class(kind)
            parts["row"].set_subtitle(when(job.get("started")))
            text = job.get("output") or ("Running..." if status == "running" else "(no output)")
            if text != parts["text"]:
                parts["buffer"].set_text(text)
                parts["text"] = text

    class NoteWindow(Adw.Window):
        """A note, rendered; wikilinks open the linked note in the same window."""

        def __init__(self, parent, note):
            super().__init__(transient_for=parent, default_width=780, default_height=720,
                             title=Path(note["path"]).name)
            self.parent = parent
            self.path = note["path"]
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            header = Adw.HeaderBar()
            header.pack_start(button("Open in your editor", lambda: parent.open_path(self.path)))
            box.append(header)
            self.view = Gtk.TextView(editable=False, cursor_visible=False,
                                     wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                     left_margin=20, right_margin=20, top_margin=14, bottom_margin=20)
            self.view.add_css_class("note")
            box.append(Gtk.ScrolledWindow(child=self.view, vexpand=True))
            self.set_content(box)
            click = Gtk.GestureClick()
            click.connect("released", self.on_click)
            self.view.add_controller(click)
            self.tags = {}
            self.render(note["text"])

        def render(self, text):
            buffer = self.view.get_buffer()
            buffer.set_text("")
            table = buffer.get_tag_table()
            self.tags = {}
            for name, props in (("h1", {"weight": Pango.Weight.BOLD, "scale": 1.6}),
                                ("h2", {"weight": Pango.Weight.BOLD, "scale": 1.3}),
                                ("h3", {"weight": Pango.Weight.BOLD, "scale": 1.1}),
                                ("bold", {"weight": Pango.Weight.BOLD}),
                                ("code", {"family": "monospace"}),
                                ("quote", {"style": Pango.Style.ITALIC, "left-margin": 30}),
                                ("meta", {"family": "monospace", "scale": 0.85}),
                                ("link", {"underline": Pango.Underline.SINGLE,
                                          "foreground": "#3584e4"})):
                tag = Gtk.TextTag(name=name)
                for key, value in props.items():
                    tag.set_property(key, value)
                table.add(tag)
                self.tags[name] = tag
            self.links = []
            end = buffer.get_end_iter
            for block in parse_note(text):
                kind = block[0]
                if kind == "meta":
                    for key, value in block[1]:
                        buffer.insert_with_tags(end(), f"{key}: {value}\n", self.tags["meta"])
                    buffer.insert(end(), "\n")
                elif kind == "heading":
                    self.insert_runs(buffer, block[2], self.tags[f"h{block[1]}"])
                    buffer.insert(end(), "\n\n")
                elif kind == "item":
                    buffer.insert(end(), "  • ")
                    self.insert_runs(buffer, block[1])
                    buffer.insert(end(), "\n")
                elif kind == "quote":
                    self.insert_runs(buffer, block[1], self.tags["quote"])
                    buffer.insert(end(), "\n\n")
                elif kind == "rule":
                    buffer.insert(end(), "─" * 30 + "\n\n")
                else:
                    self.insert_runs(buffer, block[1])
                    buffer.insert(end(), "\n\n")

        def insert_runs(self, buffer, runs, base=None):
            for kind, text in runs:
                tags = [base] if base else []
                if kind == "bold":
                    tags.append(self.tags["bold"])
                elif kind == "code":
                    tags.append(self.tags["code"])
                elif kind == "link":
                    start = buffer.get_end_iter().get_offset()
                    tags.append(self.tags["link"])
                    self.links.append((start, start + len(text), text))
                buffer.insert_with_tags(buffer.get_end_iter(), text, *tags)

        def on_click(self, gesture, n_press, x, y):
            bx, by = self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
            hit, iterator = self.view.get_iter_at_location(bx, by)
            if not hit:
                return
            offset = iterator.get_offset()
            for start, end, target in self.links:
                if start <= offset < end:
                    self.parent.view_note(self.parent.vault_path(target + ".md"), self)
                    return

        def show_note(self, note):
            self.path = note["path"]
            self.set_title(Path(note["path"]).name)
            self.render(note["text"])

    class Window(Adw.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title=TITLE, default_width=1120, default_height=780)
            self.status = None
            self.jobs = []
            self.watching = set()
            self.announced = set()
            self.polling = False
            self.sessions = []
            self.settings = None
            self.fields = {}
            self.entries = {}
            self.note_window = None
            self.pages = {}
            self.failures = 0
            self.status_pending = False
            self.build()
            if page:
                self.show_page(page)
            self.refresh_status()
            self.load_recordings()
            self.poll_jobs()
            GLib.timeout_add_seconds(4, self.tick)

        # ------------------------------------------------------------ layout
        def build(self):
            self.toasts = Adw.ToastOverlay()
            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.stack = Adw.ViewStack()
            header = Adw.HeaderBar()
            title = Adw.ViewSwitcherTitle(stack=self.stack, title=TITLE)
            header.set_title_widget(title)
            root.append(header)
            root.append(self.stack)
            self.stack.set_vexpand(True)
            bar = Adw.ViewSwitcherBar(stack=self.stack)
            title.bind_property("title-visible", bar, "reveal", GObject.BindingFlags.SYNC_CREATE)
            root.append(bar)
            status_box = Gtk.Box(spacing=8)
            status_box.add_css_class("statusbar")
            self.dot = Gtk.Box(valign=Gtk.Align.CENTER)
            self.dot.add_css_class("dot")
            self.spinner = Gtk.Spinner()
            self.status_label = Gtk.Label(label="Connecting", xalign=0, hexpand=True)
            self.version_label = Gtk.Label(label="", xalign=1)
            self.version_label.add_css_class("dim-label")
            status_box.append(self.dot)
            status_box.append(self.spinner)
            status_box.append(self.status_label)
            status_box.append(self.version_label)
            root.append(status_box)
            self.toasts.set_child(root)
            self.set_content(self.toasts)
            for name, label, icon, builder in (
                    ("home", "Home", "go-home-symbolic", self.build_home),
                    ("sessions", "Sessions", "view-list-symbolic", self.build_sessions),
                    ("recordings", "Recordings", "audio-input-microphone-symbolic", self.build_recordings),
                    ("search", "Search", "edit-find-symbolic", self.build_search),
                    ("settings", "Settings", "emblem-system-symbolic", self.build_settings),
                    ("tools", "Tools", "applications-utilities-symbolic", self.build_tools)):
                page = self.stack.add_titled(builder(), name, label)
                page.set_icon_name(icon)
            self.stack.connect("notify::visible-child-name", self.on_page_changed)

        def on_page_changed(self, *_):
            name = self.stack.get_visible_child_name()
            if name == "sessions":
                self.load_sessions()
            elif name == "recordings":
                self.load_recordings()
            elif name == "settings" and self.settings is None:
                self.load_settings()

        def show_page(self, name):
            self.stack.set_visible_child_name(name)

        def toast(self, text):
            self.toasts.add_toast(Adw.Toast.new(text))

        def set_state(self, text, kind):
            """The status bar and the Home headline say the same thing."""
            self.status_label.set_text(text)
            self.hero_title.set_text(text)
            for css in ("ok", "busy", "warn", "bad"):
                self.dot.remove_css_class(css)
            if kind:
                self.dot.add_css_class(kind)
            self.spinner.set_visible(kind == "busy")
            if kind == "busy":
                self.spinner.start()
            else:
                self.spinner.stop()

        # ------------------------------------------------------------ home
        def build_home(self):
            scrolled, box = page_box()
            hero = Gtk.Box(spacing=20)
            hero.add_css_class("hero")
            if APP_ICON_FILE.is_file():
                logo = Gtk.Image.new_from_file(str(APP_ICON_FILE))
            else:
                logo = Gtk.Image.new_from_icon_name(APP_ID)
            logo.set_pixel_size(88)
            logo.set_valign(Gtk.Align.CENTER)
            hero.append(logo)
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER,
                            hexpand=True)
            self.hero_title = Gtk.Label(label="Connecting", xalign=0, wrap=True)
            self.hero_title.add_css_class("hero-title")
            self.hero_detail = Gtk.Label(label=TITLE, xalign=0, wrap=True)
            self.hero_detail.add_css_class("dim-label")
            self.hero_progress = Gtk.ProgressBar(visible=False, margin_top=6)
            words.append(self.hero_title)
            words.append(self.hero_detail)
            words.append(self.hero_progress)
            hero.append(words)
            actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
            actions.append(button("Run a cycle now", lambda: self.start_job("cycle", {}), "suggested-action"))
            self.pause_button = button("Pause automatic runs", self.toggle_timer)
            actions.append(self.pause_button)
            hero.append(actions)
            box.append(hero)

            pipeline = group("Pipeline")
            self.home = {}
            for key, title, icon in (("timer", "Timer", "alarm-symbolic"),
                                     ("plug", "Plug-in trigger", "drive-removable-media-symbolic"),
                                     ("activity", "Last activity", "document-open-recent-symbolic"),
                                     ("phase", "Phase", "system-run-symbolic"),
                                     ("queued", "Queued", "view-list-symbolic"),
                                     ("detected", "On the recorder", "audio-input-microphone-symbolic")):
                row, value = value_row(title, icon=icon)
                self.home[key] = value
                pipeline.add(row)
            box.append(pipeline)

            library = group("Library")
            for key, title, icon in (("recordings", "Recordings", "folder-music-symbolic"),
                                     ("sessions", "Sessions", "emblem-documents-symbolic"),
                                     ("summarized", "Sessions with a summary", "starred-symbolic")):
                row, value = value_row(title, icon=icon)
                self.home[key] = value
                library.add(row)
            box.append(library)

            summaries = group("AI summaries")
            suffix = Gtk.Box(spacing=8)
            suffix.append(button("Test", lambda: self.start_job("test-backend", {})))
            suffix.append(button("Change", lambda: self.show_page("settings")))
            summaries.set_header_suffix(suffix)
            for key, title, icon in (("backend", "Backend", "document-edit-symbolic"),
                                     ("subject", "Subject", "dialog-information-symbolic")):
                row, value = value_row(title, icon=icon)
                self.home[key] = value
                summaries.add(row)
            box.append(summaries)

            whisper = group("Whisper")
            for key, title, icon in (("profile", "Profile", "preferences-system-symbolic"),
                                     ("fast", "fast (distil-large-v3)", "folder-download-symbolic"),
                                     ("accurate", "accurate (large-v3)", "folder-download-symbolic"),
                                     ("disk", "Disk free", "drive-harddisk-symbolic")):
                row, value = value_row(title, icon=icon)
                self.home[key] = value
                whisper.add(row)
            box.append(whisper)

            notes = group("Notes")
            self.vault_row = Adw.ActionRow(title="Folder", subtitle="")
            folder_icon = icon_image("folder-symbolic")
            if folder_icon is not None:
                self.vault_row.add_prefix(folder_icon)
            self.vault_row.add_suffix(button("Open folder", lambda: self.open_path(self.vault_path(""))))
            notes.add(self.vault_row)
            box.append(notes)

            self.home_recordings = group("Recent recordings")
            self.home_recordings_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.home_recordings_list.add_css_class("boxed-list")
            self.home_recordings.add(self.home_recordings_list)
            box.append(self.home_recordings)

            self.jobs_home = JobsView("Activity")
            box.append(self.jobs_home.group)
            self.pages["home"] = (scrolled, self.jobs_home.group)
            return scrolled

        def render_home(self):
            s = self.status
            if not s:
                return
            p = s.get("progress") or {}
            for key in ("timer", "plug"):
                text, kind = unit_pill((s.get("units") or {}).get(key))
                self.set_pill_value(self.home[key], text, kind)
            self.home["activity"].set_text(when(p.get("updated_at")) or "never")
            self.home["phase"].set_text(p.get("phase") or "idle")
            self.home["queued"].set_text(str(s.get("queued", 0)))
            detected = s.get("detected")
            self.home["detected"].set_text("checking" if detected is None else f"{detected} file(s)")
            counts = s.get("counts") or {}
            self.hero_detail.set_text(
                f"Last activity {when(p.get('updated_at')) or 'never'}  ·  "
                f"{counts.get('recordings', 0)} recordings  ·  {counts.get('sessions', 0)} sessions  ·  "
                f"{s.get('queued', 0)} queued")
            percent = p.get("current_percent") if p.get("active") else None
            self.hero_progress.set_visible(percent is not None)
            if percent is not None:
                self.hero_progress.set_fraction(max(0.0, min(1.0, float(percent) / 100.0)))
            for key in ("recordings", "sessions", "summarized"):
                self.home[key].set_text(str(counts.get(key, 0)))
            summaries = s.get("summaries") or {}
            self.set_pill_value(self.home["backend"], f"{summaries.get('backend') or 'none'}",
                                "ok" if summaries.get("ready") else "warn",
                                "ready" if summaries.get("ready") else "off")
            self.home["subject"].set_text(summaries.get("subject") or "not set")
            self.home["profile"].set_text(s.get("profile") or "fast")
            for model in s.get("models") or []:
                if model["key"] in self.home:
                    self.home[model["key"]].set_text(
                        f"{model['gib']} GiB cached" if model["cached"] else "not downloaded")
            disk = s.get("disk")
            self.home["disk"].set_text(f"{disk['free_gib']} of {disk['total_gib']} GiB" if disk else "?")
            self.vault_row.set_subtitle(s.get("vault") or "")
            self.version_label.set_text(f"control panel {s.get('version', '')}")
            timer = (s.get("units") or {}).get("timer")
            paused = bool(timer) and timer.get("active") != "active"
            self.pause_button.set_label("Resume automatic runs" if paused else "Pause automatic runs")
            self.pause_button.set_sensitive(bool(s.get("systemd")))

        def set_pill_value(self, label, text, kind, tag=None):
            """A value label that reads like a badge: text plus a coloured state word."""
            label.set_text(text if tag is None else f"{text}  ·  {tag}")
            for css in ("success", "warning", "error"):
                label.remove_css_class(css)
            css = {"ok": "success", "warn": "warning", "bad": "error"}.get(kind)
            if css:
                label.add_css_class(css)

        # ------------------------------------------------------------ status and jobs
        def tick(self):
            self.refresh_status()
            return True

        def refresh_status(self):
            if self.status_pending:
                return  # the last request is still out; never pile up
            self.status_pending = True
            call(lambda: api.request("/api/status"), self.on_status)

        def on_status(self, status, error):
            self.status_pending = False
            if error:
                self.failures += 1
                if self.failures >= 2:  # one slow answer is not an outage
                    self.set_state(*headline(self.status, unreachable=True))
                    self.hero_detail.set_text(str(error))
                return
            self.failures = 0
            self.status = status
            running = next((j for j in self.jobs if j["status"] == "running"), None)
            self.set_state(*headline(status, running["label"] if running else None))
            self.render_home()
            self.fill_backend_picker()

        def start_job(self, kind, params):
            def done(job, error):
                if error:
                    self.toast(str(error))
                    return
                self.watching.add(job["id"])
                self.toast("Started: " + job["label"])
                self.poll_jobs()
                page = self.pages.get(self.stack.get_visible_child_name())
                if page:
                    page[1].set_visible(True)
                    GLib.timeout_add(350, lambda: scroll_to(*page))
            call(lambda: api.request("/api/jobs", body={"kind": kind, "params": params}), done)

        def poll_jobs(self):
            if self.polling:
                return
            self.polling = True
            call(lambda: api.request("/api/jobs"), self.on_jobs)

        def on_jobs(self, jobs, error):
            self.polling = False
            if error:
                return
            self.jobs = jobs or []
            for view in (self.jobs_home, self.jobs_tools, self.jobs_sessions, self.jobs_settings):
                view.update(self.jobs)
            for job in self.jobs:
                if job["status"] != "running" and job["id"] in self.watching and job["id"] not in self.announced:
                    self.announced.add(job["id"])
                    self.toast(job["label"] + (": done" if job["status"] == "done" else ": failed, see the output below"))
            running = next((j for j in self.jobs if j["status"] == "running"), None)
            if running:
                if self.status and not (self.status.get("progress") or {}).get("active"):
                    self.set_state(*headline(self.status, running["label"]))
                GLib.timeout_add(2000, lambda: (self.poll_jobs(), False)[1])
            else:
                if self.stack.get_visible_child_name() == "sessions":
                    self.load_sessions()
                self.refresh_status()

        def toggle_timer(self):
            timer = ((self.status or {}).get("units") or {}).get("timer")
            action = "pause" if timer and timer.get("active") == "active" else "resume"
            self.start_job("timer", {"action": action})

        # ------------------------------------------------------------ sessions
        def build_sessions(self):
            scrolled, box = page_box()
            toolbar = Gtk.Box(spacing=8)
            heading = Gtk.Label(label="Sessions", xalign=0, hexpand=True)
            heading.add_css_class("page-title")
            toolbar.append(heading)
            self.backend_picker = Gtk.DropDown.new_from_strings([label for _, label in BACKENDS])
            self.backend_picker.set_valign(Gtk.Align.CENTER)
            toolbar.append(self.backend_picker)
            toolbar.append(button("Summarize all without a summary",
                                  lambda: self.start_job("retry", {"backend": self.backend_choice()})))
            toolbar.append(button("Summarize selected", self.summarize_selected, "suggested-action"))
            box.append(toolbar)
            top = group("", "A session is one sitting: recordings less than a few minutes apart, "
                            "stitched into one note. Summarizing sends that note's combined transcript "
                            "to the backend you pick.")
            self.sessions_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.sessions_list.add_css_class("boxed-list")
            self.sessions_empty = Adw.StatusPage(icon_name="view-list-symbolic", title="No sessions yet",
                                                 description="They appear after the first transcribed recordings.")
            top.add(self.sessions_empty)
            top.add(self.sessions_list)
            box.append(top)
            self.jobs_sessions = JobsView("Latest action", limit=1, empty="")
            self.jobs_sessions.group.set_visible(False)
            box.append(self.jobs_sessions.group)
            self.session_checks = {}
            self.pages["sessions"] = (scrolled, self.jobs_sessions.group)
            return scrolled

        def fill_backend_picker(self):
            configured = ((self.status or {}).get("summaries") or {}).get("backend") or "none"
            model = self.backend_picker.get_model()
            if model.get_string(0) != f"Configured backend ({configured})":
                selected = self.backend_picker.get_selected()
                strings = [f"Configured backend ({configured})"] + [label for _, label in BACKENDS[1:]]
                self.backend_picker.set_model(Gtk.StringList.new(strings))
                self.backend_picker.set_selected(selected)

        def backend_choice(self):
            return BACKENDS[self.backend_picker.get_selected()][0]

        def load_sessions(self):
            def done(rows, error):
                if error:
                    self.toast(str(error))
                    return
                self.sessions = rows or []
                self.render_sessions()
            call(lambda: api.request("/api/sessions"), done)

        def render_sessions(self):
            clear(self.sessions_list)
            self.session_checks = {}
            self.sessions_empty.set_visible(not self.sessions)
            self.sessions_list.set_visible(bool(self.sessions))
            for session in self.sessions:
                ended = (session.get("ended_at") or "")[11:16]
                row = Adw.ActionRow(title=f"{when(session['started_at'])} to {ended}",
                                    subtitle=session.get("note_name") or "")
                check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
                self.session_checks[session["id"]] = check
                row.add_prefix(check)
                count = Gtk.Label(label=f"{session['recordings']} recording(s)", valign=Gtk.Align.CENTER)
                count.add_css_class("dim-label")
                row.add_suffix(count)
                if session.get("summarized"):
                    row.add_suffix(pill(f"summary: {session.get('summary_model') or 'yes'}", "ok"))
                else:
                    row.add_suffix(pill("no summary yet"))
                view = button("View", lambda s=session: self.view_note(s["note"]))
                view.set_sensitive(bool(session.get("note_exists")))
                row.add_suffix(view)
                row.add_suffix(button("Summarize", lambda s=session: self.start_job(
                    "summarize", {"ids": [s["id"]], "backend": self.backend_choice()})))
                self.sessions_list.append(row)

        def summarize_selected(self):
            ids = [ident for ident, check in self.session_checks.items() if check.get_active()]
            if not ids:
                self.toast("Tick at least one session first.")
                return
            self.jobs_sessions.group.set_visible(True)
            self.start_job("summarize", {"ids": ids, "backend": self.backend_choice()})

        # ------------------------------------------------------------ recordings
        def build_recordings(self):
            scrolled, box = page_box()
            toolbar = Gtk.Box(spacing=8)
            heading = Gtk.Label(label="Recordings", xalign=0, hexpand=True)
            heading.add_css_class("page-title")
            toolbar.append(heading)
            toolbar.append(button("Refresh", self.load_recordings))
            box.append(toolbar)
            top = group("")
            self.recordings_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.recordings_list.add_css_class("boxed-list")
            self.recordings_empty = Adw.StatusPage(icon_name="audio-input-microphone-symbolic",
                                                   title="No recordings yet",
                                                   description="Plug in the recorder.")
            top.add(self.recordings_empty)
            top.add(self.recordings_list)
            box.append(top)
            return scrolled

        def load_recordings(self):
            def done(rows, error):
                if error:
                    self.toast(str(error))
                    return
                rows = rows or []
                self.recordings_empty.set_visible(not rows)
                self.recordings_list.set_visible(bool(rows))
                self.fill_recordings(self.recordings_list, rows)
                self.fill_recordings(self.home_recordings_list, rows[:8])
                self.home_recordings.set_visible(bool(rows))
            call(lambda: api.request("/api/recordings"), done)

        def fill_recordings(self, listbox, rows):
            clear(listbox)
            for item in rows:
                row = Adw.ActionRow(title=item["name"], subtitle=f"imported {when(item.get('imported_at'))}")
                row.add_suffix(pill("transcribed", "ok") if item.get("complete") else pill("waiting", "warn"))
                if item.get("note"):
                    row.add_suffix(button(item.get("note_name") or "Note",
                                          lambda i=item: self.view_note(i["note"])))
                listbox.append(row)

        # ------------------------------------------------------------ search
        def build_search(self):
            scrolled, box = page_box()
            top = group("Search", "Every word must match. Add * for a prefix (plumb*). Newest recordings first.")
            form = Gtk.Box(spacing=8)
            self.query = Gtk.SearchEntry(placeholder_text="Words to find, for example: roof leak", hexpand=True)
            self.query.connect("activate", lambda *_: self.run_search())
            self.since = Gtk.Entry(placeholder_text="on or after YYYY-MM-DD", width_chars=20)
            self.speaker = Gtk.Entry(placeholder_text="Speaker label", width_chars=16)
            form.append(self.query)
            form.append(self.since)
            form.append(self.speaker)
            form.append(button("Search", self.run_search, "suggested-action"))
            top.add(form)
            self.results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.results.add_css_class("boxed-list")
            self.results_note = Gtk.Label(xalign=0, wrap=True)
            self.results_note.add_css_class("hint")
            top.add(self.results_note)
            top.add(self.results)
            box.append(top)
            return scrolled

        def run_search(self):
            words = self.query.get_text().strip()
            if not words:
                return
            self.results_note.set_text("Searching...")
            params = {"q": words, "since": self.since.get_text().strip(), "speaker": self.speaker.get_text().strip()}

            def done(hits, error):
                clear(self.results)
                if error:
                    self.results_note.set_text(str(error))
                    return
                hits = hits or []
                self.results_note.set_text("No matches." if not hits else f"{len(hits)} match(es)")
                for hit in hits:
                    row = Gtk.ListBoxRow(activatable=False)
                    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                                    margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
                    head = Gtk.Box(spacing=10)
                    link = Gtk.Button(label=note_label(hit), has_frame=False)
                    link.add_css_class("link")
                    if hit.get("note"):
                        link.connect("clicked", lambda _b, n=hit["note"]: self.view_note(n))
                    head.append(link)
                    at = Gtk.Label(label=stamp(hit.get("start_seconds")))
                    at.add_css_class("dim-label")
                    head.append(at)
                    inner.append(head)
                    text = Gtk.Label(xalign=0, wrap=True, use_markup=True)
                    speaker = hit.get("speaker")
                    prefix = f"<b>{GLib.markup_escape_text(speaker)}:</b> " if speaker else ""
                    text.set_markup(prefix + highlight_markup(hit.get("text") or "", GLib.markup_escape_text))
                    inner.append(text)
                    row.set_child(inner)
                    self.results.append(row)
            call(lambda: api.request("/api/search", params=params), done)

        # ------------------------------------------------------------ settings
        def build_settings(self):
            scrolled, box = page_box()
            self.settings_box = box
            toolbar = Gtk.Box(spacing=8)
            heading = Gtk.Label(label="Settings", xalign=0, hexpand=True)
            heading.add_css_class("page-title")
            toolbar.append(heading)
            toolbar.append(button("Find my Obsidian vault", self.find_vault))
            toolbar.append(button("Test summary backend", lambda: self.start_job("test-backend", {})))
            toolbar.append(button("Save", self.save_settings, "suggested-action"))
            box.append(toolbar)
            head = group("")
            self.settings_path = Gtk.Label(xalign=0, wrap=True)
            self.settings_path.add_css_class("hint")
            head.add(self.settings_path)
            self.settings_failures = Gtk.Label(xalign=0, wrap=True, visible=False)
            self.settings_failures.add_css_class("failure")
            head.add(self.settings_failures)
            box.append(head)
            self.jobs_settings = JobsView("Latest action", limit=1, empty="")
            self.jobs_settings.group.set_visible(False)
            box.append(self.jobs_settings.group)
            self.settings_groups = []
            self.pages["settings"] = (scrolled, self.jobs_settings.group)
            return scrolled

        def load_settings(self):
            def done(data, error):
                if error:
                    self.toast(str(error))
                    return
                self.settings = data
                self.render_settings()
            call(lambda: api.request("/api/config"), done)

        def render_settings(self):
            for old in self.settings_groups:
                self.settings_box.remove(old)
            self.settings_groups = []
            self.fields = {}
            self.entries = {}
            self.settings_path.set_text(f"Saved to {self.settings['path']}. Changes apply on the next cycle.")
            values = self.settings["values"]
            for section in self.settings["settings"]:
                widget = group(section["title"])
                for field in section["fields"]:
                    widget.add(self.make_field(field, values.get(field["key"], "")))
                self.settings_box.append(widget)
                self.settings_groups.append(widget)

        def make_field(self, field, value):
            key, kind = field["key"], field["type"]
            subtitle = field.get("help") or key
            if kind == "choice":
                row = Adw.ComboRow(title=field["label"], subtitle=subtitle)
                choices = field["choices"]
                row.set_model(Gtk.StringList.new(["(automatic)" if c == "" else c for c in choices]))
                row.set_selected(choices.index(value) if value in choices else 0)
                self.fields[key] = lambda r=row, c=choices: c[r.get_selected()]
                return row
            row = Adw.ActionRow(title=field["label"], subtitle=subtitle)
            if kind == "bool":
                switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=value == "1")
                row.add_suffix(switch)
                row.set_activatable_widget(switch)
                self.fields[key] = lambda s=switch: "1" if s.get_active() else "0"
                return row
            if kind == "secret":
                entry = Gtk.PasswordEntry(valign=Gtk.Align.CENTER, show_peek_icon=True,
                                          placeholder_text="saved" if value else "not set")
                entry.set_text(value)
                entry.set_size_request(240, -1)
                row.add_suffix(entry)
                self.fields[key] = entry.get_text
                return row
            entry = Gtk.Entry(valign=Gtk.Align.CENTER, text=value, hexpand=True)
            entry.set_size_request(260, -1)
            if kind == "int":
                entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
                entry.set_size_request(120, -1)
            row.add_suffix(entry)
            self.entries[key] = entry
            if kind == "path":
                pick = Gtk.Button.new_from_icon_name("folder-open-symbolic")
                pick.set_valign(Gtk.Align.CENTER)
                pick.set_tooltip_text("Choose a folder")
                pick.connect("clicked", lambda _b, e=entry: self.pick_folder(e))
                row.add_suffix(pick)
            self.fields[key] = entry.get_text
            return row

        def pick_folder(self, entry):
            def chosen(path):
                if path:
                    entry.set_text(path)
            if hasattr(Gtk, "FileDialog"):
                dialog = Gtk.FileDialog()

                def finish(dialog, result):
                    try:
                        folder = dialog.select_folder_finish(result)
                    except GLib.Error:
                        return
                    chosen(folder.get_path() if folder else None)
                dialog.select_folder(self, None, finish)
            else:
                chooser = Gtk.FileChooserNative(title="Choose a folder", transient_for=self,
                                                action=Gtk.FileChooserAction.SELECT_FOLDER)

                def responded(chooser, response):
                    if response == Gtk.ResponseType.ACCEPT:
                        chosen(chooser.get_file().get_path())
                    chooser.destroy()
                chooser.connect("response", responded)
                chooser.show()

        def save_settings(self):
            if not self.fields:
                return
            values = {key: getter() for key, getter in self.fields.items()}

            def done(reply, error):
                if error:
                    self.toast(str(error))
                    return
                failures = (reply or {}).get("failures")
                if failures:
                    self.settings_failures.set_text("Not saved. The doctor found:\n" +
                                                    "\n".join(f"• {f}" for f in failures))
                    self.settings_failures.set_visible(True)
                    return
                self.settings_failures.set_visible(False)
                self.toast("Settings saved.")
                self.load_settings()
                self.refresh_status()
            call(lambda: api.request("/api/config", body={"values": values}), done)

        def find_vault(self):
            def done(vaults, error):
                if error:
                    self.toast(str(error))
                    return
                if not vaults:
                    self.toast("No Obsidian vault found on this machine.")
                    return
                if len(vaults) == 1:
                    self.use_vault(vaults[0])
                    return
                picker = Adw.Window(transient_for=self, modal=True, default_width=560, default_height=360,
                                    title="Vaults found")
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                box.append(Adw.HeaderBar())
                listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_top=12,
                                      margin_bottom=12, margin_start=12, margin_end=12)
                listbox.add_css_class("boxed-list")
                for vault in vaults:
                    row = Adw.ActionRow(title=Path(vault).name, subtitle=vault, activatable=True)
                    row.connect("activated", lambda _r, v=vault: (self.use_vault(v), picker.close()))
                    listbox.append(row)
                box.append(Gtk.ScrolledWindow(child=listbox, vexpand=True))
                picker.set_content(box)
                picker.present()
            call(lambda: api.request("/api/vaults"), done)

        def use_vault(self, vault):
            entry = self.entries.get("VAULT_DIR")
            if entry is None:
                return
            entry.set_text(vault.rstrip("/") + "/Recordings")
            self.toast("Notes folder set to a Recordings folder in that vault. Save to apply.")

        # ------------------------------------------------------------ tools
        def build_tools(self):
            scrolled, box = page_box()
            doctor = group("Doctor", "Checks the configuration, commands, packages, folders, and services.")
            doctor.set_header_suffix(button("Run the doctor", lambda: self.start_job("doctor", {})))
            box.append(doctor)
            index = group("Search index", "Rebuilt every cycle; refresh by hand after copying notes around.")
            index.set_header_suffix(button("Refresh the index", lambda: self.start_job("index", {})))
            box.append(index)
            models = group("Whisper models")
            self.profile_picker = Adw.ComboRow(title="Profile", subtitle="fast is distil-large-v3, accurate is large-v3")
            self.profile_picker.set_model(Gtk.StringList.new(PROFILES))
            models.add(self.profile_picker)
            row = Adw.ActionRow(title="Model cache")
            for action, kind in (("status", None), ("download", None), ("remove", "destructive-action")):
                row.add_suffix(button(action.capitalize(), lambda a=action: self.start_job(
                    "model-cache", {"action": a, "profile": PROFILES[self.profile_picker.get_selected()]}), kind))
            models.add(row)
            box.append(models)
            rebuild = group("Rebuild a day's sessions",
                            "Forgets the sessions that started on that day and regroups them, "
                            "for example to fold in a late recording.")
            row = Adw.ActionRow(title="Date")
            self.rebuild_date = Gtk.Entry(placeholder_text="YYYY-MM-DD", valign=Gtk.Align.CENTER, width_chars=14)
            row.add_suffix(self.rebuild_date)
            row.add_suffix(button("Rebuild", self.rebuild_day))
            rebuild.add(row)
            box.append(rebuild)
            device = group("Another device", "To open the panel from your phone, set \"Panel listens on\" to "
                                             "0.0.0.0 in Settings; this link then works on your network.")
            self.link_row = Adw.ActionRow(title="Private link", subtitle="")
            self.link_row.add_suffix(button("Copy", self.copy_link))
            device.add(self.link_row)
            box.append(device)
            self.jobs_tools = JobsView("Activity")
            box.append(self.jobs_tools.group)
            log = group("Log")
            log.set_header_suffix(button("Refresh log", self.load_log))
            scrolled_log, self.log_buffer = output_view()
            self.log_buffer.set_text("Press refresh.")
            log.add(scrolled_log)
            box.append(log)
            self.load_link()
            self.pages["tools"] = (scrolled, self.jobs_tools.group)
            return scrolled

        def rebuild_day(self):
            date = self.rebuild_date.get_text().strip()
            if not date:
                self.toast("Type a date first.")
                return
            self.start_job("rebuild", {"date": date})

        def load_log(self):
            def done(reply, error):
                self.log_buffer.set_text(str(error) if error else ((reply or {}).get("text") or "(empty)"))
            call(lambda: api.request("/api/log", params={"lines": "300"}), done)

        def load_link(self):
            def done(reply, error):
                if not error and reply:
                    self.link_row.set_subtitle(reply.get("url", ""))
            call(lambda: api.request("/api/link"), done)

        def copy_link(self):
            link = self.link_row.get_subtitle()
            if link:
                self.get_clipboard().set(link)
                self.toast("Link copied.")

        # ------------------------------------------------------------ notes and files
        def vault_path(self, name):
            vault = ((self.status or {}).get("vault") or "").rstrip("/")
            return f"{vault}/{name}" if name else vault

        def view_note(self, path, into=None):
            if not path:
                return

            def done(note, error):
                if error:
                    self.toast(str(error))
                    return
                target = into or self.note_window
                if target is not None and target.get_visible():
                    target.show_note(note)
                    target.present()
                    return
                self.note_window = NoteWindow(self, note)
                self.note_window.present()
            call(lambda: api.request("/api/note", params={"path": path}), done)

        def open_path(self, path):
            def done(reply, error):
                if error:
                    self.toast(str(error))
                elif reply and reply.get("ok"):
                    self.toast("Opened.")
                else:
                    self.toast((reply or {}).get("error") or "Could not open.")
            call(lambda: api.request("/api/open", body={"path": path}), done)

    class App(Adw.Application):
        """One instance per session: launching again raises the existing window."""

        def __init__(self):
            super().__init__(application_id=APP_ID)
            self.window = None
            action = Gio.SimpleAction.new("show-page", GLib.VariantType.new("s"))
            action.connect("activate", lambda _action, name: self.show_page(name.get_string()))
            self.add_action(action)

        def show_page(self, name):
            if self.window is not None:
                self.window.show_page(name)
                self.window.present()

        def do_activate(self):
            if self.window is None:
                provider = Gtk.CssProvider()
                if hasattr(provider, "load_from_string"):
                    provider.load_from_string(CSS)
                else:
                    provider.load_from_data(CSS.encode("utf-8"))
                Gtk.StyleContext.add_provider_for_display(
                    Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                Gtk.Window.set_default_icon_name(APP_ID)
                self.window = Window(self)
                if on_ready is not None:
                    GLib.timeout_add(600, lambda: (on_ready(self.window), False)[1])
            self.window.present()

    application = App()
    try:
        application.register(None)
    except GLib.Error:
        pass  # no session bus: run on our own
    if application.get_is_remote():
        application.activate()
        if page:
            application.activate_action("show-page", GLib.Variant("s", page))
        return 0
    return application.run([])


# --------------------------------------------------------------------------- launching

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--connect", metavar="URL", help="the panel server to use (internal)")
    parser.add_argument("--token-file", type=Path, help="file holding the panel's private token")
    parser.add_argument("--page", choices=("home", "sessions", "recordings", "search", "settings", "tools"),
                        help="the page to open on")
    parser.add_argument("--verbose", action="store_true", help="log every request to stderr")
    args = parser.parse_args(argv)
    if args.connect:
        token = args.token_file.read_text(encoding="utf-8").strip() if args.token_file else ""
        return run_gui(args.connect, token, args.page, args.verbose)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import panel
    base = panel.ensure_server()
    python = panel.gui_python()
    if python is None:
        print(GI_HINT, file=sys.stderr)
        return panel.open_web(base, tab=False)
    os.execv(python, [python, str(Path(__file__).resolve()), "--connect", base,
                      "--token-file", str(panel.TOKEN_FILE)]
             + (["--page", args.page] if args.page else []) + (["--verbose"] if args.verbose else []))


if __name__ == "__main__":
    sys.exit(main())
