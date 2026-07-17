"""Service target placeholders used by target-resolution helpers."""


class TargetSelection:
    def __init__(self, data):
        self.has_any_target = bool(data.get("target"))


def async_extract_referenced_entity_ids(hass, target_selection):
    raise NotImplementedError("Target extraction is covered by Home Assistant's own tests")
