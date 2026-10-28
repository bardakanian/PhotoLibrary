import json
from dataclasses import dataclass
from pathlib import Path

from .constants import APP_DIR, CONFIG_FILE

VIEW_MODES = ("years", "months", "all")
TIME_FORMATS = ("system", "12", "24")
SORT_ORDERS = ("newest", "oldest")
MIN_ZOOM, MAX_ZOOM, DEFAULT_ZOOM = 80, 320, 160


@dataclass(slots=True)
class AppSettings:
    library_folder: Path | None = None
    theme: str = "system"
    time_format: str = "system"  # "system", "12" or "24" hour clock
    zoom: int = DEFAULT_ZOOM
    view_mode: str = "all"
    sort_order: str = "newest"  # "newest" or "oldest" first
    info_panel_visible: bool = False
    sidebar_visible: bool = True
    window_geometry: str = ""


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

        folder = None
        folder_text = data.get("library_folder", "")
        if folder_text:
            candidate = Path(folder_text).expanduser()
            if candidate.is_dir():
                folder = candidate
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
            library_folder=folder,
            theme=theme,
            time_format=time_format,
            zoom=zoom,
            view_mode=view_mode,
            sort_order=sort_order,
            info_panel_visible=bool(data.get("info_panel_visible", False)),
            sidebar_visible=bool(data.get("sidebar_visible", True)),
            window_geometry=str(data.get("window_geometry", "")),
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
            "library_folder": str(settings.library_folder) if settings.library_folder else "",
            "theme": settings.theme,
            "time_format": settings.time_format,
            "zoom": settings.zoom,
            "view_mode": settings.view_mode,
            "sort_order": settings.sort_order,
            "info_panel_visible": settings.info_panel_visible,
            "sidebar_visible": settings.sidebar_visible,
            "window_geometry": settings.window_geometry,
        })
        # Write then rename, so a crash mid-write never leaves a truncated config.
        temp = CONFIG_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        temp.replace(CONFIG_FILE)
