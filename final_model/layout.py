"""Versioned class layouts. Never reinterpret an old checkpoint by changing its channel count."""
LEGACY_CLASSES = ["wall_not_installed", "wall_installed", "ceiling_not_installed",
                  "ceiling_installed", "occluded", "not_target"]
STATE_CLASSES = LEGACY_CLASSES[:4] + ["floor_not_installed", "floor_installed"] + LEGACY_CLASSES[4:]
SURFACES = {"wall": (0, 1, "wall_board_installation"),
            "ceiling": (2, 3, "ceiling_panel_installation"),
            "floor": (4, 5, "floor_finish_installation")}


def classes_for(config):
    if config["n_state"] == 6:
        return LEGACY_CLASSES
    if config["n_state"] == 8:
        return STATE_CLASSES
    raise ValueError("Only explicit 6-state legacy and 8-state layouts are supported")


def surfaces_for(config):
    return {key: value for key, value in SURFACES.items() if value[1] < config["n_state"] - 2}
