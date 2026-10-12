import os
import sys

# 서버의 ~/.local 폴더 패키지 간섭을 차단하고 가상환경(alo-train) 패키지를 강제 로드
sys.path = [p for p in sys.path if ".local" not in p]

import glob
import json
import random
import time
import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import torchvision.transforms.functional as TF
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
IMG_DIR = os.path.join(BASE_DIR, "data", "raw_images")
JSON_DIR = os.path.join(BASE_DIR, "data", "processed_json")
WEIGHTS_DIR = os.path.join(BASE_DIR, "weights")
RUNS_DIR = os.path.join(BASE_DIR, "runs")
VIZ_DIR = os.path.join(RUNS_DIR, "visualizations")

IMG_SIZE = (512, 512)
BATCH_SIZE = 16  # VRAM OOM 방지
EPOCHS = 100
LEARNING_RATE = 2e-4
NUM_WORKERS = 4  # 데이터 로딩에 사용할 CPU 프로세스 수 (서버 4, 로컬 1 권장)
RESUME = True  # True로 설정 시 weights 폴더의 가장 최신 체크포인트부터 이어서 학습
CHECKPOINT_PATH = ""  # 특정 가중치를 직접 지정할 경우 경로 입력 (비워두면 자동 탐색)

# =============================================================================
# 데이터 분할 설정 (도시별 층화 분할 / Stratified Split)
# =============================================================================
TRAIN_RATIO = 0.80  # 학습(Train) 비율
VAL_RATIO = 0.10    # 검증(Validation) 비율 - 최고 모델(unet_best.pth) 선정 기준
TEST_RATIO = 0.10   # 테스트(Test) 비율 - 최종 성능 보고용
SPLIT_SEED = 42     # 분할 시드 (같은 값이면 항상 같은 이미지가 같은 세트에 배정됨)
EVAL_TEST_EACH_EPOCH = True  # True: 매 에포크마다 Test Loss/Acc/mIoU도 함께 출력 (에포크당 시간 약 10% 증가)

# =============================================================================
# 에포크 기록 및 체크포인트 저장 주기 설정
# =============================================================================
SAVE_THRESHOLD_EPOCH = 80  # 저장 주기가 전환되는 기준 에포크 (지금은 20)
SAVE_INTERVAL_LOW = 5      # 기준 에포크(20) 이하일 때 저장 주기: 5 에포크마다
SAVE_INTERVAL_HIGH = 1     # 기준 에포크(20) 초과일 때 저장 주기: 1 에포크마다

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


# =============================================================================
# 2. 커스텀 데이터셋 (Dataset)
# =============================================================================
class SegmentationDataset(Dataset):
    def __init__(self, img_dir, json_dir, class_map, img_size=(512, 512), img_paths=None):
        self.img_dir = img_dir
        self.json_dir = json_dir
        self.class_map = class_map
        self.img_size = img_size
        
        # 이미지 파일 리스트 (지정되지 않은 경우 전체 탐색)
        if img_paths is not None:
            self.img_paths = list(img_paths)
        else:
            self.img_paths = glob.glob(os.path.join(img_dir, "*.jpg"))
        
    def __len__(self):
        return len(self.img_paths)
    
    def __getitem__(self, idx):
        img_path = self.img_paths[idx]
        filename = os.path.basename(img_path)
        json_path = os.path.join(self.json_dir, os.path.splitext(filename)[0] + ".json")
        
        # 1) 이미지 읽기 (깨지거나 0바이트인 파일 발견 시 다음 이미지로 자동 우회)
        try:
            image = imread_unicode(img_path)
            if image is None or image.size == 0:
                return self.__getitem__((idx + 1) % len(self.img_paths))
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            orig_h, orig_w = image.shape[:2]
        except Exception:
            return self.__getitem__((idx + 1) % len(self.img_paths))
        
        # 2) 마스크 생성 (배경은 0으로 초기화)
        mask = np.zeros((orig_h, orig_w), dtype=np.uint8)
        
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 폴리곤 데이터를 마스크에 그리기
                for shape in data.get('shapes', []):
                    label = shape['label']
                    class_id = self.class_map.get(label, 0) # 매핑 안되면 배경(0) 처리
                    points = np.array(shape['points'], dtype=np.int32)
                    cv2.fillPoly(mask, [points], class_id)
            except Exception:
                # 중간에 끊기거나 손상된 JSON 파일은 무시하고 배경으로 처리하여 학습 중단 방지
                pass
                
        # 3) 크기 리사이즈
        image = cv2.resize(image, self.img_size, interpolation=cv2.INTER_LINEAR)
        # 마스크는 픽셀값이 클래스 ID이므로 보간법을 NEAREST로 설정해야 소수점이 안생깁니다.
        mask = cv2.resize(mask, self.img_size, interpolation=cv2.INTER_NEAREST)
        
        # 4) PyTorch 텐서로 변환 (HWC -> CHW, 0~1 스케일링)
        image = torch.from_numpy(image).float().permute(2, 0, 1) / 255.0
        mask = torch.from_numpy(mask).long() # 마스크는 long(정수) 타입
        
        return image, mask

# =============================================================================
# 3. U-Net 모델 정의 (PyTorch 기본 구현)
# =============================================================================
class DoubleConv(nn.Module):
    def __init__(self, in_channels, out_channels):
        super(DoubleConv, self).__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(in_channels, out_channels, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_channels, out_channels, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
        )
    def forward(self, x):
        return self.conv(x)

class UNet(nn.Module):
    def __init__(self, in_channels=3, out_channels=10):
        super(UNet, self).__init__()
        
        self.down1 = DoubleConv(in_channels, 64)
        self.pool1 = nn.MaxPool2d(2)
        
        self.down2 = DoubleConv(64, 128)
        self.pool2 = nn.MaxPool2d(2)
        
        self.down3 = DoubleConv(128, 256)
        self.pool3 = nn.MaxPool2d(2)
        
        self.down4 = DoubleConv(256, 512)
        self.pool4 = nn.MaxPool2d(2)
        
        self.bottleneck = DoubleConv(512, 1024)
        
        self.up4 = nn.ConvTranspose2d(1024, 512, kernel_size=2, stride=2)
        self.up_conv4 = DoubleConv(1024, 512)
        
        self.up3 = nn.ConvTranspose2d(512, 256, kernel_size=2, stride=2)
        self.up_conv3 = DoubleConv(512, 256)
        
        self.up2 = nn.ConvTranspose2d(256, 128, kernel_size=2, stride=2)
        self.up_conv2 = DoubleConv(256, 128)
        
        self.up1 = nn.ConvTranspose2d(128, 64, kernel_size=2, stride=2)
        self.up_conv1 = DoubleConv(128, 64)
        
        self.out_conv = nn.Conv2d(64, out_channels, kernel_size=1)
        
    def forward(self, x):
        d1 = self.down1(x)
        x = self.pool1(d1)
        
        d2 = self.down2(x)
        x = self.pool2(d2)
        
        d3 = self.down3(x)
        x = self.pool3(d3)
        
        d4 = self.down4(x)
        x = self.pool4(d4)
        
        x = self.bottleneck(x)
        
        x = self.up4(x)
        x = torch.cat([x, d4], dim=1)
        x = self.up_conv4(x)
        
        x = self.up3(x)
        x = torch.cat([x, d3], dim=1)
        x = self.up_conv3(x)
        
        x = self.up2(x)
        x = torch.cat([x, d2], dim=1)
        x = self.up_conv2(x)
        
        x = self.up1(x)
        x = torch.cat([x, d1], dim=1)
        x = self.up_conv1(x)
        
        x = self.out_conv(x)
        return x

# =============================================================================
# 4. 도시별 데이터 분할 및 시각화 도구 (Utilities & Visualization)
# =============================================================================
def split_dataset_by_city(img_dir, train_ratio=0.80, val_ratio=0.10, test_ratio=0.10, seed=42):
    """
    각 도시(지점)별로 이미지를 묶은 뒤, 도시마다 동일한 비율로 Train / Val / Test 분할
    (Stratified Sampling / 층화 추출)

    셔플된 순서에서 [앞 val_ratio → Val] [다음 test_ratio → Test] [나머지 → Train] 순으로 자릅니다.
    파일 목록을 정렬한 뒤 셔플하므로, Windows(로컬)와 Linux(서버)에서 항상 동일한 분할이 나옵니다.
    """
    total = train_ratio + val_ratio + test_ratio
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"TRAIN/VAL/TEST 비율의 합이 1이 아닙니다: {train_ratio} + {val_ratio} + {test_ratio} = {total}")

    all_img_paths = glob.glob(os.path.join(img_dir, "*.jpg"))
    city_groups = {}
    
    for path in all_img_paths:
        filename = os.path.basename(path)
        parts = filename.split('_-_')
        if len(parts) >= 3:
            city_name = parts[2].split('-')[0].strip()
        else:
            city_name = "Unknown"
            
        if city_name not in city_groups:
            city_groups[city_name] = []
        city_groups[city_name].append(path)
        
    random.seed(seed)
    train_paths, val_paths, test_paths = [], [], []
    
    print("\n" + "=" * 75)
    print(f" [*] 도시별 데이터셋 층화 분할 (Train {train_ratio:.0%} : Val {val_ratio:.0%} : Test {test_ratio:.0%}, seed={seed})")
    print("=" * 75)
    for city, paths in sorted(city_groups.items()):
        paths_copy = sorted(paths)  # glob 순서가 OS마다 달라도 동일한 분할이 나오도록 정렬
        random.shuffle(paths_copy)
        n_val = int(len(paths_copy) * val_ratio)
        n_test = int(len(paths_copy) * test_ratio)
        city_val = paths_copy[:n_val]
        city_test = paths_copy[n_val:n_val + n_test]
        city_train = paths_copy[n_val + n_test:]
        
        train_paths.extend(city_train)
        val_paths.extend(city_val)
        test_paths.extend(city_test)
        print(f"  - [{city}]: 전체 {len(paths):,}장 -> Train: {len(city_train):,} | Val: {len(city_val):,} | Test: {len(city_test):,}")
        
    n_all = len(train_paths) + len(val_paths) + len(test_paths)
    print(f" [*] 총 분할 결과: Train {len(train_paths):,}장 | Val {len(val_paths):,}장 | Test {len(test_paths):,}장 (합계 {n_all:,}장)")
    print("=" * 75 + "\n")
    return train_paths, val_paths, test_paths


def mask_to_rgb(mask_2d):
    """(H, W) 정수 마스크를 (H, W, 3) RGB 컬러 이미지로 변환"""
    h, w = mask_2d.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    for c in range(NUM_CLASSES):
        rgb[mask_2d == c] = CLASS_COLORS[c]
    return rgb


def select_fixed_city_samples(val_dataset, json_dir):
    """각 도시별로 라벨이 포함된 대표 검증 샘플을 1장씩 선정하여 고정 추적"""
    city_samples = {}
    for idx, path in enumerate(val_dataset.img_paths):
        filename = os.path.basename(path)
        parts = filename.split('_-_')
        city = parts[2].split('-')[0].strip() if len(parts) >= 3 else "Unknown"
        
        if city not in city_samples:
            json_file = os.path.join(json_dir, os.path.splitext(filename)[0] + ".json")
            # 라벨이 적어도 1개 이상 존재하는 유의미한 사진 우선 선정
            if os.path.exists(json_file):
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if len(data.get('shapes', [])) > 0:
                        city_samples[city] = idx
                except Exception:
                    pass
            if city not in city_samples:
                city_samples[city] = idx
                
        # 4개 도시가 모두 확보되면 조기 종료
        if len(city_samples) >= 4:
            break
            
    return city_samples


def save_city_visualizations(model, val_dataset, city_samples, epoch, save_dir, device):
    """
    각 도시별 대표 이미지에 대해 [원본 | 정답 라벨 | 모델 예측 | 오버레이] 비교 시각자료 생성
    """
    if not HAS_MATPLOTLIB:
        print("[주의] matplotlib이 설치되어 있지 않아 시각자료 생성을 건너뜁니다. (pip install matplotlib 권장)")
        return

    model.eval()
    os.makedirs(save_dir, exist_ok=True)
    
    with torch.no_grad():
        for city, sample_idx in city_samples.items():
            image_t, mask_t = val_dataset[sample_idx]
            input_t = image_t.unsqueeze(0).to(device)
            
            with torch.cuda.amp.autocast(enabled=(device.type == "cuda")):
                output = model(input_t)
            pred = torch.argmax(output, dim=1).squeeze(0).cpu().numpy()
            
            # 원본 및 마스크 변환
            orig_img = (image_t.permute(1, 2, 0).cpu().numpy() * 255.0).astype(np.uint8)
            gt_mask = mask_t.cpu().numpy()
            
            gt_rgb = mask_to_rgb(gt_mask)
            pred_rgb = mask_to_rgb(pred)
            
            # 50% 알파 블렌딩 오버레이
            overlay = (0.5 * orig_img + 0.5 * pred_rgb).astype(np.uint8)
            
            # 4열 서브플롯 생성
            fig, axes = plt.subplots(1, 4, figsize=(20, 5.5))
            axes[0].imshow(orig_img)
            axes[0].set_title("1. Original Image", fontsize=13, fontweight='bold')
            axes[0].axis('off')
            
            axes[1].imshow(gt_rgb)
            axes[1].set_title("2. Ground Truth Mask", fontsize=13, fontweight='bold')
            axes[1].axis('off')
            
            axes[2].imshow(pred_rgb)
            axes[2].set_title(f"3. UNet Prediction (Epoch {epoch})", fontsize=13, fontweight='bold')
            axes[2].axis('off')
            
            axes[3].imshow(overlay)
            axes[3].set_title("4. Overlay (Image + Pred)", fontsize=13, fontweight='bold')
            axes[3].axis('off')
            
            # 범례(Legend) 표시
            legend_patches = [
                mpatches.Patch(color=CLASS_COLORS[i] / 255.0, label=f"{i}: {('bg' if i==0 else CLASSES[i-1])}")
                for i in range(NUM_CLASSES)
            ]
            fig.legend(handles=legend_patches, loc='lower center', ncol=5, bbox_to_anchor=(0.5, -0.05), fontsize=10)
            
            plt.suptitle(f"[{city}] Segmentation Comparison - Epoch {epoch}", fontsize=15, fontweight='bold')
            plt.tight_layout(rect=[0, 0.08, 1, 0.95])
            
            safe_city_name = city.replace(',', '').replace(' ', '_')
            save_path = os.path.join(save_dir, f"epoch_{epoch:02d}_{safe_city_name}.png")
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

            with torch.cuda.amp.autocast(enabled=(device.type == "cuda")):
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


def _to_plot_values(values):
    """history 안의 None(기록 없는 에포크)을 NaN으로 바꿔 그래프에서 빈 구간으로 표시"""
    return [float('nan') if v is None else v for v in values]


def plot_training_curves(history, save_path):
    """Train/Val/Test Loss, Pixel Acc, mIoU 학습 곡선 그래프 자동 업데이트"""
    if not HAS_MATPLOTLIB:
        return
    if not history or "epochs" not in history or len(history["epochs"]) == 0:
        return
        
    epochs = history["epochs"]
    has_test = any(v is not None for v in history.get("test_loss", []))
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5))
    
    # 1) Loss 곡선
    ax1.plot(epochs, _to_plot_values(history["train_loss"]), marker='o', color='#e74c3c', label='Train Loss', linewidth=2)
    ax1.plot(epochs, _to_plot_values(history["val_loss"]), marker='s', color='#2980b9', label='Val Loss', linewidth=2)
    if has_test:
        ax1.plot(epochs, _to_plot_values(history["test_loss"]), marker='x', color='#f39c12', label='Test Loss', linewidth=2)
    ax1.set_title("Train / Validation / Test Loss", fontsize=13, fontweight='bold')
    ax1.set_xlabel("Epoch", fontsize=11)
    ax1.set_ylabel("Loss", fontsize=11)
    ax1.grid(True, linestyle='--', alpha=0.6)
    ax1.legend(fontsize=11)
    
    # 2) 성능 지표 곡선 (Pixel Acc & mIoU)
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


# =============================================================================
# 5. 학습 루프 (Training Loop)
# =============================================================================
def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[*] 사용할 디바이스: {device}")
    
    os.makedirs(WEIGHTS_DIR, exist_ok=True)
    os.makedirs(RUNS_DIR, exist_ok=True)
    os.makedirs(VIZ_DIR, exist_ok=True)
    
    # 1) 데이터셋 도시별 층화 분할 (Train / Val / Test)
    train_paths, val_paths, test_paths = split_dataset_by_city(
        IMG_DIR, train_ratio=TRAIN_RATIO, val_ratio=VAL_RATIO, test_ratio=TEST_RATIO, seed=SPLIT_SEED
    )
    
    train_dataset = SegmentationDataset(IMG_DIR, JSON_DIR, CLASS_MAP, IMG_SIZE, img_paths=train_paths)
    val_dataset = SegmentationDataset(IMG_DIR, JSON_DIR, CLASS_MAP, IMG_SIZE, img_paths=val_paths)
    test_dataset = SegmentationDataset(IMG_DIR, JSON_DIR, CLASS_MAP, IMG_SIZE, img_paths=test_paths)
    
    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=NUM_WORKERS, pin_memory=True)
    val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False, num_workers=NUM_WORKERS, pin_memory=True)
    test_loader = DataLoader(test_dataset, batch_size=BATCH_SIZE, shuffle=False, num_workers=NUM_WORKERS, pin_memory=True)
    
    # 도시별 고정 시각화 샘플 4장 선정
    city_samples = select_fixed_city_samples(val_dataset, JSON_DIR)
    print(f"[*] 도시별 시각화 추적 샘플 선정 완료: {list(city_samples.keys())}")
    
    # 2) 모델, 손실 함수, 옵티마이저 초기화
    model = UNet(in_channels=3, out_channels=NUM_CLASSES).to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE)
    scaler = torch.cuda.amp.GradScaler(enabled=(device.type == "cuda"))
    
    # 3) 이어 학습하기 (Resume) 체크포인트 로드
    start_epoch = 1
    best_val_loss = float('inf')
    history_file = os.path.join(RUNS_DIR, "training_history.json")
    history_keys = ["train_loss", "val_loss", "val_pixel_acc", "val_miou",
                    "test_loss", "test_pixel_acc", "test_miou"]
    history = {"epochs": [], **{k: [] for k in history_keys}}
    
    # 새로 학습(RESUME=False)할 때는 이전 기록을 불러오지 않고 처음부터 기록
    if RESUME and os.path.exists(history_file):
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            n_prev = len(loaded.get("epochs", []))
            # 이전 버전 기록(Test 항목 없음)과 호환: 없는 항목은 None으로 채워 길이를 맞춤
            for k in history_keys:
                vals = loaded.get(k, [])
                loaded[k] = vals + [None] * (n_prev - len(vals))
            history = loaded
            prev_val_losses = [v for v in history["val_loss"] if v is not None]
            if prev_val_losses:
                best_val_loss = min(prev_val_losses)
        except Exception:
            pass
            
    if RESUME:
        ckpt_to_load = None
        if CHECKPOINT_PATH and os.path.exists(CHECKPOINT_PATH):
            ckpt_to_load = CHECKPOINT_PATH
        else:
            # weights 폴더에서 unet_epoch_*.pth 파일 중 가장 큰 에포크 번호 탐색
            ckpt_files = glob.glob(os.path.join(WEIGHTS_DIR, "unet_epoch_*.pth"))
            if ckpt_files:
                def extract_epoch(path):
                    basename = os.path.basename(path)
                    try:
                        return int(basename.replace("unet_epoch_", "").replace(".pth", ""))
                    except ValueError:
                        return 0
                ckpt_files.sort(key=extract_epoch)
                ckpt_to_load = ckpt_files[-1]

        if ckpt_to_load and os.path.exists(ckpt_to_load):
            try:
                model.load_state_dict(torch.load(ckpt_to_load, map_location=device))
                loaded_epoch = int(os.path.basename(ckpt_to_load).replace("unet_epoch_", "").replace(".pth", ""))
                start_epoch = loaded_epoch + 1
                print(f"[*] [이어 학습하기] 체크포인트 '{ckpt_to_load}' 로드 완료! Epoch {start_epoch}부터 학습을 시작합니다.")
            except Exception as e:
                print(f"[경고] 체크포인트 로드 실패 ({e}). Epoch 1부터 새로 시작합니다.")
        else:
            print("[*] 저장된 체크포인트가 없습니다. Epoch 1부터 새로 시작합니다.")

    # 4) 에포크 진행 (Train + Validation)
    print(f"[*] 모델 학습을 시작합니다! (진행 범위: Epoch {start_epoch} ~ {EPOCHS})")
    
    for epoch in range(start_epoch, EPOCHS + 1):
        # -----------------------------
        # [A] 학습 단계 (Training)
        # -----------------------------
        model.train()
        train_loss = 0.0
        
        train_loop = tqdm(train_loader, desc=f"Train [{epoch}/{EPOCHS}]")
        for images, masks in train_loop:
            images = images.to(device)
            masks = masks.to(device)
            
            optimizer.zero_grad()

            with torch.cuda.amp.autocast(enabled=(device.type == "cuda")):
                outputs = model(images)
                loss = criterion(outputs, masks)
            
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            
            train_loss += loss.item() * len(images)
            train_loop.set_postfix(loss=loss.item())
            
        avg_train_loss = train_loss / len(train_dataset)

        # -----------------------------
        # [B] 검증 단계 (Validation) - 최고 모델 선정 기준
        # -----------------------------
        avg_val_loss, val_pixel_acc, val_miou, _, _ = evaluate(
            model, val_loader, criterion, device, desc=f"Val   [{epoch}/{EPOCHS}]"
        )

        # -----------------------------
        # [B-2] 테스트 단계 (Test) - 참고용 모니터링 (모델 선정에는 사용하지 않음)
        # -----------------------------
        avg_test_loss = test_pixel_acc = test_miou = None
        if EVAL_TEST_EACH_EPOCH:
            avg_test_loss, test_pixel_acc, test_miou, _, _ = evaluate(
                model, test_loader, criterion, device, desc=f"Test  [{epoch}/{EPOCHS}]"
            )
        
        print(f"\n====> Epoch {epoch} 완료")
        print(f"      Train Loss: {avg_train_loss:.4f}")
        print(f"      Val   Loss: {avg_val_loss:.4f} | Pixel Acc: {val_pixel_acc:.2f}% | mIoU: {val_miou:.2f}%")
        if EVAL_TEST_EACH_EPOCH:
            print(f"      Test  Loss: {avg_test_loss:.4f} | Pixel Acc: {test_pixel_acc:.2f}% | mIoU: {test_miou:.2f}%")
        
        # -----------------------------
        # [C] 시각자료 생성 및 이력 기록
        # -----------------------------
        history["epochs"].append(epoch)
        history["train_loss"].append(avg_train_loss)
        history["val_loss"].append(avg_val_loss)
        history["val_pixel_acc"].append(val_pixel_acc)
        history["val_miou"].append(val_miou)
        history["test_loss"].append(avg_test_loss)
        history["test_pixel_acc"].append(test_pixel_acc)
        history["test_miou"].append(test_miou)
        
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2)
            
        plot_training_curves(history, os.path.join(RUNS_DIR, "training_curves.png"))

        # -----------------------------
        # [D] 동적 주기 기반 체크포인트 및 시각자료 저장
        # (기준 20 에포크 이하: 5단위, 20 에포크 초과: 1단위)
        # -----------------------------
        save_interval = SAVE_INTERVAL_LOW if epoch <= SAVE_THRESHOLD_EPOCH else SAVE_INTERVAL_HIGH
        is_save_epoch = (epoch % save_interval == 0) or (epoch == EPOCHS)
        
        if is_save_epoch:
            # 1) 도시별 대표 예측 비교 시각자료 생성
            save_city_visualizations(model, val_dataset, city_samples, epoch, VIZ_DIR, device)
            
            # 2) 정기 체크포인트 가중치 저장
            ckpt_path = os.path.join(WEIGHTS_DIR, f"unet_epoch_{epoch}.pth")
            torch.save(model.state_dict(), ckpt_path)
            print(f"[*] [에포크 기록 & 체크포인트 저장] Epoch {epoch} 가중치 및 도시별 시각화 완료 (적용 주기: {save_interval} 에포크)")
        else:
            print(f"[*] [에포크 알림] Epoch {epoch} 완료 (체크포인트 저장은 {save_interval} 에포크 주기로 진행됩니다)")
        
        # 최고 성능(Best Val Loss) 모델 별도 저장
        if avg_val_loss < best_val_loss:
            best_val_loss = avg_val_loss
            best_ckpt_path = os.path.join(WEIGHTS_DIR, "unet_best.pth")
            torch.save(model.state_dict(), best_ckpt_path)
            print(f"[*] 🌟 [최고 성능 갱신] Best Val Loss: {best_val_loss:.4f} -> '{best_ckpt_path}' 저장 완료!")

    # 최종 가중치 저장
    save_path = os.path.join(WEIGHTS_DIR, "unet_final.pth")
    torch.save(model.state_dict(), save_path)
    print(f"\n[*] 전체 학습이 완료되었습니다! 최종 가중치가 '{save_path}'에 저장되었습니다.")

if __name__ == "__main__":
    main()

