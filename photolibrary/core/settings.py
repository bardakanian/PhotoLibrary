import json
from dataclasses import dataclass, field
from pathlib import Path

from .constants import APP_DIR, CONFIG_FILE

VIEW_MODES = ("years", "months", "all")
TIME_FORMATS = ("system", "12", "24")
SORT_ORDERS = ("newest", "oldest")
MIN_ZOOM, MAX_ZOOM, DEFAULT_ZOOM = 80, 320, 160


@dataclass(slots=True)
class AppSettings:
    # The library is one or more folders. A missing one (an unplugged drive) is kept, not forgotten.
    library_folders: list[Path] = field(default_factory=list)
    theme: str = "system"
    time_format: str = "system"  # "system", "12" or "24" hour clock
    zoom: int = DEFAULT_ZOOM
    view_mode: str = "all"
    sort_order: str = "newest"  # "newest" or "oldest" first
    info_panel_visible: bool = False
    sidebar_visible: bool = True
    watch_folder: bool = True  # rescan automatically when files change
    window_geometry: str = ""
    # Albums left out of All Photos, search, counts and Insights: (resolved library folder, album name).
    hidden_albums: list[tuple[str, str]] = field(default_factory=list)


def _hidden_albums(value) -> list[tuple[str, str]]:
    albums: list[tuple[str, str]] = []
    for entry in value if isinstance(value, list) else []:
        if isinstance(entry, list) and len(entry) == 2 and all(isinstance(part, str) and part for part in entry):
            if tuple(entry) not in albums:
                albums.append(tuple(entry))
    return albums


class SettingsStore:
    """JSON configuration that keeps keys it doesn't know about."""

    def load(self) -> AppSettings:
        data: dict = {}
        try:
            if CONFIG_FILE.exists():
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}

        folder_texts = data.get("library_folders")
        if not isinstance(folder_texts, list):
            # Settings from before several folders were supported.
            folder_texts = [data.get("library_folder", "")]
        folders: list[Path] = []
        for text in folder_texts:
            if isinstance(text, str) and text:
                candidate = Path(text).expanduser()
                if candidate not in folders:
                    folders.append(candidate)
        theme = data.get("theme", "system")
        if theme not in {"system", "light", "dark"}:
            theme = "system"
        try:
            zoom = max(MIN_ZOOM, min(MAX_ZOOM, int(data.get("zoom", DEFAULT_ZOOM))))
        except (TypeError, ValueError):
            zoom = DEFAULT_ZOOM
        time_format = str(data.get("time_format", "system"))
        if time_format not in TIME_FORMATS:
            time_format = "system"
        sort_order = data.get("sort_order", "newest")
        if sort_order not in SORT_ORDERS:
            sort_order = "newest"
        view_mode = data.get("view_mode", "all")
        if view_mode not in VIEW_MODES:
            view_mode = "all"
        return AppSettings(
            library_folders=folders,
            theme=theme,
            time_format=time_format,
            zoom=zoom,
            view_mode=view_mode,
            sort_order=sort_order,
            info_panel_visible=bool(data.get("info_panel_visible", False)),
            sidebar_visible=bool(data.get("sidebar_visible", True)),
            watch_folder=bool(data.get("watch_folder", True)),
            window_geometry=str(data.get("window_geometry", "")),
            hidden_albums=_hidden_albums(data.get("hidden_albums")),
        )

    def save(self, settings: AppSettings) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        try:
            if CONFIG_FILE.exists():
                existing = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            pass
        if not isinstance(existing, dict):
            existing = {}
        existing.update({
            "library_folders": [str(folder) for folder in settings.library_folders],
            # Older versions read only this key; keep it pointing at the first folder.
            "library_folder": str(settings.library_folders[0]) if settings.library_folders else "",
            "theme": settings.theme,
            "time_format": settings.time_format,
            "zoom": settings.zoom,
            "view_mode": settings.view_mode,
            "sort_order": settings.sort_order,
            "info_panel_visible": settings.info_panel_visible,
            "sidebar_visible": settings.sidebar_visible,
            "watch_folder": settings.watch_folder,
            "window_geometry": settings.window_geometry,
            "hidden_albums": [list(pair) for pair in settings.hidden_albums],
        })
        # Write then rename, so a crash mid-write never leaves a truncated config.
        temp = CONFIG_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        temp.replace(CONFIG_FILE)
