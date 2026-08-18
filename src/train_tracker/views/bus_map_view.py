from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageTk

from train_tracker.bus_map.models import BusDirection, BusMapScene, BusMarker, BusRouteChoice
from train_tracker.bus_map.renderer import BusMapRenderer, BusRenderOptions
from train_tracker.config import BusMapConfig
from train_tracker.map.cbd import TrainSelection, resolve_marker_overlaps
from train_tracker.map.geometry import distance
from train_tracker.map.interpolation import scheduled_markers
from train_tracker.map.models import Point, TrainPositionSource


class BusMapView:
    def __init__(
        self,
        parent: Any,
        config: BusMapConfig,
        *,
        routes: tuple[BusRouteChoice, ...],
        now: Callable[[], Any],
        refresh: Callable[[], None],
        toggle_pause: Callable[[], None],
        set_speed: Callable[[float], None],
        set_route: Callable[[str], None],
        set_direction: Callable[[str], None],
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.frame = ttk.Frame(parent)
        self.config = config
        self.routes = routes
        self.now = now
        self.renderer = BusMapRenderer()
        self.scene: BusMapScene | None = None
        self.current_logical: Image.Image | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.selection = TrainSelection()
        self.previous_positions: dict[str, Point] = {}
        self.transition_started = time.monotonic()
        self.auto_cycle_at = time.monotonic()
        self.auto_cycle_index = -1
        self.display_scale = 1
        self.display_offset = (0, 0)

        route_labels = tuple(route.label for route in routes)
        selected_route = next(
            (route for route in routes if route.short_name == config.default_route),
            routes[0] if routes else None,
        )
        self.route_var = tk.StringVar(value=selected_route.label if selected_route else "")
        self.direction_var = tk.StringVar(value=config.direction)
        self.live_var = tk.BooleanVar(value=True)
        self.estimates_var = tk.BooleanVar(value=True)
        self.labels_var = tk.BooleanVar(value=config.show_major_stop_labels)
        self.led_pixels_var = tk.BooleanVar(value=True)
        self.speed_var = tk.StringVar(value="1")
        self.info_var = tk.StringVar(value="Hover over a bus or click to pin it.")

        toolbar = ttk.Frame(self.frame, padding=4)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="Route").pack(side="left")
        route_combo = ttk.Combobox(
            toolbar,
            textvariable=self.route_var,
            values=route_labels,
            width=27,
            state="readonly",
        )
        route_combo.pack(side="left", padx=3)
        route_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: set_route(self._selected_route_id()),
        )
        ttk.Label(toolbar, text="Direction").pack(side="left", padx=(8, 2))
        direction = ttk.Combobox(
            toolbar,
            textvariable=self.direction_var,
            values=tuple(item.value for item in BusDirection),
            width=9,
            state="readonly",
        )
        direction.pack(side="left")
        direction.bind(
            "<<ComboboxSelected>>",
            lambda _event: set_direction(self.direction_var.get()),
        )
        for label, command in (
            ("Fit route", self.redraw),
            ("Reset", self.redraw),
            ("Refresh", refresh),
            ("Pause / resume", toggle_pause),
        ):
            ttk.Button(toolbar, text=label, command=command).pack(side="left", padx=2)

        controls = ttk.Frame(self.frame, padding=(4, 0, 4, 4))
        controls.pack(fill="x")
        for label, variable in (
            ("Live buses", self.live_var),
            ("Estimates", self.estimates_var),
            ("Stop labels", self.labels_var),
            ("LED pixels", self.led_pixels_var),
        ):
            ttk.Checkbutton(controls, text=label, variable=variable, command=self.redraw).pack(
                side="left", padx=4
            )
        ttk.Label(controls, text="Speed").pack(side="left", padx=(10, 2))
        speed = ttk.Combobox(
            controls,
            textvariable=self.speed_var,
            values=("1", "10", "60"),
            width=4,
            state="readonly",
        )
        speed.pack(side="left")
        speed.bind("<<ComboboxSelected>>", lambda _event: set_speed(float(self.speed_var.get())))
        ttk.Button(controls, text="Export PNG", command=self.save_png).pack(side="right")

        self.canvas = tk.Canvas(
            self.frame, background=config.background_color, highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)
        ttk.Label(self.frame, textvariable=self.info_var, anchor="w", padding=(6, 4)).pack(fill="x")
        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Leave>", self._leave)
        self.canvas.bind("<ButtonRelease-1>", self._click)
        self.frame.after(max(16, int(1000 / config.preview_fps)), self._animation_tick)

    def _selected_route_id(self) -> str:
        label = self.route_var.get()
        return next((route.id for route in self.routes if route.label == label), label)

    def update_scene(self, scene: BusMapScene) -> None:
        if self.scene is not None:
            self.previous_positions = {
                marker.id: marker.marker.position for marker in self.scene.markers
            }
        self.transition_started = time.monotonic()
        self.scene = scene
        self.route_var.set(scene.route.label)
        ids = {marker.id for marker in scene.markers}
        if self.selection.pinned_id not in ids:
            self.selection.pinned_id = None
        if self.selection.automatic_id not in ids:
            self.selection.automatic_id = None
        self.redraw()

    def _animated_scene(self) -> BusMapScene | None:
        if self.scene is None:
            return None
        now = self.now()
        live = tuple(
            marker
            for marker in self.scene.markers
            if marker.marker.source == TrainPositionSource.LIVE_GPS
        )
        live_ids = {marker.marker.trip_id for marker in live}
        trip_by_id = {trip.id: trip for trip in self.scene.trips}
        estimates = tuple(
            BusMarker(
                marker,
                int(trip_by_id[marker.trip_id].direction_id or 0),
                any(
                    distance(marker.position, station.position) <= 1.5
                    for station in self.scene.network.stations.values()
                ),
            )
            for marker in scheduled_markers(
                self.scene.network, self.scene.trips, now, self.scene.delays
            )
            if marker.trip_id not in live_ids
        )
        transition = min(1.0, (time.monotonic() - self.transition_started) / 1.0)
        smoothed: list[BusMarker] = []
        for bus in live:
            previous = self.previous_positions.get(bus.id)
            if previous is None:
                smoothed.append(bus)
            else:
                marker = bus.marker
                smoothed.append(
                    replace(
                        bus,
                        marker=replace(
                            marker,
                            position=Point(
                                previous.x + (marker.position.x - previous.x) * transition,
                                previous.y + (marker.position.y - previous.y) * transition,
                            ),
                        ),
                    )
                )
        base_markers = tuple((*smoothed, *estimates))
        resolved = resolve_marker_overlaps(
            tuple(marker.marker for marker in base_markers), self.config.lane_spacing * 0.75
        )
        by_id = {marker.id: marker for marker in resolved}
        markers = tuple(replace(bus, marker=by_id[bus.id]) for bus in base_markers)
        return replace(self.scene, now=now, markers=markers)

    def redraw(self) -> None:
        scene = self._animated_scene()
        if scene is None:
            return
        frame = int(time.monotonic() * 4)
        options = BusRenderOptions(
            map_width=self.config.map_width,
            info_panel_width=self.config.info_panel_width,
            show_labels=self.labels_var.get(),
            show_live=self.live_var.get(),
            show_estimates=self.estimates_var.get(),
            show_direction_animation=self.config.show_direction_animation,
            sprite_size=self.config.bus_sprite_size,
            background=self.config.background_color,
            route_glow=self.config.route_glow,
            selected_bus_id=self.selection.active_id,
            animation_frame=frame,
        )
        self.current_logical = self.renderer.render(scene, options=options)
        width = max(128, self.canvas.winfo_width())
        height = max(64, self.canvas.winfo_height())
        scale = max(1, min(width // 128, height // 64))
        preview = self.current_logical.resize((128 * scale, 64 * scale), Image.Resampling.NEAREST)
        if self.led_pixels_var.get() and scale >= 5:
            draw = ImageDraw.Draw(preview)
            for x in range(0, preview.width, scale):
                draw.line((x, 0, x, preview.height), fill=(15, 22, 30))
            for y in range(0, preview.height, scale):
                draw.line((0, y, preview.width, y), fill=(15, 22, 30))
        self.photo = ImageTk.PhotoImage(preview)
        self.canvas.delete("all")
        x, y = width // 2, height // 2
        self.canvas.create_image(x, y, image=self.photo, anchor="center")
        self.display_scale = scale
        self.display_offset = (x - preview.width // 2, y - preview.height // 2)

    def _logical_point(self, event: Any) -> Point:
        return Point(
            (event.x - self.display_offset[0]) / self.display_scale,
            (event.y - self.display_offset[1]) / self.display_scale,
        )

    def _target(self, event: Any) -> str | None:
        scene = self._animated_scene()
        if scene is None:
            return None
        point = self._logical_point(event)
        values = [
            (math.hypot(point.x - bus.marker.position.x, point.y - bus.marker.position.y), bus.id)
            for bus in scene.markers
        ]
        match = min(values, default=(99.0, ""))
        return match[1] if match[0] <= 5 else None

    def _hover(self, event: Any) -> None:
        marker_id = self._target(event)
        if marker_id != self.selection.hovered_id:
            self.selection.hover(marker_id)
            if marker_id is not None and self.selection.pinned_id is None:
                self._show_info(marker_id)
            self.redraw()

    def _leave(self, _event: Any) -> None:
        self.selection.hover(None)
        if self.selection.pinned_id is None:
            self.info_var.set("Hover over a bus or click to pin it.")
        self.redraw()

    def _click(self, event: Any) -> None:
        marker_id = self._target(event)
        if marker_id is None:
            self.selection.clear_pin()
            self.info_var.set("Hover over a bus or click to pin it.")
        else:
            self.selection.pin(marker_id)
            self._show_info(marker_id)
        self.redraw()

    def _show_info(self, marker_id: str) -> None:
        scene = self._animated_scene()
        if scene is None:
            return
        bus = next((item for item in scene.markers if item.id == marker_id), None)
        if bus is None:
            return
        marker = bus.marker
        source = "LIVE GPS" if marker.source == TrainPositionSource.LIVE_GPS else "ESTIMATE"
        self.info_var.set(
            f"{marker.route_name} → {marker.destination}  •  "
            f"{marker.previous_station or '—'} → {marker.next_station or '—'}  •  "
            f"{source}  •  delay {marker.delay_seconds:+d}s"
        )

    def _animation_tick(self) -> None:
        interval = self.config.auto_cycle_bus_seconds
        if interval > 0 and self.scene is not None and self.scene.markers:
            now = time.monotonic()
            if now - self.auto_cycle_at >= interval:
                ordered = sorted(self.scene.markers, key=lambda item: item.id)
                self.auto_cycle_index = (self.auto_cycle_index + 1) % len(ordered)
                self.selection.select_automatically(ordered[self.auto_cycle_index].id)
                self.auto_cycle_at = now
        self.redraw()
        self.frame.after(max(16, int(1000 / self.config.preview_fps)), self._animation_tick)

    def save_png(self) -> None:
        from tkinter import filedialog

        if self.current_logical is None:
            return
        path = filedialog.asksaveasfilename(
            title="Export native 128×64 bus map",
            defaultextension=".png",
            filetypes=(("PNG image", "*.png"),),
            initialfile="brisbane-bus-map-128x64.png",
        )
        if path:
            self.current_logical.save(Path(path), format="PNG")
