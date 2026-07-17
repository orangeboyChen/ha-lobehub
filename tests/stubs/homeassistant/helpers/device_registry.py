"""Device registry placeholder for entry cleanup."""


def async_get(hass):
    return object()


def async_entries_for_config_entry(registry, entry_id):
    return []
