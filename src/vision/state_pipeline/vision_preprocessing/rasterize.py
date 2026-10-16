"""R08: masks in original pixel coordinates + identical geometric transforms.

Polygon fill uses PIL ImageDraw.polygon(outline=1, fill=1), the same convention
as labelme's ``shape_to_mask``. Holes and exclusions are cleared after their
polygon is filled, so they are preserved.

Mask layers (all separate; none overwrite the source annotation):
  semantic   object class per pixel (existing JSON), uint8, 255 = ignore
  target     physical target index (targets.json), uint8/uint16, 0 = none, max = ignore/overlap
  state      per activity, uint8: 0 not_installed, 1 installed, 255 ignore (opt. 2 occluded)
  visibility uint8: 0 outside ROI, 1 visible, 2 occluded, 3 undeterminable
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from .schemas import exclusion_polys


# ------------------------------------------------------------------ fill
def _canvas(size):
    return Image.new("L", (int(size[0]), int(size[1])), 0)


def fill_simple(point_lists, size) -> np.ndarray:
    """Union of hole-free polygons (point lists)."""
    img = _canvas(size)
    draw = ImageDraw.Draw(img)
    for pts in point_lists:
        if len(pts) >= 3:
            draw.polygon([tuple(map(float, p)) for p in pts], outline=1, fill=1)
        elif len(pts) == 2:  # rectangle diagonals
            (x1, y1), (x2, y2) = pts
            draw.rectangle([min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)], outline=1, fill=1)
    return np.asarray(img, dtype=bool)


def fill_polys(polys, size) -> np.ndarray:
    """Union of polygons with holes ({"exterior", "holes"})."""
    out = np.zeros((int(size[1]), int(size[0])), bool)
    for poly in polys or []:
        img = _canvas(size)
        draw = ImageDraw.Draw(img)
        draw.polygon([tuple(map(float, p)) for p in poly["exterior"]], outline=1, fill=1)
        for hole in poly.get("holes") or []:
            draw.polygon([tuple(map(float, p)) for p in hole], outline=0, fill=0)
        out |= np.asarray(img, dtype=bool)
    return out


def roi_mask(roi: dict, size) -> np.ndarray:
    mask = fill_polys(roi.get("polygons", []), size)
    excl = roi.get("exclusions") or []
    if excl:
        mask &= ~fill_polys(exclusion_polys(excl), size)
    return mask


# -------------------------------------------------------------- semantic
def class_lookup(taxonomy: dict) -> tuple[dict, set, dict]:
    oc = taxonomy["object_classes"]
    by_label = {}
    for c in oc["classes"]:
        for label in c["labels"]:
            by_label[label.strip().casefold()] = c
    ignore = {l.strip().casefold() for l in oc.get("ignore_labels", [])}
    layers = {name: i for i, name in enumerate(oc["layers"]["order_front_to_back"])}
    return by_label, ignore, layers


def semantic_mask(doc: dict, size, taxonomy: dict) -> tuple[np.ndarray, dict]:
    by_label, ignore_labels, layers = class_lookup(taxonomy)
    ignore_value = taxonomy["object_classes"].get("ignore_value", 255)
    per_class: dict[int, list] = {}
    unknown, ignored = {}, 0
    ignore_shapes = []
    for shape in doc.get("shapes", []):
        label = str(shape.get("label", "")).strip().casefold()
        pts = shape.get("points") or []
        if label in ignore_labels:
            ignore_shapes.append(pts)
            ignored += 1
            continue
        c = by_label.get(label)
        if c is None:
            unknown[shape.get("label")] = unknown.get(shape.get("label"), 0) + 1
            ignore_shapes.append(pts)
            continue
        per_class.setdefault(c["id"], []).append(pts)
    classes = {c["id"]: c for c in taxonomy["object_classes"]["classes"]}
    h, w = int(size[1]), int(size[0])
    out = np.full((h, w), ignore_value, np.uint8)
    assigned = np.zeros((h, w), bool)
    stats = {"conflict_px": 0, "occlusion_resolved_px": 0, "unknown_labels": unknown,
             "ignored_label_shapes": ignored, "class_px": {}}
    masks = {cid: fill_simple(pts, size) for cid, pts in per_class.items()}
    covered_any = np.zeros((h, w), bool)
    for layer_name, _ in sorted(layers.items(), key=lambda kv: kv[1]):
        ids = [cid for cid in masks if classes[cid]["layer"] == layer_name]
        if not ids:
            continue
        count = np.zeros((h, w), np.uint8)
        for cid in ids:
            count += masks[cid]
        layer_cover = count > 0
        stats["occlusion_resolved_px"] += int((layer_cover & assigned).sum())
        fresh = layer_cover & ~assigned
        conflict = fresh & (count > 1)
        stats["conflict_px"] += int(conflict.sum())
        for cid in ids:
            sel = fresh & masks[cid] & ~conflict
            out[sel] = cid
        assigned |= fresh  # conflicts stay ignore and are not filled by lower layers
        covered_any |= layer_cover
    if ignore_shapes:
        ign = fill_simple(ignore_shapes, size)
        out[ign & ~covered_any] = ignore_value
    for cid in classes:
        stats["class_px"][classes[cid]["name"]] = int((out == cid).sum())
    stats["unlabeled_px"] = int((~covered_any).sum())
    return out, stats


# ---------------------------------------------------------------- target
def target_dtype(n_targets: int):
    if n_targets <= 254:
        return np.uint8, 255
    if n_targets <= 65534:
        return np.uint16, 65535
    raise OverflowError(f"{n_targets} targets exceed uint16 target mask capacity")


def target_mask(entries: list[tuple[int, dict]], size, n_targets: int) -> tuple[np.ndarray, dict]:
    """entries: (target_index >=1, roi json). Overlapping ROIs -> ignore value."""
    dtype, ignore = target_dtype(n_targets)
    h, w = int(size[1]), int(size[0])
    out = np.zeros((h, w), dtype)
    count = np.zeros((h, w), np.uint16)
    for index, roi in entries:
        if not 1 <= index < ignore:
            raise OverflowError(f"target index {index} outside dtype range")
        m = roi_mask(roi, size)
        out[m] = index
        count += m
    overlap = count > 1
    out[overlap] = ignore
    return out, {"overlap_px": int(overlap.sum()), "dtype": np.dtype(dtype).name, "ignore_value": ignore}


# ----------------------------------------------------------------- state
def state_and_visibility(label: dict | None, roi: np.ndarray, size, auto_occluders: list | None = None,
                         occlusion_class: bool = False, values: dict | None = None) -> tuple[np.ndarray, np.ndarray, dict]:
    values = values or {"not_installed": 0, "installed": 1}
    h, w = int(size[1]), int(size[0])
    state = np.full((h, w), 255, np.uint8)
    vis = np.zeros((h, w), np.uint8)
    vis[roi] = 1
    stats = {"roi_px": int(roi.sum())}
    occl = np.zeros((h, w), bool)
    undet = np.zeros((h, w), bool)
    if label is not None:
        explicit = {name: fill_polys([p for r in label.get("regions", []) if r["state"] == name for p in r["polygons"]], size)
                    for name in values}
        any_explicit = np.zeros((h, w), bool)
        for m in explicit.values():
            any_explicit |= m
        conflict = np.zeros((h, w), bool)
        names = list(values)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                conflict |= explicit[names[i]] & explicit[names[j]]
        fill = label.get("fill_roi_state")
        if fill:
            state[roi & ~any_explicit] = values[fill]
        for name, m in explicit.items():
            state[roi & m] = values[name]
        state[conflict] = 255
        stats["label_conflict_px"] = int((conflict & roi).sum())
        stats["clipped_outside_roi_px"] = int((any_explicit & ~roi).sum())
        undet = fill_polys([p for u in label.get("undeterminable", []) for p in u["polygons"]], size)
        occl = fill_polys([p for o in label.get("occluded", []) for p in o["polygons"]], size)
        if label.get("whole_target_undeterminable"):
            undet |= roi
    if auto_occluders:
        occl |= fill_polys(auto_occluders, size)
    occl &= roi
    undet &= roi
    state[undet] = 255
    state[occl & ~undet] = 2 if occlusion_class else 255
    vis[occl] = 2
    vis[undet] = 3
    stats.update({"occluded_px": int(occl.sum()), "undeterminable_px": int(undet.sum()),
                  "visible_px": int((vis == 1).sum())})
    for name, v in values.items():
        stats[f"{name}_px"] = int((state == v).sum())
    stats["valid_px"] = int(sum(stats[f"{n}_px"] for n in values))
    stats["unlabeled_visible_px"] = int(((vis == 1) & (state == 255)).sum())
    return state, vis, stats


# ------------------------------------------------------------ transforms
def make_transform(src_size, out_size=None, mode: str = "letterbox", crop_box=None) -> dict:
    W0, H0 = int(src_size[0]), int(src_size[1])
    x0, y0, x1, y1 = crop_box if crop_box else (0, 0, W0, H0)
    x0, y0 = max(0, int(np.floor(x0))), max(0, int(np.floor(y0)))
    x1, y1 = min(W0, int(np.ceil(x1))), min(H0, int(np.ceil(y1)))
    cw, ch = x1 - x0, y1 - y0
    if cw <= 0 or ch <= 0:
        raise ValueError("empty crop")
    if out_size is None or mode == "none":
        W, H, sx, sy, nw, nh, pl, pt = cw, ch, 1.0, 1.0, cw, ch, 0, 0
    elif mode == "stretch":
        W, H = map(int, out_size)
        nw, nh, pl, pt = W, H, 0, 0
        sx, sy = W / cw, H / ch
    elif mode == "letterbox":
        W, H = map(int, out_size)
        s = min(W / cw, H / ch)
        nw, nh = max(1, round(cw * s)), max(1, round(ch * s))
        pl, pt = (W - nw) // 2, (H - nh) // 2
        sx, sy = nw / cw, nh / ch
    else:
        raise ValueError(f"unknown resize mode {mode}")
    return {"mode": mode, "src_wh": [W0, H0], "crop_box_xyxy": [x0, y0, x1, y1], "resized_wh": [nw, nh],
            "scale_xy": [sx, sy], "pad_ltrb": [pl, pt, W - nw - pl, H - nh - pt], "out_wh": [W, H],
            "image_resample": "lanczos", "mask_resample": "nearest",
            "forward": "x_out = (x_orig - crop_x0) * scale_x + pad_left ; y likewise",
            "inverse": "x_orig = (x_out - pad_left) / scale_x + crop_x0 ; y likewise"}


def to_original(x: float, y: float, T: dict) -> tuple[float, float]:
    return ((x - T["pad_ltrb"][0]) / T["scale_xy"][0] + T["crop_box_xyxy"][0],
            (y - T["pad_ltrb"][1]) / T["scale_xy"][1] + T["crop_box_xyxy"][1])


def to_output(x: float, y: float, T: dict) -> tuple[float, float]:
    return ((x - T["crop_box_xyxy"][0]) * T["scale_xy"][0] + T["pad_ltrb"][0],
            (y - T["crop_box_xyxy"][1]) * T["scale_xy"][1] + T["pad_ltrb"][1])


def apply_image(img: Image.Image, T: dict, pad_rgb=(0, 0, 0)) -> Image.Image:
    x0, y0, x1, y1 = T["crop_box_xyxy"]
    out = img.crop((x0, y0, x1, y1))
    if tuple(T["resized_wh"]) != out.size:
        out = out.resize(tuple(T["resized_wh"]), Image.LANCZOS)
    if tuple(T["out_wh"]) != tuple(T["resized_wh"]):
        canvas = Image.new("RGB", tuple(T["out_wh"]), pad_rgb)
        canvas.paste(out, (T["pad_ltrb"][0], T["pad_ltrb"][1]))
        out = canvas
    return out


def apply_mask(mask: np.ndarray, T: dict, pad_value: int) -> np.ndarray:
    x0, y0, x1, y1 = T["crop_box_xyxy"]
    m = mask[y0:y1, x0:x1]
    nw, nh = T["resized_wh"]
    if (m.shape[1], m.shape[0]) != (nw, nh):
        mode = "I;16" if m.dtype == np.uint16 else "L"
        m = np.asarray(Image.fromarray(m, mode=mode).resize((nw, nh), Image.NEAREST), dtype=mask.dtype)
    W, H = T["out_wh"]
    if (W, H) != (nw, nh):
        canvas = np.full((H, W), pad_value, mask.dtype)
        pl, pt = T["pad_ltrb"][:2]
        canvas[pt:pt + nh, pl:pl + nw] = m
        m = canvas
    return m


def save_mask_png(mask: np.ndarray, path):
    if mask.dtype == np.uint16:
        Image.fromarray(mask, mode="I;16").save(path, optimize=False)
    elif mask.dtype == np.uint8:
        Image.fromarray(mask, mode="L").save(path, optimize=False)
    else:
        raise TypeError(f"unsupported mask dtype {mask.dtype}")


def load_mask_png(path) -> np.ndarray:
    with Image.open(path) as im:
        if im.mode in ("I;16", "I;16B", "I"):
            return np.asarray(im, dtype=np.uint16)
        return np.asarray(im, dtype=np.uint8)


# --------------------------------------------------------------- overlay
PALETTE = [(230, 159, 0), (86, 180, 233), (0, 158, 115), (240, 228, 66), (0, 114, 178), (213, 94, 0),
           (204, 121, 167), (120, 120, 120), (160, 60, 220), (60, 200, 200)]


def render_overlay(rgb: Image.Image, semantic: np.ndarray | None, taxonomy: dict, rois: dict[str, dict],
                   occluders: dict[str, list], state: dict[str, np.ndarray] | None, title: str,
                   max_side: int = 1100, hints: list | None = None) -> Image.Image:
    """Three panels: object classes | target ROIs (+exclusions, occluder candidates) | state draft."""
    scale = min(1.0, max_side / max(rgb.size))
    size = (round(rgb.width * scale), round(rgb.height * scale))
    base = rgb.resize(size, Image.BILINEAR)
    panels = []
    # panel 1: semantic
    p1 = base.copy()
    if semantic is not None:
        sem = np.asarray(Image.fromarray(semantic).resize(size, Image.NEAREST))
        color = np.zeros((*sem.shape, 3), np.uint8)
        alpha = np.zeros(sem.shape, np.uint8)
        for c in taxonomy["object_classes"]["classes"]:
            sel = sem == c["id"]
            color[sel] = PALETTE[c["id"] % len(PALETTE)]
            alpha[sel] = 110
        p1 = Image.composite(Image.fromarray(color), p1, Image.fromarray(alpha))
    dl = ImageDraw.Draw(p1)
    for k, c in enumerate(taxonomy["object_classes"]["classes"]):
        y = 6 + 14 * k
        dl.rectangle([6, y, 18, y + 10], fill=PALETTE[c["id"] % len(PALETTE)])
        dl.text((22, y - 1), f"{c['id']} {c['name']}", fill=(255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0))
    panels.append(("object classes (existing JSON, pseudo-label)", p1))
    # panel 2: ROIs
    p2 = base.copy().convert("RGBA")
    over = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    for hint in hints or []:
        if hint.get("points"):
            d.line([(x * scale, y * scale) for x, y in hint["points"] + hint["points"][:1]], fill=(255, 255, 255, 160), width=1)
    for i, (tid, roi) in enumerate(rois.items()):
        col = PALETTE[(i + 3) % len(PALETTE)]
        for poly in roi.get("polygons", []):
            pts = [(x * scale, y * scale) for x, y in poly["exterior"]]
            d.polygon(pts, fill=col + (60,), outline=col + (255,))
            d.line(pts + pts[:1], fill=col + (255,), width=3)
            for hole in poly.get("holes", []):
                d.polygon([(x * scale, y * scale) for x, y in hole], fill=(0, 0, 0, 90), outline=(255, 0, 0, 255))
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            d.text((cx, cy), tid, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))
        for ex in roi.get("exclusions", []):
            d.polygon([(x * scale, y * scale) for x, y in ex["points"]], fill=(0, 0, 0, 110), outline=(255, 0, 0, 255))
        for poly in occluders.get(tid, []):
            d.polygon([(x * scale, y * scale) for x, y in poly["exterior"]], fill=(255, 0, 255, 90))
    p2 = Image.alpha_composite(p2, over).convert("RGB")
    panels.append(("target ROIs / exclusions(red) / occluder cand.(magenta)", p2))
    # panel 3: state
    p3 = base.copy()
    if state:
        color = np.zeros((size[1], size[0], 3), np.uint8)
        alpha = np.zeros((size[1], size[0]), np.uint8)
        for m in state.values():
            sm = np.asarray(Image.fromarray(m).resize(size, Image.NEAREST))
            color[sm == 1] = (0, 200, 0)
            color[sm == 0] = (220, 0, 0)
            color[sm == 2] = (200, 0, 200)
            alpha[sm != 255] = 120
        p3 = Image.composite(Image.fromarray(color), p3, Image.fromarray(alpha))
        panels.append(("state DRAFT (green installed / red not / none = ignore)", p3))
    else:
        panels.append(("state: no labels (all ignore=255)", p3))
    W = sum(p.width for _, p in panels) + 10 * (len(panels) - 1)
    canvas = Image.new("RGB", (W, size[1] + 40), (25, 25, 25))
    x = 0
    dd = ImageDraw.Draw(canvas)
    dd.text((6, 4), title, fill=(255, 255, 255))
    for name, p in panels:
        canvas.paste(p, (x, 40))
        dd.text((x + 6, 22), name, fill=(220, 220, 220))
        x += p.width + 10
    return canvas
