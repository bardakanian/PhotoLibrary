"""Zoom and pan arithmetic for the photo viewer, kept free of Qt so it can be tested.

`scale` is view units per image pixel. The view centre shows image point (cx, cy).
"""

from dataclasses import dataclass

MAX_SCALE = 4.0


@dataclass(frozen=True, slots=True)
class ZoomState:
    image_width: float
    image_height: float
    view_width: float
    view_height: float
    scale: float
    cx: float
    cy: float

    @classmethod
    def fitted(cls, image_width: float, image_height: float, view_width: float, view_height: float) -> "ZoomState":
        state = cls(max(1.0, image_width), max(1.0, image_height), max(1.0, view_width), max(1.0, view_height),
                    1.0, image_width / 2, image_height / 2)
        return state.with_scale(state.fit_scale)

    @property
    def fit_scale(self) -> float:
        # Fit the view, but never blow a small image up past one view unit per pixel.
        return min(self.view_width / self.image_width, self.view_height / self.image_height, 1.0)

    @property
    def is_fitted(self) -> bool:
        return abs(self.scale - self.fit_scale) < 1e-6

    def target_rect(self) -> tuple[float, float, float, float]:
        """(x, y, width, height) of the whole image in view coordinates."""
        return (self.view_width / 2 - self.cx * self.scale, self.view_height / 2 - self.cy * self.scale,
                self.image_width * self.scale, self.image_height * self.scale)

    def clamped(self) -> "ZoomState":
        def axis(center: float, image: float, view: float) -> float:
            half = view / 2 / self.scale
            if image <= 2 * half:
                return image / 2  # smaller than the view: keep centred
            return min(max(center, half), image - half)

        return ZoomState(self.image_width, self.image_height, self.view_width, self.view_height, self.scale,
                         axis(self.cx, self.image_width, self.view_width),
                         axis(self.cy, self.image_height, self.view_height))

    def with_scale(self, scale: float, anchor: tuple[float, float] | None = None) -> "ZoomState":
        """Zoom to `scale`, keeping the image point under `anchor` (view coords) in place."""
        scale = min(max(scale, self.fit_scale), max(MAX_SCALE, self.fit_scale))
        ax, ay = anchor if anchor is not None else (self.view_width / 2, self.view_height / 2)
        image_x = self.cx + (ax - self.view_width / 2) / self.scale
        image_y = self.cy + (ay - self.view_height / 2) / self.scale
        cx = image_x - (ax - self.view_width / 2) / scale
        cy = image_y - (ay - self.view_height / 2) / scale
        return ZoomState(self.image_width, self.image_height, self.view_width, self.view_height,
                         scale, cx, cy).clamped()

    def panned(self, dx: float, dy: float) -> "ZoomState":
        """Move the image by (dx, dy) view units, as when dragging it."""
        return ZoomState(self.image_width, self.image_height, self.view_width, self.view_height, self.scale,
                         self.cx - dx / self.scale, self.cy - dy / self.scale).clamped()

    def resized(self, view_width: float, view_height: float) -> "ZoomState":
        was_fitted = self.is_fitted
        state = ZoomState(self.image_width, self.image_height, max(1.0, view_width), max(1.0, view_height),
                          self.scale, self.cx, self.cy)
        return state.with_scale(state.fit_scale) if was_fitted else state.with_scale(self.scale)
