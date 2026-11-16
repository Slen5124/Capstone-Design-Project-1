"""Transfer a reviewed 6-state/8-state checkpoint by class NAME, never channel position."""
from layout import LEGACY_CLASSES, STATE_CLASSES


def transfer_weights(model, checkpoint, config):
    if checkpoint.get("encoder") != config["encoder"] or checkpoint.get("decoder") != config["decoder"]:
        raise ValueError("Initialization encoder/decoder mismatch")
    source_classes = checkpoint.get("state_classes")
    if source_classes not in (LEGACY_CLASSES, STATE_CLASSES) or checkpoint.get("n_state") != len(source_classes):
        raise ValueError("Initialization needs explicit supported class names")
    if checkpoint.get("vit_pad_crop") is not True or checkpoint.get("n_sem") != 8:
        raise ValueError("Initialization geometry / semantic head mismatch")
    source = checkpoint["state_dict"]
    destination = model.state_dict()
    transferred = {}
    for key, value in source.items():
        if key.startswith("encoder."):
            raise ValueError("Use the established decoder/head-only checkpoint for initialization")
        if key not in destination:
            raise ValueError("Unexpected initialization parameter: " + key)
        if key in ("state_head.weight", "state_head.bias"):
            if value.shape[0] != len(source_classes) or value.shape[1:] != destination[key].shape[1:]:
                raise ValueError("State-head shape mismatch")
            target = destination[key].clone()
            for src_idx, name in enumerate(source_classes):
                target[STATE_CLASSES.index(name)] = value[src_idx]
            transferred[key] = target
        else:
            if value.shape != destination[key].shape:
                raise ValueError("Initialization parameter shape mismatch: " + key)
            transferred[key] = value
    missing, unexpected = model.load_state_dict(transferred, strict=False)
    bad = [k for k in missing if not k.startswith("encoder.")] + list(unexpected)
    if bad:
        raise ValueError("Missing initialization parameters: " + str(bad[:5]))
    return {"copied_state_classes": source_classes,
            "new_random_state_classes": [name for name in STATE_CLASSES if name not in source_classes]}
