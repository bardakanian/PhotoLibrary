"""Human-readable labels shared by the grid, viewer and info panel."""

from datetime import datetime


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return ""
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_size(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "—"
    value = float(num_bytes)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} TB"


def format_time(value: datetime, hour12: bool = False) -> str:
    """13:05 on a 24-hour clock, 1:05 PM on a 12-hour clock."""
    if hour12:
        return f"{value.hour % 12 or 12}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"
    return f"{value:%H:%M}"


def format_date(value: datetime | None, with_time: bool = True, hour12: bool = False) -> str:
    if value is None:
        return "Unknown date"
    text = f"{value:%A, %B} {value.day}, {value.year}"
    return f"{text} at {format_time(value, hour12)}" if with_time else text


def format_dimensions(width: int | None, height: int | None) -> str:
    if not width or not height:
        return "—"
    megapixels = width * height / 1_000_000
    suffix = f"  ·  {megapixels:.1f} MP" if megapixels >= 0.5 else ""
    return f"{width:,} × {height:,}{suffix}"
