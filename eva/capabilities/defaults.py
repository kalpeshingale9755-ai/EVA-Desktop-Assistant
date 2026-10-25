"""The list of capabilities EVA starts with. To add a new one: write its file, add one line here."""
from __future__ import annotations

from eva.capabilities.app_index import AppIndex
from eva.capabilities.app_settings import AppSettings
from eva.capabilities.clock import ClockCapability
from eva.capabilities.close_app import CloseAppCapability
from eva.capabilities.file_index import FileIndex
from eva.capabilities.file_settings import FileSettings
from eva.capabilities.open_app import OpenAppCapability
from eva.capabilities.open_file import OpenFileCapability
from eva.capabilities.system_probe import WindowsProbe
from eva.core.registry import CapabilityRegistry


def register_default_capabilities(registry: CapabilityRegistry, probe=None,
                                  app_settings: AppSettings | None = None,
                                  file_settings: FileSettings | None = None,
                                  refresh_apps: bool = True) -> AppIndex:
    """refresh_apps=False (used by tests) also skips the background scan of your folders."""
    probe = probe or WindowsProbe()
    settings = app_settings or AppSettings()
    files = file_settings or FileSettings()

    index = AppIndex(probe, settings)
    index.load_cache()               # fast: the saved list from last time
    file_index = FileIndex(files)
    file_index.load_cache()
    if refresh_apps:
        index.ensure_fresh()         # background: re-read the Start menu if the list is old or missing
        file_index.ensure_fresh()    # background: re-scan your folders if the list is old or missing

    registry.register(OpenAppCapability(index, probe, settings))
    registry.register(OpenFileCapability(file_index, probe, index, settings, files))
    registry.register(CloseAppCapability(probe, index, settings))
    registry.register(ClockCapability())
    return index
