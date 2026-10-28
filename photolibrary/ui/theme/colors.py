from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    background: str
    surface: str
    surface_alt: str
    elevated: str
    text: str
    text_muted: str
    text_subtle: str
    border: str
    border_strong: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    selection: str
    success: str
    warning: str
    danger: str


LIGHT = Palette(
    background="#F5F6F8", surface="#FFFFFF", surface_alt="#F0F2F5", elevated="#FFFFFF",
    text="#1D2430", text_muted="#667085", text_subtle="#8A94A3", border="#E2E6EC",
    border_strong="#CDD3DC", accent="#4263EB", accent_hover="#3451D1", accent_pressed="#2944B5",
    accent_soft="#E9EEFF", selection="#DFE7FF", success="#23845B", warning="#A96413", danger="#C43D4B",
)

DARK = Palette(
    background="#16181D", surface="#1D2026", surface_alt="#242830", elevated="#292D35",
    text="#ECEEF2", text_muted="#A6ADBA", text_subtle="#7D8593", border="#30353E",
    border_strong="#424955", accent="#7895FF", accent_hover="#8DA5FF", accent_pressed="#6582EA",
    accent_soft="#29345D", selection="#313E6A", success="#55B78A", warning="#D59A4A", danger="#EE7180",
)
