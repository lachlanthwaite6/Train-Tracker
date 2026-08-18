from __future__ import annotations

from datetime import datetime

from PIL import Image, ImageDraw

from train_tracker.models import FeedHealth, ProviderSnapshot, RealtimeState
from train_tracker.rendering import colors
from train_tracker.rendering.fonts import draw_text
from train_tracker.rendering.layout import abbreviate
from train_tracker.services.departures import minutes_until, upcoming_departures


class MatrixRenderer:
    width = 128
    height = 64

    def render(
        self,
        snapshot: ProviderSnapshot,
        now: datetime,
        *,
        brightness: int = 100,
    ) -> Image.Image:
        image = Image.new("RGB", (self.width, self.height), colors.BLACK)
        draw = ImageDraw.Draw(image)

        def tint(value: colors.RGB) -> colors.RGB:
            return colors.apply_brightness(value, brightness)

        station = abbreviate(snapshot.station.name, 18)
        draw_text(image, (1, 1), station, tint(colors.CYAN), max_width=75)
        draw_text(image, (97, 1), now.strftime("%H:%M"), tint(colors.WHITE), max_width=30)
        draw.line((0, 7, 127, 7), fill=tint(colors.GREY))

        departures = upcoming_departures(snapshot.departures, now, limit=4)
        if not departures:
            draw_text(image, (23, 25), "NO SERVICES", tint(colors.AMBER), scale=2)
        else:
            for index, departure in enumerate(departures):
                y = 10 + index * 11
                route = abbreviate(departure.route_name, 4)
                destination = abbreviate(departure.destination, 18)
                if departure.cancelled:
                    eta = "CXL"
                    eta_color = colors.RED
                    destination_color = colors.RED
                else:
                    minutes = minutes_until(departure, now)
                    eta = "NOW" if minutes == 0 else f"{minutes}M"
                    eta_color = colors.GREEN if minutes <= 5 else colors.WHITE
                    destination_color = colors.WHITE
                draw_text(image, (1, y), route, tint(colors.BLUE), max_width=15)
                draw_text(image, (18, y), destination, tint(destination_color), max_width=72)
                draw_text(image, (99, y), eta, tint(eta_color), max_width=28)
                if departure.delay_seconds > 59 and not departure.cancelled:
                    delay_minutes = departure.delay_seconds // 60
                    draw_text(
                        image, (89, y + 6), f"+{delay_minutes}", tint(colors.AMBER), max_width=15
                    )
                state_color = {
                    RealtimeState.REALTIME: colors.GREEN,
                    RealtimeState.SCHEDULED: colors.DIM_WHITE,
                    RealtimeState.STALE: colors.AMBER,
                }[departure.realtime_state]
                draw.point((95, y + 2), fill=tint(state_color))
                if departure.platform:
                    draw_text(image, (89, y), departure.platform, tint(colors.MAGENTA), max_width=4)

        draw.line((0, 54, 127, 54), fill=tint(colors.GREY))
        if snapshot.status.health == FeedHealth.OFFLINE:
            footer, footer_color = "OFFLINE - SCHEDULE", colors.RED
        elif snapshot.status.health == FeedHealth.STALE:
            footer, footer_color = "STALE - SCHEDULE", colors.AMBER
        elif snapshot.alerts:
            footer, footer_color = snapshot.alerts[0].header, colors.AMBER
        elif snapshot.status.using_fallback:
            footer, footer_color = "TIMETABLE ONLY", colors.DIM_WHITE
        else:
            footer, footer_color = "LIVE  QLD DEMO", colors.GREEN
        draw_text(image, (1, 57), abbreviate(footer, 31), tint(footer_color), max_width=126)
        return image
