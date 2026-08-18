from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from PIL import Image, ImageTk

from train_tracker.config import MapConfig
from train_tracker.map.cbd import TrainSelection, resolve_marker_overlaps
from train_tracker.map.interpolation import scheduled_markers
from train_tracker.map.models import MapScene, MapScope, Point, RailRoute, TrainPositionSource
from train_tracker.map.projection import MapViewport
from train_tracker.map.renderer import MapRenderOptions, NetworkMapRenderer, hit_test

SCOPE_LABELS = {
    MapScope.FOCUSED: "Route Focus",
    MapScope.CBD: "Central Corridor",
    MapScope.FULL: "Full Network",
}


class NetworkMapView:
    def __init__(
        self,
        parent: Any,
        config: MapConfig,
        *,
        routes: tuple[RailRoute, ...],
        now: Callable[[], Any],
        refresh: Callable[[], None],
        toggle_pause: Callable[[], None],
        set_speed: Callable[[float], None],
        default_scope: MapScope,
        set_scope: Callable[[str], None],
        set_routes: Callable[[tuple[str, ...]], None],
        set_direction: Callable[[str], None],
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.frame = ttk.Frame(parent)
        self.config = config
        self.routes = routes
        self.set_routes_callback = set_routes
        self.now = now
        self.refresh_callback = refresh
        self.renderer = NetworkMapRenderer()
        self.scene: MapScene | None = None
        self.viewport: MapViewport | None = None
        self.interaction_viewport: MapViewport | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.current_image: Image.Image | None = None
        self.previous_positions: dict[str, Point] = {}
        self.transition_started = time.monotonic()
        self.drag_origin: tuple[int, int] | None = None
        self.dragged = False
        self.selection = TrainSelection()
        self.last_scope: MapScope | None = None
        self.auto_cycle_index = -1
        self.auto_cycle_at = time.monotonic()
        self.display_scale = 1
        self.display_offset = (0, 0)
        self.labels_var = tk.BooleanVar(value=config.show_station_labels)
        self.trains_var = tk.BooleanVar(value=True)
        self.estimates_var = tk.BooleanVar(value=config.show_scheduled_estimates)
        self.led_preview_var = tk.BooleanVar(value=True)
        route_labels = tuple(f"{route.id} — {route.name}" for route in routes)
        preferred = tuple(
            label
            for label in route_labels
            if any(key.casefold() in label.casefold() for key in config.focused_routes)
        )
        self.route_var = tk.StringVar(
            value=preferred[0] if preferred else (route_labels[0] if route_labels else "")
        )
        self.route_two_var = tk.StringVar(value=preferred[1] if len(preferred) > 1 else "None")
        self.direction_var = tk.StringVar(value=config.rail_direction)
        self.scope_var = tk.StringVar(value=SCOPE_LABELS[default_scope])
        self.speed_var = tk.StringVar(value="1")
        self.info_var = tk.StringVar(value="Click a station or train for details.")

        toolbar = ttk.Frame(self.frame, padding=(4, 4))
        toolbar.pack(fill="x")
        for label, command in (
            ("Fit all", self.fit_all),
            ("Reset", self.fit_all),
            ("Refresh", refresh),
        ):
            ttk.Button(toolbar, text=label, command=command).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Pause / resume", command=toggle_pause).pack(side="left", padx=2)
        ttk.Label(toolbar, text="Scope").pack(side="left", padx=(10, 2))
        scope = ttk.Combobox(
            toolbar,
            textvariable=self.scope_var,
            values=tuple(SCOPE_LABELS.values()),
            width=12,
            state="readonly",
        )
        scope.pack(side="left")
        scope.bind(
            "<<ComboboxSelected>>",
            lambda _event: set_scope(
                next(
                    item.value
                    for item, label in SCOPE_LABELS.items()
                    if label == self.scope_var.get()
                )
            ),
        )
        ttk.Label(toolbar, text="Direction").pack(side="left", padx=(8, 2))
        direction = ttk.Combobox(
            toolbar,
            textvariable=self.direction_var,
            values=("inbound", "outbound", "both"),
            width=8,
            state="readonly",
        )
        direction.pack(side="left")
        direction.bind(
            "<<ComboboxSelected>>",
            lambda _event: set_direction(self.direction_var.get()),
        )
        ttk.Label(toolbar, text="Speed").pack(side="left", padx=(8, 2))
        speed = ttk.Combobox(
            toolbar,
            textvariable=self.speed_var,
            values=("1", "10", "60"),
            width=4,
            state="readonly",
        )
        speed.pack(side="left")
        speed.bind("<<ComboboxSelected>>", lambda _event: set_speed(float(self.speed_var.get())))
        ttk.Checkbutton(toolbar, text="Labels", variable=self.labels_var, command=self.redraw).pack(
            side="left", padx=5
        )
        ttk.Checkbutton(toolbar, text="Trains", variable=self.trains_var, command=self.redraw).pack(
            side="left", padx=5
        )
        ttk.Checkbutton(
            toolbar, text="Estimates", variable=self.estimates_var, command=self.redraw
        ).pack(side="left", padx=5)
        ttk.Checkbutton(
            toolbar, text="LED preview", variable=self.led_preview_var, command=self.redraw
        ).pack(side="left", padx=5)
        ttk.Label(toolbar, text="Route 1").pack(side="left", padx=(8, 2))
        self.route_combo = ttk.Combobox(
            toolbar,
            textvariable=self.route_var,
            values=route_labels,
            width=17,
            state="readonly",
        )
        self.route_combo.pack(side="left")
        self.route_combo.bind("<<ComboboxSelected>>", lambda _event: self._routes_changed())
        ttk.Label(toolbar, text="Route 2").pack(side="left", padx=(5, 2))
        self.route_two_combo = ttk.Combobox(
            toolbar,
            textvariable=self.route_two_var,
            values=("None", *route_labels),
            width=17,
            state="readonly",
        )
        self.route_two_combo.pack(side="left")
        self.route_two_combo.bind("<<ComboboxSelected>>", lambda _event: self._routes_changed())
        ttk.Button(toolbar, text="Export PNG", command=self.save_png).pack(side="right", padx=2)

        self.canvas = tk.Canvas(
            self.frame, background=config.background_color, highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)
        info = ttk.Label(
            self.frame, textvariable=self.info_var, anchor="w", padding=(6, 4), wraplength=1100
        )
        info.pack(fill="x")
        self.canvas.bind("<Configure>", self._configured)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda event: self._zoom(event.x, event.y, 1.15))
        self.canvas.bind("<Button-5>", lambda event: self._zoom(event.x, event.y, 1 / 1.15))
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Leave>", self._leave)
        self.frame.after(max(8, int(1000 / config.interpolation_fps)), self._animation_tick)

    def update_scene(self, scene: MapScene) -> None:
        if self.scene is not None:
            self.previous_positions = {marker.id: marker.position for marker in self.scene.markers}
        self.transition_started = time.monotonic()
        self.scene = scene
        self.scope_var.set(SCOPE_LABELS[scene.scope])
        scope_changed = self.last_scope is not None and self.last_scope != scene.scope
        self.last_scope = scene.scope
        marker_ids = {marker.id for marker in scene.markers}
        if self.selection.pinned_id not in marker_ids:
            self.selection.pinned_id = None
        if self.selection.automatic_id not in marker_ids:
            self.selection.automatic_id = None
        if scene.scope == MapScope.FOCUSED:
            labels_by_id = {route.id: f"{route.id} — {route.name}" for route in self.routes}
            selected_labels = [
                labels_by_id[route_id]
                for route_id in scene.network.routes
                if route_id in labels_by_id
            ]
            if selected_labels:
                self.route_var.set(selected_labels[0])
                self.route_two_var.set(selected_labels[1] if len(selected_labels) > 1 else "None")
        if self.viewport is None or scope_changed:
            self.fit_all()
        self.redraw()

    def _configured(self, _event: Any) -> None:
        if self.scene is None:
            return
        width = self._map_width(max(320, self.canvas.winfo_width()))
        height = max(240, self.canvas.winfo_height())
        if self.viewport is None:
            self.viewport = MapViewport.fit(
                self.scene.network.bounds, width, height, self.config.padding
            )
        else:
            self.viewport.width, self.viewport.height = width, height
        self.redraw()

    def fit_all(self) -> None:
        if self.scene is None:
            return
        self.viewport = MapViewport.fit(
            self.scene.network.bounds,
            self._map_width(max(320, self.canvas.winfo_width())),
            max(240, self.canvas.winfo_height()),
            self.config.padding,
        )
        self.redraw()

    def _map_width(self, canvas_width: int) -> int:
        if (
            self.scene is not None
            and self.scene.scope in {MapScope.FOCUSED, MapScope.CBD}
            and self.config.reserve_train_info_panel
        ):
            return max(320, canvas_width - max(220, round(canvas_width * 0.24)))
        return canvas_width

    def _visible_routes(self) -> frozenset[str] | None:
        if self.scene is not None and self.scene.scope != MapScope.FULL:
            return None
        values = (self.route_var.get(), self.route_two_var.get())
        selected = frozenset(
            value.split(" — ", 1)[0] for value in values if value and value != "None"
        )
        return selected or None

    def _routes_changed(self) -> None:
        values = (self.route_var.get(), self.route_two_var.get())
        routes = tuple(value.split(" — ", 1)[0] for value in values if value and value != "None")
        self.set_routes_callback(routes)
        self.redraw()

    def _animated_scene(self) -> MapScene | None:
        if self.scene is None:
            return None
        now = self.now()
        live = tuple(
            marker for marker in self.scene.markers if marker.source == TrainPositionSource.LIVE_GPS
        )
        live_ids = {marker.trip_id for marker in live}
        estimates = tuple(
            marker
            for marker in scheduled_markers(
                self.scene.network, self.scene.trips, now, self.scene.delays
            )
            if marker.trip_id not in live_ids
        )
        transition = min(1.0, (time.monotonic() - self.transition_started) / 1.0)
        smoothed = []
        for marker in live:
            previous = self.previous_positions.get(marker.id)
            if previous is None:
                smoothed.append(marker)
            else:
                smoothed.append(
                    replace(
                        marker,
                        position=Point(
                            previous.x + (marker.position.x - previous.x) * transition,
                            previous.y + (marker.position.y - previous.y) * transition,
                        ),
                    )
                )
        markers = tuple((*smoothed, *estimates))
        if self.scene.scope in {MapScope.FOCUSED, MapScope.CBD}:
            spacing = (
                self.config.focused_track_spacing
                if self.scene.scope == MapScope.FOCUSED
                else self.config.cbd_track_spacing
            )
            markers = resolve_marker_overlaps(markers, spacing * 0.7)
        return replace(self.scene, now=now, markers=markers)

    def redraw(self) -> None:
        scene = self._animated_scene()
        if scene is None or self.viewport is None:
            return
        options = MapRenderOptions(
            show_labels=self.labels_var.get(),
            show_trains=self.trains_var.get(),
            show_estimates=self.estimates_var.get(),
            visible_routes=self._visible_routes(),
            background=self.config.background_color,
            highlighted_station_id=self.config.default_station_id,
            scope=scene.scope,
            show_compass=self.config.show_compass,
            show_symbol_legend=self.config.show_symbol_legend,
            reserve_info_panel=(
                scene.scope in {MapScope.FOCUSED, MapScope.CBD}
                and self.config.reserve_train_info_panel
            ),
            info_panel_width=self.config.train_info_panel_width,
            selected_train_id=self.selection.active_id,
            train_sprite_size=self.config.rail_sprite_size,
            show_direction_animation=self.config.show_direction_animation,
            animation_frame=int(time.monotonic() * 4),
        )
        if self.led_preview_var.get() and scene.scope != MapScope.FULL:
            logical_viewport = MapViewport.fit(
                scene.network.bounds, self.config.rail_map_width, 64, 1
            )
            logical = self.renderer.render(
                scene, (128, 64), viewport=logical_viewport, options=options
            )
            canvas_width = max(128, self.canvas.winfo_width())
            canvas_height = max(64, self.canvas.winfo_height())
            scale = max(1, min(canvas_width // 128, canvas_height // 64))
            preview = logical.resize((128 * scale, 64 * scale), Image.Resampling.NEAREST)
            self.current_image = logical
            self.photo = ImageTk.PhotoImage(preview)
            self.display_scale = scale
            self.display_offset = (
                (canvas_width - preview.width) // 2,
                (canvas_height - preview.height) // 2,
            )
            self.interaction_viewport = logical_viewport
            image_position = self.display_offset
        else:
            self.current_image = self.renderer.render(
                scene,
                (max(320, self.canvas.winfo_width()), self.viewport.height),
                viewport=self.viewport,
                options=options,
            )
            self.photo = ImageTk.PhotoImage(self.current_image)
            self.display_scale = 1
            self.display_offset = (0, 0)
            self.interaction_viewport = self.viewport
            image_position = (0, 0)
        self.canvas.delete("map")
        self.canvas.create_image(*image_position, image=self.photo, anchor="nw", tags="map")

    def _animation_tick(self) -> None:
        self._auto_cycle_selection()
        self.redraw()
        self.frame.after(max(8, int(1000 / self.config.interpolation_fps)), self._animation_tick)

    def _auto_cycle_selection(self) -> None:
        interval = self.config.auto_cycle_train_seconds
        if interval <= 0 or self.scene is None or not self.scene.markers:
            return
        now = time.monotonic()
        if now - self.auto_cycle_at < interval:
            return
        ordered = sorted(self.scene.markers, key=lambda item: item.id)
        self.auto_cycle_index = (self.auto_cycle_index + 1) % len(ordered)
        self.selection.select_automatically(ordered[self.auto_cycle_index].id)
        if self.selection.pinned_id is None and self.selection.hovered_id is None:
            self._show_train_info(ordered[self.auto_cycle_index].id)
        self.auto_cycle_at = now

    def _wheel(self, event: Any) -> None:
        if (
            self.led_preview_var.get()
            and self.scene is not None
            and self.scene.scope != MapScope.FULL
        ):
            return
        self._zoom(event.x, event.y, 1.15 if event.delta > 0 else 1 / 1.15)

    def _zoom(self, x: int, y: int, factor: float) -> None:
        if self.viewport is not None:
            self.viewport.zoom_at(factor, Point(x, y))
            self.redraw()

    def _press(self, event: Any) -> None:
        self.drag_origin = (event.x, event.y)
        self.dragged = False

    def _drag(self, event: Any) -> None:
        if self.drag_origin is None or self.viewport is None:
            return
        dx, dy = event.x - self.drag_origin[0], event.y - self.drag_origin[1]
        if abs(dx) + abs(dy) > 2:
            self.dragged = True
        self.viewport.pan(dx, dy)
        self.drag_origin = (event.x, event.y)
        self.redraw()

    def _release(self, event: Any) -> None:
        self.drag_origin = None
        if not self.dragged:
            self._inspect(event.x, event.y)

    def _hover(self, event: Any) -> None:
        if self.scene is None or self.interaction_viewport is None:
            return
        target = hit_test(
            self._animated_scene() or self.scene,
            self.interaction_viewport,
            self._interaction_point(event.x, event.y),
        )
        self.canvas.configure(cursor="hand2" if target else "")
        marker_id = target.id if target is not None and target.kind == "train" else None
        if marker_id != self.selection.hovered_id:
            self.selection.hover(marker_id)
            if self.selection.pinned_id is None:
                if marker_id is None:
                    self.info_var.set("Hover over or click a train for details.")
                else:
                    self._show_train_info(marker_id)
            self.redraw()

    def _leave(self, _event: Any) -> None:
        self.selection.hover(None)
        if self.selection.pinned_id is None:
            self.info_var.set("Hover over or click a train for details.")
        self.redraw()

    def _inspect(self, x: int, y: int) -> None:
        scene = self._animated_scene()
        if scene is None or self.interaction_viewport is None:
            return
        target = hit_test(scene, self.interaction_viewport, self._interaction_point(x, y))
        if target is None:
            self.selection.clear_pin()
            self.info_var.set("Hover over or click a train for details.")
            self.redraw()
            return
        if target.kind == "station":
            station = scene.network.stations[target.id]
            routes = ", ".join(scene.network.routes[item].name for item in station.route_ids)
            calls = scene.upcoming.get(station.id, ())
            upcoming = (
                "; ".join(
                    f"{call.departure:%H:%M} {call.route_name} → {call.destination}"
                    for call in calls
                )
                or "none"
            )
            self.info_var.set(
                f"{station.name}  •  {station.id}  •  Lines: {routes}  •  Upcoming: {upcoming}"
            )
            return
        self.selection.pin(target.id)
        self._show_train_info(target.id)
        self.redraw()

    def _interaction_point(self, x: int, y: int) -> Point:
        return Point(
            (x - self.display_offset[0]) / self.display_scale,
            (y - self.display_offset[1]) / self.display_scale,
        )

    def _show_train_info(self, marker_id: str) -> None:
        scene = self._animated_scene()
        if scene is None:
            return
        marker = next((item for item in scene.markers if item.id == marker_id), None)
        if marker is None:
            return
        source = (
            "LIVE GPS" if marker.source == TrainPositionSource.LIVE_GPS else "SCHEDULED ESTIMATE"
        )
        age = "unknown" if marker.age_seconds is None else f"{marker.age_seconds:.0f}s"
        arrival = marker.scheduled_arrival.strftime("%H:%M:%S") if marker.scheduled_arrival else "—"
        predicted = (
            marker.predicted_arrival.strftime("%H:%M:%S") if marker.predicted_arrival else "—"
        )
        identity = marker.vehicle_id or marker.trip_id
        self.info_var.set(
            f"{marker.route_name} → {marker.destination}  •  {identity}  •  "
            f"{marker.previous_station or '—'} → {marker.next_station or '—'}  •  "
            f"Scheduled {arrival} / predicted {predicted}  •  "
            f"Delay {marker.delay_seconds:+d}s  •  {source}  •  Age {age}"
        )

    def save_png(self) -> None:
        from tkinter import filedialog

        if self.current_image is None:
            return
        path = filedialog.asksaveasfilename(
            title="Export network map",
            defaultextension=".png",
            filetypes=(("PNG image", "*.png"),),
            initialfile="seq-network-map.png",
        )
        if path:
            self.current_image.save(Path(path), format="PNG")
