from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk

from train_tracker.bus_map.models import BusMapScene
from train_tracker.bus_map.presenter import BusMapPresenter
from train_tracker.clock import SimulatedClock
from train_tracker.config import AppConfig, BusMapConfig, MapConfig
from train_tracker.demo_recorder import record_demo
from train_tracker.map.models import MapScene, MapScope
from train_tracker.map.presenter import MapPresenter
from train_tracker.models import ProviderSnapshot
from train_tracker.providers.base import DepartureProvider
from train_tracker.providers.replay import ReplayProvider
from train_tracker.providers.simulated import Scenario, SimulatedProvider
from train_tracker.rendering.renderer import MatrixRenderer
from train_tracker.views.bus_map_view import BusMapView
from train_tracker.views.network_map_view import NetworkMapView

LOGGER = logging.getLogger(__name__)


class DesktopSimulator:
    def __init__(
        self,
        providers: dict[str, DepartureProvider],
        clock: SimulatedClock,
        *,
        station_id: str,
        scale: int = 6,
        fps: int = 20,
        refresh_seconds: float = 5.0,
        brightness: int = 100,
        pixel_grid: bool = True,
        map_presenter: MapPresenter | None = None,
        map_config: MapConfig | None = None,
        default_view: str = "departures",
        default_map_scope: MapScope = MapScope.FULL,
        bus_map_presenter: BusMapPresenter | None = None,
        bus_map_config: BusMapConfig | None = None,
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = tk.Tk()
        self.root.title("Queensland Train Tracker — 128×64 Simulator")
        self.root.minsize(900, 650)
        self.providers = providers
        self.clock = clock
        self.renderer = MatrixRenderer()
        self.refresh_seconds = refresh_seconds
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tracker-refresh")
        self.future: Future[ProviderSnapshot] | None = None
        self.map_future: Future[MapScene] | None = None
        self.bus_map_future: Future[BusMapScene] | None = None
        self.map_presenter = map_presenter
        self.map_config = map_config or MapConfig()
        self.default_view = default_view
        self.default_map_scope = default_map_scope
        self.bus_map_presenter = bus_map_presenter
        self.bus_map_config = bus_map_config or BusMapConfig()
        self.bus_map_view: BusMapView | None = None
        self.map_view: NetworkMapView | None = None
        self.snapshot: ProviderSnapshot | None = None
        self.current_frame = Image.new("RGB", (128, 64))
        self.photo: ImageTk.PhotoImage | None = None
        self.last_refresh = 0.0
        self.last_render = 0.0
        self.frames_rendered = 0
        self.fps_started = time.monotonic()

        self.mode_var = tk.StringVar(value=next(iter(providers)))
        self.station_var = tk.StringVar(value=station_id)
        self.scenario_var = tk.StringVar(value=Scenario.NORMAL.value)
        self.speed_var = tk.StringVar(value="1")
        self.scale_var = tk.IntVar(value=scale)
        self.fps_var = tk.IntVar(value=fps)
        self.brightness_var = tk.IntVar(value=brightness)
        self.grid_var = tk.BooleanVar(value=pixel_grid)
        self.delay_var = tk.IntVar(value=0)
        self.cancel_var = tk.BooleanVar(value=False)
        self.platform_var = tk.BooleanVar(value=False)
        self.alert_var = tk.BooleanVar(value=False)
        self.stale_var = tk.BooleanVar(value=False)
        self.offline_var = tk.BooleanVar(value=False)
        self.empty_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Starting offline simulator…")
        self.diagnostics_var = tk.StringVar(value="")

        self._build_ui()
        self._update_stations()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<space>", lambda _event: self.toggle_pause())
        self.clock.start()

    @property
    def provider(self) -> DepartureProvider:
        return self.providers[self.mode_var.get()]

    def _build_ui(self) -> None:
        tk, ttk = self.tk, self.ttk
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)
        departure_tab = ttk.Frame(self.notebook)
        self.notebook.add(departure_tab, text="Departure Board")
        matrix_group = ttk.LabelFrame(
            departure_tab, text="Logical framebuffer — exactly 128 × 64 pixels"
        )
        matrix_group.pack(fill="both", expand=True, padx=4, pady=4)
        self.canvas = tk.Canvas(matrix_group, background="#151515", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)
        if self.map_presenter is not None:
            self.map_view = NetworkMapView(
                self.notebook,
                self.map_config,
                routes=self.map_presenter.route_choices(self.clock.now()),
                now=self.clock.now,
                refresh=self.request_refresh,
                toggle_pause=self.toggle_pause,
                set_speed=self.clock.set_speed,
                default_scope=self.default_map_scope,
                set_scope=self.set_map_scope,
                set_routes=self.set_rail_routes,
                set_direction=self.set_rail_direction,
            )
            self.notebook.add(self.map_view.frame, text="Rail Map")
            if self.default_view in {"map", "network", "rail-map"}:
                self.notebook.select(self.map_view.frame)
        if self.bus_map_presenter is not None:
            self.bus_map_view = BusMapView(
                self.notebook,
                self.bus_map_config,
                routes=self.bus_map_presenter.route_choices(),
                now=self.clock.now,
                refresh=self.request_refresh,
                toggle_pause=self.toggle_pause,
                set_speed=self.clock.set_speed,
                set_route=self.set_bus_route,
                set_direction=self.set_bus_direction,
            )
            self.notebook.add(self.bus_map_view.frame, text="Bus Map")
            if self.default_view == "bus-map":
                self.notebook.select(self.bus_map_view.frame)

        transport = ttk.Frame(outer)
        transport.pack(fill="x", pady=(8, 3))
        for label, command in (
            ("Start", self.clock.start),
            ("Pause", self.clock.pause),
            ("Resume", self.clock.resume),
            ("Restart", self.restart),
            ("Step +30s", self.step),
            ("Save PNG", self.save_png),
            ("Record GIF", self.record_gif),
        ):
            ttk.Button(transport, text=label, command=command).pack(side="left", padx=2)
        ttk.Label(transport, text="Speed").pack(side="left", padx=(12, 3))
        speed = ttk.Combobox(
            transport,
            textvariable=self.speed_var,
            values=("1", "10", "60"),
            width=4,
            state="readonly",
        )
        speed.pack(side="left")
        speed.bind(
            "<<ComboboxSelected>>", lambda _event: self.clock.set_speed(float(self.speed_var.get()))
        )

        selectors = ttk.Frame(outer)
        selectors.pack(fill="x", pady=3)
        ttk.Label(selectors, text="Mode").grid(row=0, column=0, sticky="w")
        mode = ttk.Combobox(
            selectors,
            textvariable=self.mode_var,
            values=tuple(self.providers),
            width=12,
            state="readonly",
        )
        mode.grid(row=0, column=1, padx=4)
        mode.bind("<<ComboboxSelected>>", lambda _event: self._provider_changed())
        ttk.Label(selectors, text="Station").grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.station_combo = ttk.Combobox(
            selectors, textvariable=self.station_var, width=23, state="readonly"
        )
        self.station_combo.grid(row=0, column=3, padx=4)
        ttk.Label(selectors, text="Scenario").grid(row=0, column=4, sticky="w", padx=(10, 0))
        scenario = ttk.Combobox(
            selectors,
            textvariable=self.scenario_var,
            values=tuple(item.value for item in Scenario),
            width=15,
            state="readonly",
        )
        scenario.grid(row=0, column=5, padx=4)
        scenario.bind("<<ComboboxSelected>>", lambda _event: self._scenario_changed())

        injections = ttk.LabelFrame(outer, text="Live scenario injection")
        injections.pack(fill="x", pady=3)
        ttk.Label(injections, text="Delay min").pack(side="left", padx=(6, 2))
        ttk.Spinbox(injections, from_=0, to=60, textvariable=self.delay_var, width=4).pack(
            side="left"
        )
        for label, variable in (
            ("Cancel first", self.cancel_var),
            ("Platform", self.platform_var),
            ("Alert", self.alert_var),
            ("Stale", self.stale_var),
            ("Disconnected", self.offline_var),
            ("Empty", self.empty_var),
        ):
            ttk.Checkbutton(injections, text=label, variable=variable).pack(side="left", padx=4)

        preview = ttk.Frame(outer)
        preview.pack(fill="x", pady=3)
        ttk.Label(preview, text="Max scale").pack(side="left")
        ttk.Spinbox(preview, from_=1, to=12, textvariable=self.scale_var, width=4).pack(
            side="left", padx=3
        )
        ttk.Label(preview, text="Target FPS").pack(side="left", padx=(10, 0))
        ttk.Spinbox(preview, from_=1, to=60, textvariable=self.fps_var, width=4).pack(
            side="left", padx=3
        )
        ttk.Label(preview, text="Brightness preview").pack(side="left", padx=(10, 0))
        ttk.Scale(
            preview, from_=5, to=100, variable=self.brightness_var, orient="horizontal", length=150
        ).pack(side="left", padx=3)
        ttk.Checkbutton(preview, text="Pixel grid", variable=self.grid_var).pack(
            side="left", padx=8
        )

        ttk.Label(outer, textvariable=self.status_var, anchor="w").pack(fill="x", pady=(5, 0))
        ttk.Label(outer, textvariable=self.diagnostics_var, anchor="w", foreground="#555555").pack(
            fill="x"
        )

    def _provider_changed(self) -> None:
        self._update_stations()
        self.snapshot = None
        self.last_refresh = 0.0

    def request_refresh(self) -> None:
        self.last_refresh = 0.0

    def record_gif(self) -> None:
        from tkinter import filedialog

        selected = self.notebook.select()
        if self.map_view is not None and selected == str(self.map_view.frame):
            view = "rail-map"
        elif self.bus_map_view is not None and selected == str(self.bus_map_view.frame):
            view = "bus-map"
        else:
            view = "departures"
        path = filedialog.asksaveasfilename(
            title=f"Record {view} demonstration",
            defaultextension=".gif",
            filetypes=(("GIF animation", "*.gif"),),
            initialfile=f"{view}.gif",
        )
        if not path:
            return
        database = None
        if self.map_presenter is not None and self.map_presenter.repository is not None:
            database = self.map_presenter.repository.database_path
        config = AppConfig(
            station_id=self._station_id(),
            start_time=self.clock.now(),
            map=self.map_config,
            bus_map=self.bus_map_config,
        )
        record_demo(view, path, config=config, duration=8, fps=6, database=database)
        self.status_var.set(f"Recorded {path}")

    def set_map_scope(self, scope: str) -> None:
        if self.map_presenter is not None:
            self.map_presenter.set_scope(scope)
        self.request_refresh()

    def set_rail_routes(self, routes: tuple[str, ...]) -> None:
        if self.map_presenter is not None:
            self.map_presenter.set_focused_routes(routes)
        self.request_refresh()

    def set_rail_direction(self, direction: str) -> None:
        if self.map_presenter is not None:
            self.map_presenter.set_rail_direction(direction)
        self.request_refresh()

    def set_bus_route(self, route: str) -> None:
        if self.bus_map_presenter is not None:
            self.bus_map_presenter.set_route(route)
        self.request_refresh()

    def set_bus_direction(self, direction: str) -> None:
        if self.bus_map_presenter is not None:
            self.bus_map_presenter.set_direction(direction)
        self.request_refresh()

    def _update_stations(self) -> None:
        stops = self.provider.list_stops()
        labels = tuple(f"{stop.id} — {stop.name}" for stop in stops)
        self.station_combo["values"] = labels
        requested = self.station_var.get().split(" — ", 1)[0]
        selected = next(
            (label for label in labels if label.startswith(requested + " —")), labels[0]
        )
        self.station_var.set(selected)

    def _station_id(self) -> str:
        return self.station_var.get().split(" — ", 1)[0]

    def _scenario_changed(self) -> None:
        if isinstance(self.provider, SimulatedProvider):
            self.provider.set_scenario(self.scenario_var.get())
            self.last_refresh = 0.0

    def _apply_injections(self) -> None:
        provider = self.provider
        if not isinstance(provider, SimulatedProvider):
            return
        provider.overrides.delay_minutes = self.delay_var.get()
        provider.overrides.cancel_first = self.cancel_var.get()
        provider.overrides.platform_change = self.platform_var.get()
        provider.overrides.alert = self.alert_var.get()
        provider.overrides.stale = self.stale_var.get()
        provider.overrides.disconnected = self.offline_var.get()
        provider.overrides.empty = self.empty_var.get()

    def run(self) -> None:
        self.root.after(1, self._tick)
        self.root.mainloop()

    def _tick(self) -> None:
        now_monotonic = time.monotonic()
        self._apply_injections()
        if self.future is None and now_monotonic - self.last_refresh >= self.refresh_seconds:
            now = self.clock.now()
            self.future = self.executor.submit(self.provider.refresh, self._station_id(), now)
            self.status_var.set("Refreshing provider in background…")
        if self.future is not None and self.future.done():
            try:
                self.snapshot = self.future.result()
                self.last_refresh = now_monotonic
                self.status_var.set(self.snapshot.status.message)
                if self.map_presenter is not None and self.map_future is None:
                    self.map_future = self.executor.submit(
                        self.map_presenter.prepare, self.clock.now(), self.snapshot
                    )
                if self.bus_map_presenter is not None and self.bus_map_future is None:
                    self.bus_map_future = self.executor.submit(
                        self.bus_map_presenter.prepare, self.clock.now(), self.snapshot
                    )
            except Exception as exc:  # keep GUI alive and retain last good frame
                LOGGER.exception("Provider refresh failed")
                self.status_var.set(f"Last error: {exc}")
                self.last_refresh = now_monotonic
            finally:
                self.future = None

        if self.map_future is not None and self.map_future.done():
            try:
                scene = self.map_future.result()
                if self.map_view is not None:
                    self.map_view.update_scene(scene)
            except Exception as exc:
                LOGGER.exception("Map preparation failed")
                self.status_var.set(f"Map error: {exc}")
            finally:
                self.map_future = None

        if self.bus_map_future is not None and self.bus_map_future.done():
            try:
                bus_scene = self.bus_map_future.result()
                if self.bus_map_view is not None:
                    self.bus_map_view.update_scene(bus_scene)
            except Exception as exc:
                LOGGER.exception("Bus map preparation failed")
                self.status_var.set(f"Bus map error: {exc}")
            finally:
                self.bus_map_future = None

        fps = max(1, self.fps_var.get())
        if self.snapshot is not None and now_monotonic - self.last_render >= 1 / fps:
            now = self.clock.now()
            self.current_frame = self.renderer.render(
                self.snapshot, now, brightness=self.brightness_var.get()
            )
            self._display_frame(self.current_frame)
            self.last_render = now_monotonic
            self.frames_rendered += 1
            elapsed = max(0.001, now_monotonic - self.fps_started)
            actual_fps = self.frames_rendered / elapsed
            self.diagnostics_var.set(
                f"Mode: {self.provider.mode}   Time: {now:%Y-%m-%d %H:%M:%S %Z}   "
                f"Clock: {self.clock.speed:g}×   Render: {actual_fps:.1f} fps   "
                f"Refresh: {self.refresh_seconds:g}s"
            )
        self.root.after(10, self._tick)

    def _display_frame(self, frame: Image.Image) -> None:
        width = max(128, self.canvas.winfo_width())
        height = max(64, self.canvas.winfo_height())
        fit_scale = max(1, min(width // 128, height // 64, max(1, self.scale_var.get())))
        scaled = frame.resize((128 * fit_scale, 64 * fit_scale), Image.Resampling.NEAREST)
        if self.grid_var.get() and fit_scale >= 4:
            draw = ImageDraw.Draw(scaled)
            grid_color = (30, 30, 30)
            for x in range(0, scaled.width, fit_scale):
                draw.line((x, 0, x, scaled.height), fill=grid_color)
            for y in range(0, scaled.height, fit_scale):
                draw.line((0, y, scaled.width, y), fill=grid_color)
        self.photo = ImageTk.PhotoImage(scaled)
        self.canvas.delete("all")
        self.canvas.create_image(width // 2, height // 2, image=self.photo, anchor="center")

    def restart(self) -> None:
        self.clock.restart()
        if isinstance(self.provider, ReplayProvider):
            self.provider.restart(self.clock.now())
        self.last_refresh = 0.0

    def step(self) -> None:
        self.clock.step(30)
        self.last_refresh = 0.0

    def toggle_pause(self) -> None:
        self.clock.pause() if self.clock.running else self.clock.resume()

    def save_png(self) -> None:
        from tkinter import filedialog

        path = filedialog.asksaveasfilename(
            title="Save logical 128×64 frame",
            defaultextension=".png",
            filetypes=(("PNG image", "*.png"),),
            initialfile="train-tracker-frame.png",
        )
        if path:
            self.current_frame.save(Path(path), format="PNG")
            self.status_var.set(f"Saved {path}")

    def close(self) -> None:
        self.clock.pause()
        self.executor.shutdown(wait=False, cancel_futures=True)
        for provider in self.providers.values():
            close = getattr(provider, "close", None)
            if callable(close):
                close()
        self.root.destroy()
