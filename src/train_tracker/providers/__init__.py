"""Departure data providers."""

from train_tracker.providers.base import DepartureProvider
from train_tracker.providers.simulated import SimulatedProvider

__all__ = ["DepartureProvider", "SimulatedProvider"]
