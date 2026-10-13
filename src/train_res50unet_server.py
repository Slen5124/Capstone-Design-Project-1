import os
import sys

# 서버의 ~/.local 폴더 패키지 간섭을 차단하고 가상환경(alo-train) 패키지를 강제 로드
sys.path = [p for p in sys.path if ".local" not in p]

# Windows 콘솔 한글 인코딩 방지
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import glob
import json
import random
from datetime import datetime
import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from tqdm import tqdm

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

# =============================================================================
# 1. 설정 (Settings)
# =============================================================================
# 프로젝트 루트 디렉토리 기준 경로 설정 (어디서 실행해도 동작하도록 보장)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
IMG_DIR = os.path.join(BASE_DIR, "data", "raw_images")       # data/raw_images/<도시>/*.jpg
JSON_DIR = os.path.join(BASE_DIR, "data", "processed_json")  # data/processed_json/<도시>/*.json

# 모델별로 결과 폴더를 분리 (기존 일반 U-Net 결과와 섞이지 않도록)
MODEL_TAG = "res50_unet"
WEIGHTS_DIR = os.path.join(BASE_DIR, "weights", MODEL_TAG)
RUNS_DIR = os.path.join(BASE_DIR, "runs", MODEL_TAG)
VIZ_DIR = os.path.join(RUNS_DIR, "visualizations")
CKPT_PREFIX = "res50unet_epoch_"  # 체크포인트 파일명: res50unet_epoch_<N>.pth

IMG_SIZE = (512, 512)
BATCH_SIZE = 16          # VRAM 부족(OOM) 시 8로 낮추세요
EPOCHS = 100
LEARNING_RATE = 1e-4     # 사전학습 인코더를 미세조정하므로 일반 U-Net(2e-4)보다 낮게 설정
NUM_WORKERS = 4          # 데이터 로딩에 사용할 CPU 프로세스 수 (서버 4, 로컬 1 권장)
USE_AUGMENTATION = True  # True: 학습 시 좌우 반전(Horizontal Flip) 증강 적용 (도시 간 일반화에 도움)
RESUME = True            # True: WEIGHTS_DIR의 가장 최신 체크포인트부터 이어서 학습
CHECKPOINT_PATH = ""     # 특정 체크포인트를 직접 지정할 경우 경로 입력 (비워두면 자동 탐색)

# =============================================================================
# 모델 설정 (ResNet50 인코더 + U-Net 디코더, ImageNet 사전학습 가중치)
# =============================================================================
ENCODER_NAME = "resnet50"
ENCODER_WEIGHTS = "imagenet"  # None 으로 두면 사전학습 없이 무작위 초기화
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# =============================================================================
# 데이터 분할 설정 (도시 폴더 단위 분할 / City-level Split)
# - 비슷한 사진이 많은 데이터 특성상, 같은 현장의 사진이 Train/Val/Test에 섞이면
#   성능이 과대평가되므로 "현장(도시) 단위"로 완전히 분리합니다.
# =============================================================================
TRAIN_CITIES = ["Costa_Mesa", "Brea"]
VAL_CITIES = ["Las_Vegas"]     # 최고 모델(best) 선정 기준
TEST_CITIES = ["Venice"]       # 처음 보는 현장에 대한 최종 일반화 성능 보고용
EVAL_TEST_EACH_EPOCH = True    # True: 매 에포크마다 Test Loss/Acc/mIoU도 함께 출력
PRINT_CLASS_IOU = False        # True: 매 에포크 완료 시 클래스별 세부 IoU(겹침 점수) 표 출력
VIZ_SAMPLES_PER_SET = 2        # 시각화용으로 Val/Test 세트에서 각각 고정 추적할 샘플 수

# =============================================================================
# 에포크 기록 및 체크포인트 저장 주기 설정
# =============================================================================
SAVE_THRESHOLD_EPOCH = 80  # 저장 주기가 전환되는 기준 에포크
SAVE_INTERVAL_LOW = 5      # 기준 에포크 이하일 때 저장 주기: 5 에포크마다
SAVE_INTERVAL_HIGH = 1     # 기준 에포크 초과일 때 저장 주기: 1 에포크마다

# SAM3 전처리 시 사용했던 태그 리스트
# 0번(배경)을 포함하기 위해 1부터 번호를 부여합니다.
CLASSES = [
    "floor",
    "metal stud framing",
    "wall",
    "ceiling",
    "HVAC ductwork",
    "person",
    "construction materials",
    "Tools",
    "unidentifiable",
]
CLASS_MAP = {name: i + 1 for i, name in enumerate(CLASSES)}
NUM_CLASSES = len(CLASSES) + 1  # 배경(0) + 9개 클래스 = 총 10개

# 시각화용 클래스별 고유 색상 (RGB)
CLASS_COLORS = np.array([
    [30, 30, 30],       # 0: Background (어두운 회색)
    [46, 204, 113],     # 1: floor (초록)
    [52, 152, 219],     # 2: metal stud framing (파랑)
    [230, 126, 34],     # 3: wall (주황)
    [155, 89, 182],     # 4: ceiling (보라)
    [26, 188, 156],     # 5: HVAC ductwork (청록)
    [231, 76, 60],      # 6: person (빨강)
    [241, 196, 15],     # 7: construction materials (노랑)
    [52, 73, 94],       # 8: Tools (남색)
    [149, 165, 166],    # 9: unidentifiable (회색)
], dtype=np.uint8)


def imread_unicode(path):
    """Windows 한글 경로 및 Linux 환경 모두에서 안전하게 이미지를 로드하는 함수"""
    try:
        img_array = np.fromfile(path, np.uint8)
        return cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    except Exception:
        return None


def city_of(img_path):
    """이미지 경로의 상위 폴더명(= 도시 이름)을 반환"""
    return os.path.basename(os.path.dirname(img_path))


def json_path_of(img_path, json_dir=JSON_DIR):
    """data/raw_images/<도시>/<이름>.jpg -> data/processed_json/<도시>/<이름>.json"""
    stem = os.path.splitext(os.path.basename(img_path))[0]
    return os.path.join(json_dir, city_of(img_path), stem + ".json")


# =============================================================================
# 2. 커스텀 데이터셋 (Dataset)
# =============================================================================
class SegmentationDataset(Dataset):
    def __init__(self, img_paths, json_dir=JSON_DIR, class_map=CLASS_MAP, img_size=IMG_SIZE, augment=False):
        self.img_paths = list(img_paths)
        self.json_dir = json_dir
        self.class_map = class_map
        self.img_size = img_size
        self.augment = augment

    def __len__(self):
        return len(self.img_paths)

    def load_raw(self, idx):
        """정규화 전 (RGB uint8 이미지, 마스크) 반환. 깨진 파일은 다음 이미지로 자동 우회"""
        for offset in range(len(self.img_paths)):
            img_path = self.img_paths[(idx + offset) % len(self.img_paths)]
            image = imread_unicode(img_path)
            if image is None or image.size == 0:
                continue
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            orig_h, orig_w = image.shape[:2]

            # 마스크 생성 (배경은 0으로 초기화)
            mask = np.zeros((orig_h, orig_w), dtype=np.uint8)
            json_path = json_path_of(img_path, self.json_dir)
            if os.path.exists(json_path):
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    for shape in data.get('shapes', []):
                        class_id = self.class_map.get(shape['label'], 0)  # 매핑 안되면 배경(0) 처리
                        points = np.array(shape['points'], dtype=np.int32)
                        cv2.fillPoly(mask, [points], class_id)
                except Exception:
                    # 중간에 끊기거나 손상된 JSON 파일은 배경으로 처리하여 학습 중단 방지
                    pass

            image = cv2.resize(image, self.img_size, interpolation=cv2.INTER_LINEAR)
            # 마스크는 픽셀값이 클래스 ID이므로 NEAREST 보간을 사용해야 합니다.
            mask = cv2.resize(mask, self.img_size, interpolation=cv2.INTER_NEAREST)
            return image, mask
        raise RuntimeError("읽을 수 있는 이미지가 하나도 없습니다.")

    def __getitem__(self, idx):
        image, mask = self.load_raw(idx)

        if self.augment and random.random() < 0.5:
            image = np.ascontiguousarray(image[:, ::-1])
            mask = np.ascontiguousarray(mask[:, ::-1])

        # ImageNet 사전학습 인코더에 맞춘 정규화 (0~1 스케일 후 mean/std)
        image = (image.astype(np.float32) / 255.0 - IMAGENET_MEAN) / IMAGENET_STD
        image = torch.from_numpy(image).permute(2, 0, 1).float()  # HWC -> CHW
        mask = torch.from_numpy(mask).long()
        return image, mask


# =============================================================================
# 3. 모델 정의 (ResNet50-UNet, ImageNet 사전학습)
# =============================================================================
def build_model(encoder_weights=ENCODER_WEIGHTS):
    """
    segmentation_models_pytorch 의 U-Net 구조에 ResNet50 인코더를 연결합니다.
    encoder_weights="imagenet" 이면 최초 실행 시 사전학습 가중치를 인터넷에서 내려받아 캐시합니다.
    (평가/이어 학습 시에는 체크포인트로 덮어쓰므로 encoder_weights=None 으로 생성해도 됩니다.)
    """
    try:
        import segmentation_models_pytorch as smp
    except ImportError:
        print("[오류] segmentation_models_pytorch 가 설치되어 있지 않습니다.")
        print("       conda activate alo-train 후 다음을 실행하세요: pip install segmentation-models-pytorch")
        raise

    try:
        return smp.Unet(
            encoder_name=ENCODER_NAME,
            encoder_weights=encoder_weights,
            in_channels=3,
            classes=NUM_CLASSES,
        )
    except Exception as e:
        print(f"[오류] ImageNet 사전학습 가중치를 불러오지 못했습니다: {e}")
        print("       GPU 노드에서 인터넷이 막혀 있다면, 로그인 서버(인터넷 가능)에서 먼저 한 번 실행해")
        print("       가중치를 캐시(~/.cache)에 내려받은 뒤 다시 실행하세요.")
        raise


def load_model_weights(model, path, device):
    """체크포인트(dict 형식) 또는 순수 state_dict 파일 모두에서 모델 가중치를 로드"""
    ckpt = torch.load(path, map_location=device)
    state = ckpt["model"] if isinstance(ckpt, dict) and "model" in ckpt else ckpt
    model.load_state_dict(state)
    return ckpt


# =============================================================================
# 4. 도시별 데이터 분할 및 시각화 도구 (Utilities & Visualization)
# =============================================================================
def collect_city_paths(cities, img_dir=IMG_DIR):
    """지정한 도시 폴더들의 이미지 경로를 정렬된 리스트로 반환"""
    paths = []
    for city in cities:
        city_dir = os.path.join(img_dir, city)
        if not os.path.isdir(city_dir):
            print(f"[경고] 도시 폴더를 찾을 수 없습니다: {city_dir}")
            continue
        paths.extend(sorted(glob.glob(os.path.join(city_dir, "*.jpg"))))
    return paths


def split_dataset_by_city_folders(img_dir=IMG_DIR):
    """
    도시 폴더 단위로 Train / Val / Test 를 분리합니다.
    반환값: (train_paths, val_paths, test_paths)
    """
    groups = {"Train": TRAIN_CITIES, "Val": VAL_CITIES, "Test": TEST_CITIES}
    overlap = set(TRAIN_CITIES) & set(VAL_CITIES) | set(TRAIN_CITIES) & set(TEST_CITIES) | set(VAL_CITIES) & set(TEST_CITIES)
    if overlap:
        raise ValueError(f"같은 도시가 여러 세트에 중복 지정되었습니다: {overlap}")

    result = {}
    print("\n" + "=" * 75)
    print(" [*] 도시 폴더 단위 데이터셋 분할 (City-level Split)")
    print("=" * 75)
    for name, cities in groups.items():
        paths = collect_city_paths(cities, img_dir)
        result[name] = paths
        detail = ", ".join(f"{c}: {len(collect_city_paths([c], img_dir)):,}장" for c in cities)
        print(f"  - {name:<5}: {len(paths):>6,}장  ({detail})")
    print("=" * 75 + "\n")

    if not result["Train"] or not result["Val"]:
        raise RuntimeError(f"Train 또는 Val 이미지가 없습니다. IMG_DIR 경로를 확인하세요: {img_dir}")
    return result["Train"], result["Val"], result["Test"]


def mask_to_rgb(mask_2d):
    """(H, W) 정수 마스크를 (H, W, 3) RGB 컬러 이미지로 변환"""
    h, w = mask_2d.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    for c in range(NUM_CLASSES):
        rgb[mask_2d == c] = CLASS_COLORS[c]
    return rgb


def select_fixed_samples(dataset, n_samples):
    """데이터셋 전체에서 균등 간격으로, 라벨이 1개 이상 있는 샘플을 n장 선정하여 고정 추적"""
    if len(dataset) == 0 or n_samples <= 0:
        return []
    selected = []
    step = max(len(dataset) // n_samples, 1)
    for start in range(0, len(dataset), step):
        for idx in range(start, min(start + step, len(dataset))):
            json_file = json_path_of(dataset.img_paths[idx], dataset.json_dir)
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    if len(json.load(f).get('shapes', [])) > 0:
                        selected.append(idx)
                        break
            except Exception:
                continue
        if len(selected) >= n_samples:
            break
    return selected


def save_visualizations(model, dataset, sample_indices, set_name, epoch, save_dir, device):
    """
    고정 샘플에 대해 [원본 | 정답 라벨 | 모델 예측 | 오버레이] 비교 시각자료 생성
    """
    if not HAS_MATPLOTLIB:
        print("[주의] matplotlib이 설치되어 있지 않아 시각자료 생성을 건너뜁니다. (pip install matplotlib 권장)")
        return

    model.eval()
    os.makedirs(save_dir, exist_ok=True)

    with torch.no_grad():
        for k, sample_idx in enumerate(sample_indices, start=1):
            image_t, mask_t = dataset[sample_idx]
            with torch.amp.autocast("cuda", enabled=(device.type == "cuda")):
                output = model(image_t.unsqueeze(0).to(device))
            pred = torch.argmax(output, dim=1).squeeze(0).cpu().numpy()

            # 정규화를 되돌려 원본 이미지 복원
            orig_img = image_t.permute(1, 2, 0).cpu().numpy() * IMAGENET_STD + IMAGENET_MEAN
            orig_img = (np.clip(orig_img, 0, 1) * 255.0).astype(np.uint8)
            gt_rgb = mask_to_rgb(mask_t.cpu().numpy())
            pred_rgb = mask_to_rgb(pred)
            overlay = (0.5 * orig_img + 0.5 * pred_rgb).astype(np.uint8)

            fig, axes = plt.subplots(1, 4, figsize=(20, 5.5))
            panels = [
                (orig_img, "1. Original Image"),
                (gt_rgb, "2. Ground Truth Mask"),
                (pred_rgb, f"3. Res50-UNet Prediction (Epoch {epoch})"),
                (overlay, "4. Overlay (Image + Pred)"),
            ]
            for ax, (img, title) in zip(axes, panels):
                ax.imshow(img)
                ax.set_title(title, fontsize=13, fontweight='bold')
                ax.axis('off')

            legend_patches = [
                mpatches.Patch(color=CLASS_COLORS[i] / 255.0, label=f"{i}: {('bg' if i == 0 else CLASSES[i - 1])}")
                for i in range(NUM_CLASSES)
            ]
            fig.legend(handles=legend_patches, loc='lower center', ncol=5, bbox_to_anchor=(0.5, -0.05), fontsize=10)

            city = city_of(dataset.img_paths[sample_idx])
            plt.suptitle(f"[{set_name} - {city} #{k}] Segmentation Comparison - Epoch {epoch}", fontsize=15, fontweight='bold')
            plt.tight_layout(rect=[0, 0.08, 1, 0.95])

            save_path = os.path.join(save_dir, f"epoch_{epoch:02d}_{set_name.lower()}_{city}_{k}.png")
            plt.savefig(save_path, dpi=130, bbox_inches='tight')
            plt.close()


def evaluate(model, loader, criterion, device, desc="Eval"):
    """
    주어진 데이터셋(Val 또는 Test)에 대해 Loss / Pixel Accuracy / mIoU 를 계산
    반환값: (avg_loss, pixel_acc(%), miou(%), class_iou 배열(%), 클래스별 IoU 계산 가능 여부 배열)
    """
    model.eval()
    total_loss = 0.0
    n_samples = 0
    pixel_correct = 0
    total_pixels = 0
    class_intersections = np.zeros(NUM_CLASSES)
    class_unions = np.zeros(NUM_CLASSES)

    with torch.no_grad():
        for images, masks in tqdm(loader, desc=desc):
            images = images.to(device)
            masks = masks.to(device)

            with torch.amp.autocast("cuda", enabled=(device.type == "cuda")):
                outputs = model(images)
                loss = criterion(outputs, masks)

            total_loss += loss.item() * len(images)
            n_samples += len(images)
            preds = torch.argmax(outputs, dim=1)

            pixel_correct += (preds == masks).sum().item()
            total_pixels += masks.numel()

            for c in range(NUM_CLASSES):
                class_intersections[c] += ((preds == c) & (masks == c)).sum().item()
                class_unions[c] += ((preds == c) | (masks == c)).sum().item()

    avg_loss = total_loss / max(n_samples, 1)
    pixel_acc = (pixel_correct / max(total_pixels, 1)) * 100
    valid_classes = class_unions > 0
    class_iou = np.zeros(NUM_CLASSES)
    class_iou[valid_classes] = class_intersections[valid_classes] / class_unions[valid_classes] * 100
    miou = class_iou[valid_classes].mean() if valid_classes.any() else 0.0
    return avg_loss, pixel_acc, miou, class_iou, valid_classes


def format_class_iou_table(val_class_iou, test_class_iou=None):
    """
    Validation 및 Test 세트의 클래스별 세부 IoU(겹침 점수)를 표 형식으로 정렬하여 출력용 문자열 생성
    """
    all_names = ["background"] + CLASSES
    has_test = test_class_iou is not None
    col_name_w, col_iou_w = 26, 14

    if has_test:
        border = f"      +{'-' * (col_name_w + 2)}+{'-' * (col_iou_w + 2)}+{'-' * (col_iou_w + 2)}+"
        title = f"      | {'Class Name':<{col_name_w}} | {'Val IoU (%)':^{col_iou_w}} | {'Test IoU (%)':^{col_iou_w}} |"
    else:
        border = f"      +{'-' * (col_name_w + 2)}+{'-' * (col_iou_w + 2)}+"
        title = f"      | {'Class Name':<{col_name_w}} | {'Val IoU (%)':^{col_iou_w}} |"

    lines = ["", "      [클래스별 세부 겹침 스코어 (Class-wise IoU)]", border, title, border]
    for c, name in enumerate(all_names):
        v_val = f"{val_class_iou[c]:.2f}%"
        if has_test:
            t_val = f"{test_class_iou[c]:.2f}%"
            lines.append(f"      | {name:<{col_name_w}} | {v_val:^{col_iou_w}} | {t_val:^{col_iou_w}} |")
        else:
            lines.append(f"      | {name:<{col_name_w}} | {v_val:^{col_iou_w}} |")
    lines.append(border)
    return "\n".join(lines)


def _to_plot_values(values):
    """history 안의 None(기록 없는 에포크)을 NaN으로 바꿔 그래프에서 빈 구간으로 표시"""
    return [float('nan') if v is None else v for v in values]


def plot_training_curves(history, save_path):
    """Train/Val/Test Loss, Pixel Acc, mIoU 학습 곡선 그래프 자동 업데이트"""
    if not HAS_MATPLOTLIB or not history.get("epochs"):
        return

    epochs = history["epochs"]
    has_test = any(v is not None for v in history.get("test_loss", []))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5))

    ax1.plot(epochs, _to_plot_values(history["train_loss"]), marker='o', color='#e74c3c', label='Train Loss', linewidth=2)
    ax1.plot(epochs, _to_plot_values(history["val_loss"]), marker='s', color='#2980b9', label='Val Loss (Las_Vegas)', linewidth=2)
    if has_test:
        ax1.plot(epochs, _to_plot_values(history["test_loss"]), marker='x', color='#f39c12', label='Test Loss (Venice)', linewidth=2)
    ax1.set_title("Train / Validation / Test Loss", fontsize=13, fontweight='bold')
    ax1.set_xlabel("Epoch", fontsize=11)
    ax1.set_ylabel("Loss", fontsize=11)
    ax1.grid(True, linestyle='--', alpha=0.6)
    ax1.legend(fontsize=10)

    ax2.plot(epochs, _to_plot_values(history["val_pixel_acc"]), marker='^', color='#27ae60', label='Val Pixel Acc (%)', linewidth=2)
    ax2.plot(epochs, _to_plot_values(history["val_miou"]), marker='d', color='#8e44ad', label='Val mIoU (%)', linewidth=2)
    if has_test:
        ax2.plot(epochs, _to_plot_values(history["test_pixel_acc"]), linestyle='--', marker='^', color='#82e0aa', label='Test Pixel Acc (%)', linewidth=1.5)
        ax2.plot(epochs, _to_plot_values(history["test_miou"]), linestyle='--', marker='d', color='#c39bd3', label='Test mIoU (%)', linewidth=1.5)
    ax2.set_title("Accuracy & mIoU (Val / Test)", fontsize=13, fontweight='bold')
    ax2.set_xlabel("Epoch", fontsize=11)
    ax2.set_ylabel("Percentage (%)", fontsize=11)
    ax2.grid(True, linestyle='--', alpha=0.6)
    ax2.legend(fontsize=10)

    plt.tight_layout()
    plt.savefig(save_path, dpi=130)
    plt.close()


def update_epoch_summary_txt(history, save_path, model_name="Res50-UNet", data_info=None):
    """
    100+ 에포크 학습 시에도 메모장(notepad)으로 전체 진행 추이와 최고 성능을
    한눈에 스크롤하며 일괄 확인할 수 있도록 1행 1에포크 형식의 요약 표를 작성/갱신합니다.
    (Windows 메모장 호환을 위해 UTF-8 with BOM(utf-8-sig) 인코딩 사용)
    """
    if not history or not history.get("epochs"):
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    has_test = any(v is not None for v in history.get("test_loss", []))

    lines = []
    lines.append("=" * 130)
    lines.append(f"  [{model_name} 전체 에포크 학습 결과 일괄 요약표 (Epoch Summary Table)]")
    lines.append(f"  - 최근 갱신 시각: {now_str}")
    if data_info:
        for k, v in data_info.items():
            lines.append(f"  - {k:<15}: {v}")
    lines.append("=" * 130)

    if has_test:
        header = f"{'Epoch':^7} | {'Train Loss':^12} | {'Val Loss':^10} | {'Val Acc':^10} | {'Val mIoU':^10} | {'Test Loss':^10} | {'Test Acc':^10} | {'Test mIoU':^10} | {'비고 (Best 갱신)':<22}"
        sep    = f"{'-'*7}-+-{'-'*12}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*22}"
    else:
        header = f"{'Epoch':^7} | {'Train Loss':^12} | {'Val Loss':^10} | {'Val Acc':^10} | {'Val mIoU':^10} | {'비고 (Best 갱신)':<25}"
        sep    = f"{'-'*7}-+-{'-'*12}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*25}"

    lines.append(header)
    lines.append(sep)

    best_val_loss = float('inf')
    best_loss_ep = None
    best_val_miou = -1.0
    best_miou_ep = None

    epochs = history["epochs"]
    n_records = len(epochs)

    for i in range(n_records):
        ep = epochs[i]
        tr_loss = history["train_loss"][i]
        va_loss = history["val_loss"][i]
        va_acc = history["val_pixel_acc"][i]
        va_miou = history["val_miou"][i]

        te_loss = history["test_loss"][i] if has_test else None
        te_acc = history["test_pixel_acc"][i] if has_test else None
        te_miou = history["test_miou"][i] if has_test else None

        notes = []
        if va_loss is not None and va_loss < best_val_loss:
            best_val_loss = va_loss
            best_loss_ep = ep
            notes.append("★Best Loss")
        if va_miou is not None and va_miou > best_val_miou:
            best_val_miou = va_miou
            best_miou_ep = ep
            notes.append("★Best mIoU")

        note_str = " ".join(notes)
        tr_str = f"{tr_loss:.4f}" if tr_loss is not None else "-"
        va_l_str = f"{va_loss:.4f}" if va_loss is not None else "-"
        va_a_str = f"{va_acc:.2f}%" if va_acc is not None else "-"
        va_m_str = f"{va_miou:.2f}%" if va_miou is not None else "-"

        if has_test:
            te_l_str = f"{te_loss:.4f}" if te_loss is not None else "-"
            te_a_str = f"{te_acc:.2f}%" if te_acc is not None else "-"
            te_m_str = f"{te_miou:.2f}%" if te_miou is not None else "-"
            row = f"{ep:^7d} | {tr_str:^12} | {va_l_str:^10} | {va_a_str:^10} | {va_m_str:^10} | {te_l_str:^10} | {te_a_str:^10} | {te_m_str:^10} | {note_str:<22}"
        else:
            row = f"{ep:^7d} | {tr_str:^12} | {va_l_str:^10} | {va_a_str:^10} | {va_m_str:^10} | {note_str:<25}"
        lines.append(row)

    lines.append(sep)
    lines.append("")
    lines.append("  [전체 기간 최고 성능 달성 에포크]")
    if best_loss_ep is not None:
        lines.append(f"  - 최저 Val Loss 달성: Epoch {best_loss_ep:>3d} (Loss: {best_val_loss:.4f})  -> 'res50unet_best.pth' 기준")
    if best_miou_ep is not None:
        lines.append(f"  - 최고 Val mIoU 달성: Epoch {best_miou_ep:>3d} (mIoU: {best_val_miou:.2f}%)")
    lines.append("=" * 130)

    with open(save_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines) + "\n")


def append_epoch_detail_txt(save_path, epoch, total_epochs,
                            train_loss, val_loss, val_acc, val_miou,
                            test_loss=None, test_acc=None, test_miou=None,
                            val_class_iou=None, test_class_iou=None,
                            is_best=False):
    """
    각 에포크별 세부 지표와 10개 클래스별 세부 IoU(겹침 점수) 표를
    메모장용 텍스트 파일(epoch_details.txt)에 누적 기록합니다.
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    lines.append("=" * 80)
    best_mark = "  ★ [최고 성능 갱신: Best Val Loss]" if is_best else ""
    lines.append(f"[Epoch {epoch:03d} / {total_epochs:03d}]  기록 일시: {now_str}{best_mark}")
    lines.append("-" * 80)
    lines.append(f"  Train Loss     : {train_loss:.4f}")
    lines.append(f"  Val Loss       : {val_loss:.4f} | Pixel Acc: {val_acc:.2f}% | mIoU: {val_miou:.2f}%")
    if test_loss is not None:
        lines.append(f"  Test Loss      : {test_loss:.4f} | Pixel Acc: {test_acc:.2f}% | mIoU: {test_miou:.2f}%")

    if val_class_iou is not None:
        lines.append(format_class_iou_table(val_class_iou, test_class_iou))
    lines.append("=" * 80)
    lines.append("\n")

    mode = "a" if os.path.exists(save_path) else "w"
    with open(save_path, mode, encoding="utf-8-sig") as f:
        f.write("\n".join(lines))


def find_latest_checkpoint():
    """WEIGHTS_DIR 에서 가장 큰 에포크 번호의 체크포인트 경로와 에포크 번호를 반환"""
    best_path, best_epoch = None, 0
    for path in glob.glob(os.path.join(WEIGHTS_DIR, f"{CKPT_PREFIX}*.pth")):
        try:
            ep = int(os.path.basename(path)[len(CKPT_PREFIX):-len(".pth")])
        except ValueError:
            continue
        if ep > best_epoch:
            best_path, best_epoch = path, ep
    return best_path, best_epoch


# =============================================================================
# 5. 학습 루프 (Training Loop)
# =============================================================================
def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[*] 사용할 디바이스: {device}")
    print(f"[*] 모델: U-Net (encoder={ENCODER_NAME}, weights={ENCODER_WEIGHTS})")

    os.makedirs(WEIGHTS_DIR, exist_ok=True)
    os.makedirs(RUNS_DIR, exist_ok=True)
    os.makedirs(VIZ_DIR, exist_ok=True)

    # 1) 도시 폴더 단위 분할 (Train: Costa_Mesa+Brea / Val: Las_Vegas / Test: Venice)
    train_paths, val_paths, test_paths = split_dataset_by_city_folders(IMG_DIR)
    use_test = EVAL_TEST_EACH_EPOCH and len(test_paths) > 0

    train_dataset = SegmentationDataset(train_paths, augment=USE_AUGMENTATION)
    val_dataset = SegmentationDataset(val_paths)
    test_dataset = SegmentationDataset(test_paths)

    loader_kwargs = dict(batch_size=BATCH_SIZE, num_workers=NUM_WORKERS, pin_memory=True)
    train_loader = DataLoader(train_dataset, shuffle=True, **loader_kwargs)
    val_loader = DataLoader(val_dataset, shuffle=False, **loader_kwargs)
    test_loader = DataLoader(test_dataset, shuffle=False, **loader_kwargs) if use_test else None

    # 시각화용 고정 샘플 (Val: Las_Vegas, Test: Venice)
    val_viz_idx = select_fixed_samples(val_dataset, VIZ_SAMPLES_PER_SET)
    test_viz_idx = select_fixed_samples(test_dataset, VIZ_SAMPLES_PER_SET) if len(test_dataset) else []
    print(f"[*] 시각화 추적 샘플 선정 완료: Val {len(val_viz_idx)}장, Test {len(test_viz_idx)}장")

    # 2) 모델, 손실 함수, 옵티마이저 초기화
    model = build_model().to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE)
    scaler = torch.amp.GradScaler("cuda", enabled=(device.type == "cuda"))

    # 3) 이어 학습하기 (Resume) 체크포인트 로드
    start_epoch = 1
    best_val_loss = float('inf')
    history_file = os.path.join(RUNS_DIR, "training_history.json")
    history_keys = ["train_loss", "val_loss", "val_pixel_acc", "val_miou",
                    "test_loss", "test_pixel_acc", "test_miou",
                    "val_class_iou", "test_class_iou"]
    history = {"epochs": [], **{k: [] for k in history_keys}}

    if RESUME:
        if CHECKPOINT_PATH and os.path.exists(CHECKPOINT_PATH):
            ckpt_to_load = CHECKPOINT_PATH
        else:
            ckpt_to_load, _ = find_latest_checkpoint()

        if ckpt_to_load:
            try:
                ckpt = load_model_weights(model, ckpt_to_load, device)
                if isinstance(ckpt, dict) and "model" in ckpt:
                    optimizer.load_state_dict(ckpt["optimizer"])
                    scaler.load_state_dict(ckpt["scaler"])
                    start_epoch = ckpt["epoch"] + 1
                    best_val_loss = ckpt.get("best_val_loss", best_val_loss)
                print(f"[*] [이어 학습하기] '{ckpt_to_load}' 로드 완료! Epoch {start_epoch}부터 학습을 시작합니다.")
            except Exception as e:
                print(f"[경고] 체크포인트 로드 실패 ({e}). Epoch 1부터 새로 시작합니다.")
                model = build_model().to(device)
                optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE)
                start_epoch = 1
        else:
            print("[*] 저장된 체크포인트가 없습니다. Epoch 1부터 새로 시작합니다.")

        # 이전 학습 기록 불러오기 (체크포인트 이후 에포크의 기록은 다시 학습하므로 잘라냄)
        if start_epoch > 1 and os.path.exists(history_file):
            try:
                with open(history_file, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                keep = [i for i, ep in enumerate(loaded.get("epochs", [])) if ep < start_epoch]
                history["epochs"] = [loaded["epochs"][i] for i in keep]
                for k in history_keys:
                    vals = loaded.get(k, [])
                    history[k] = [vals[i] if i < len(vals) else None for i in keep]
            except Exception:
                pass

    # 4) 에포크 진행 (Train + Validation + Test)
    print(f"[*] 모델 학습을 시작합니다! (진행 범위: Epoch {start_epoch} ~ {EPOCHS})")
    all_class_names = ["background"] + CLASSES

    for epoch in range(start_epoch, EPOCHS + 1):
        # [A] 학습 단계 (Training)
        model.train()
        train_loss = 0.0
        train_loop = tqdm(train_loader, desc=f"Train [{epoch}/{EPOCHS}]")
        for images, masks in train_loop:
            images = images.to(device, non_blocking=True)
            masks = masks.to(device, non_blocking=True)

            optimizer.zero_grad()
            with torch.amp.autocast("cuda", enabled=(device.type == "cuda")):
                outputs = model(images)
                loss = criterion(outputs, masks)

            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()

            train_loss += loss.item() * len(images)
            train_loop.set_postfix(loss=loss.item())

        avg_train_loss = train_loss / len(train_dataset)

        # [B] 검증 단계 (Validation: Las_Vegas) - 최고 모델 선정 기준
        avg_val_loss, val_pixel_acc, val_miou, val_class_iou, _ = evaluate(
            model, val_loader, criterion, device, desc=f"Val   [{epoch}/{EPOCHS}]"
        )

        # [B-2] 테스트 단계 (Test: Venice) - 참고용 모니터링 (모델 선정에는 사용하지 않음)
        avg_test_loss = test_pixel_acc = test_miou = test_class_iou = None
        if use_test:
            avg_test_loss, test_pixel_acc, test_miou, test_class_iou, _ = evaluate(
                model, test_loader, criterion, device, desc=f"Test  [{epoch}/{EPOCHS}]"
            )

        print(f"\n====> Epoch {epoch} 완료")
        print(f"      Train Loss: {avg_train_loss:.4f}")
        print(f"      Val   Loss: {avg_val_loss:.4f} | Pixel Acc: {val_pixel_acc:.2f}% | mIoU: {val_miou:.2f}%")
        if use_test:
            print(f"      Test  Loss: {avg_test_loss:.4f} | Pixel Acc: {test_pixel_acc:.2f}% | mIoU: {test_miou:.2f}%")
        if PRINT_CLASS_IOU:
            print(format_class_iou_table(val_class_iou, test_class_iou))

        # [C] 이력 기록 및 학습 곡선 갱신
        history["epochs"].append(epoch)
        history["train_loss"].append(avg_train_loss)
        history["val_loss"].append(avg_val_loss)
        history["val_pixel_acc"].append(val_pixel_acc)
        history["val_miou"].append(val_miou)
        history["test_loss"].append(avg_test_loss)
        history["test_pixel_acc"].append(test_pixel_acc)
        history["test_miou"].append(test_miou)
        history["val_class_iou"].append({n: round(float(val_class_iou[c]), 2) for c, n in enumerate(all_class_names)})
        history["test_class_iou"].append(
            {n: round(float(test_class_iou[c]), 2) for c, n in enumerate(all_class_names)} if test_class_iou is not None else None
        )
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2)
        plot_training_curves(history, os.path.join(RUNS_DIR, "training_curves.png"))

        # 최고 성능(Best Val Loss) 모델 별도 저장
        is_best_loss = (avg_val_loss < best_val_loss)
        if is_best_loss:
            best_val_loss = avg_val_loss
            best_ckpt_path = os.path.join(WEIGHTS_DIR, "res50unet_best.pth")
            torch.save(model.state_dict(), best_ckpt_path)
            print(f"[*] 🌟 [최고 성능 갱신] Best Val Loss: {best_val_loss:.4f} -> '{best_ckpt_path}' 저장 완료!")

        # [C-2] 메모장 파일(.txt) 저장 (100+ 에포크 일괄 확인용 요약 표 + 세부 로그)
        summary_txt_path = os.path.join(RUNS_DIR, "epoch_summary.txt")
        details_txt_path = os.path.join(RUNS_DIR, "epoch_details.txt")
        data_info = {
            "모델 아키텍처": f"U-Net (encoder={ENCODER_NAME}, weights={ENCODER_WEIGHTS})",
            "Train 데이터셋": f"{', '.join(TRAIN_CITIES)} ({len(train_dataset):,}장)",
            "Val 데이터셋  ": f"{', '.join(VAL_CITIES)} ({len(val_dataset):,}장)",
            "Test 데이터셋 ": f"{', '.join(TEST_CITIES)} ({len(test_dataset):,}장)" if use_test else "미사용",
            "배치 크기     ": f"{BATCH_SIZE}",
            "학습률(LR)    ": f"{LEARNING_RATE}",
        }
        update_epoch_summary_txt(history, summary_txt_path, model_name="Res50-UNet", data_info=data_info)
        append_epoch_detail_txt(
            details_txt_path, epoch, EPOCHS,
            avg_train_loss, avg_val_loss, val_pixel_acc, val_miou,
            avg_test_loss, test_pixel_acc, test_miou,
            val_class_iou, test_class_iou,
            is_best=is_best_loss
        )

        # [D] 동적 주기 기반 체크포인트 및 시각자료 저장
        save_interval = SAVE_INTERVAL_LOW if epoch <= SAVE_THRESHOLD_EPOCH else SAVE_INTERVAL_HIGH
        if (epoch % save_interval == 0) or (epoch == EPOCHS):
            save_visualizations(model, val_dataset, val_viz_idx, "Val", epoch, VIZ_DIR, device)
            if test_viz_idx:
                save_visualizations(model, test_dataset, test_viz_idx, "Test", epoch, VIZ_DIR, device)

            # 이어 학습을 위해 옵티마이저/스케일러 상태까지 함께 저장
            ckpt_path = os.path.join(WEIGHTS_DIR, f"{CKPT_PREFIX}{epoch}.pth")
            torch.save({
                "epoch": epoch,
                "model": model.state_dict(),
                "optimizer": optimizer.state_dict(),
                "scaler": scaler.state_dict(),
                "best_val_loss": best_val_loss,
                "encoder": ENCODER_NAME,
            }, ckpt_path)
            print(f"[*] [체크포인트 저장] Epoch {epoch} 가중치 및 시각화 완료 (적용 주기: {save_interval} 에포크)")
        else:
            print(f"[*] [에포크 알림] Epoch {epoch} 완료 (체크포인트 저장은 {save_interval} 에포크 주기로 진행됩니다)")

    # 최종 가중치 저장
    save_path = os.path.join(WEIGHTS_DIR, "res50unet_final.pth")
    torch.save(model.state_dict(), save_path)
    print(f"\n[*] 전체 학습이 완료되었습니다! 최종 가중치가 '{save_path}'에 저장되었습니다.")


if __name__ == "__main__":
    main()
