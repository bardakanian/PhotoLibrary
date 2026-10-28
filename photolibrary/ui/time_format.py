from PySide6.QtCore import QLocale


def system_uses_12_hour() -> bool:
    """Whether the system's short time format has an AM/PM marker."""
    pattern = QLocale.system().timeFormat(QLocale.FormatType.ShortFormat)
    return "a" in pattern.lower()


def uses_12_hour(preference: str) -> bool:
    """Resolve the "system", "12" or "24" setting to a clock."""
    if preference == "12":
        return True
    if preference == "24":
        return False
    return system_uses_12_hour()
