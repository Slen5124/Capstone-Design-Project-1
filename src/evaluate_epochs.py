import os
import sys
import glob
import re
import argparse
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from tqdm import tqdm
import matplotlib.pyplot as plt

# Windows cp949 인코딩 에러 방지
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Matplotlib 한글 폰트 설정 (Windows: 맑은 고딕, Linux: NanumGothic 또는 폰트 무시)
if sys.platform.startswith("win"):
    plt.rc('font', family='Malgun Gothic')
plt.rc('axes', unicode_minus=False)

from train_unet_server import (
    BASE_DIR,
    IMG_DIR,
    JSON_DIR,
    WEIGHTS_DIR,
    RUNS_DIR,
    CLASSES,
    CLASS_MAP,
    NUM_CLASSES,
    IMG_SIZE,
    UNet,
    SegmentationDataset,
    split_dataset_by_city,
)

def evaluate_single_epoch(model, dataloader, device, num_classes=NUM_CLASSES):
    """
    단일 모델(에포크)에 대해 전체 검증셋을 순회하며 정밀 지표(Accuracy, IoU, Dice) 계산
    """
    model.eval()
    
    total_pixels = 0
    correct_pixels = 0
    
    # 클래스별 교집합(TP), 합집합(TP+FP+FN), 예측합, 정답합
    intersection = np.zeros(num_classes, dtype=np.float64)
    union = np.zeros(num_classes, dtype=np.float64)
    target_sum = np.zeros(num_classes, dtype=np.float64)
    pred_sum = np.zeros(num_classes, dtype=np.float64)
    
    with torch.no_grad():
        for images, masks in tqdm(dataloader, desc="  평가 진행 중", leave=False):
            images = images.to(device)
            masks = masks.to(device)
            
            with torch.cuda.amp.autocast(enabled=(device.type == "cuda")):
                outputs = model(images)
                
            preds = torch.argmax(outputs, dim=1) # [B, H, W]
            
            # 1) 전체 픽셀 정확도 (Pixel Accuracy)
            correct_pixels += (preds == masks).sum().item()
            total_pixels += masks.numel()
            
            # 2) 클래스별 교집합 및 합집합 집계 (mIoU, Dice)
            for c in range(num_classes):
                p_c = (preds == c)
                m_c = (masks == c)
                
                inter = (p_c & m_c).sum().item()
                un = (p_c | m_c).sum().item()
                
                intersection[c] += inter
                union[c] += un
                target_sum[c] += m_c.sum().item()
                pred_sum[c] += p_c.sum().item()
                
    # 전체 픽셀 정확도
    pixel_acc = (correct_pixels / total_pixels) * 100.0 if total_pixels > 0 else 0.0
    
    # 클래스별 IoU 및 Dice
    class_iou = {}
    class_dice = {}
    valid_iou_list = []
    valid_dice_list = []
    
    class_names = ["배경(Background)"] + CLASSES
    
    for c in range(num_classes):
        c_name = class_names[c]
        
        # IoU 계산: TP / (TP + FP + FN)
        if union[c] > 0:
            iou_val = (intersection[c] / union[c]) * 100.0
            class_iou[c_name] = iou_val
            valid_iou_list.append(iou_val)
        else:
            class_iou[c_name] = np.nan
            
        # Dice / F1 계산: 2 * TP / (Pred + Target)
        denom = target_sum[c] + pred_sum[c]
        if denom > 0:
            dice_val = (2.0 * intersection[c] / denom) * 100.0
            class_dice[c_name] = dice_val
            valid_dice_list.append(dice_val)
        else:
            class_dice[c_name] = np.nan

    mean_iou = np.nanmean(valid_iou_list) if valid_iou_list else 0.0
    mean_dice = np.nanmean(valid_dice_list) if valid_dice_list else 0.0
    
    return {
        "pixel_accuracy": pixel_acc,
        "mean_iou": mean_iou,
        "mean_dice": mean_dice,
        "class_iou": class_iou,
        "class_dice": class_dice,
    }


def plot_evaluation_trends(results, save_path):
    """에포크별 정확도 및 겹침 점수(mIoU, Dice) 변화 추이 그래프 생성"""
    epochs = [r["epoch"] for r in results]
    accs = [r["pixel_accuracy"] for r in results]
    mious = [r["mean_iou"] for r in results]
    dices = [r["mean_dice"] for r in results]
    
    plt.figure(figsize=(10, 6))
    plt.plot(epochs, accs, marker='o', linewidth=2, label='Pixel Accuracy (%)', color='#2ca02c')
    plt.plot(epochs, mious, marker='s', linewidth=2, label='mIoU (겹침 점수, %)', color='#1f77b4')
    plt.plot(epochs, dices, marker='^', linewidth=2, label='Mean Dice / F1 (%)', color='#ff7f0e')
    
    plt.title('U-Net 에포크별 성능 지표 변화 추이 (Epoch Trends)', fontsize=14, fontweight='bold', pad=15)
    plt.xlabel('Epoch', fontsize=12)
    plt.ylabel('Score (%)', fontsize=12)
    plt.grid(True, linestyle='--', alpha=0.6)
    plt.xticks(epochs)
    plt.ylim(0, 100)
    plt.legend(loc='lower right', fontsize=11)
    plt.tight_layout()
    plt.savefig(save_path, dpi=300)
    plt.close()
    print(f"[*] 성능 비교 추이 그래프 저장 완료: '{save_path}'")


def main():
    parser = argparse.ArgumentParser(description="U-Net 에포크별 정확도 및 겹침 스코어(mIoU / Dice) 평가")
    parser.add_argument("--split", type=str, default="val", choices=["val", "test", "train"], help="평가 대상 데이터셋 (val, test, train 중 선택, 기본값: val)")
    parser.add_argument("--samples", type=int, default=None, help="평가할 샘플 수 제한 (예: 500 입력 시 빠른 측정, 기본값: 전체 데이터셋)")
    parser.add_argument("--batch_size", type=int, default=16, help="평가 시 배치 크기 (기본값: 16)")
    args = parser.parse_args()
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("\n=================================================================")
    print("  [*] U-Net 에포크별 성능 평가 (Accuracy, mIoU, Dice Score)")
    print(f"  사용 디바이스: {device} ({torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'})")
    print(f"  평가 대상 세트: {args.split.upper()} (val: 10%, test: 10%, train: 80%)")
    print("=================================================================\n")
    
    # 1. weights 폴더에서 가중치 파일 스캔
    ckpt_files = glob.glob(os.path.join(WEIGHTS_DIR, "unet_epoch_*.pth"))
    if not ckpt_files:
        print(f"[!] '{WEIGHTS_DIR}' 폴더에서 'unet_epoch_*.pth' 파일을 찾을 수 없습니다.")
        return
        
    def get_epoch_num(path):
        m = re.search(r"unet_epoch_(\d+)\.pth", os.path.basename(path))
        return int(m.group(1)) if m else -1
        
    ckpt_files = sorted(ckpt_files, key=get_epoch_num)
    print(f"[*] 총 {len(ckpt_files)}개의 에포크 가중치 파일을 찾았습니다:")
    for f in ckpt_files:
        print(f"    - {os.path.basename(f)} (Epoch {get_epoch_num(f)})")
        
    # 2. 데이터셋 준비 (Stratified Split 시드 42로 학습 당시와 100% 동일한 분할 구성)
    print(f"\n[*] {args.split.upper()} 데이터셋을 불러오는 중입니다...")
    train_paths, val_paths, test_paths = split_dataset_by_city(IMG_DIR, seed=42)
    split_map = {"val": val_paths, "test": test_paths, "train": train_paths}
    target_paths = split_map[args.split]
    
    if args.samples and args.samples < len(target_paths):
        print(f"[*] 빠른 평가를 위해 {args.split} 데이터 중 무작위 {args.samples}장만 사용합니다.")
        np.random.seed(42)
        target_paths = list(np.random.choice(target_paths, args.samples, replace=False))
    else:
        print(f"[*] 전체 {args.split.upper()} 데이터셋 ({len(target_paths):,}장)을 대상으로 평가합니다.")
        
    eval_num_workers = 0 if sys.platform.startswith("win") else 2
    eval_dataset = SegmentationDataset(IMG_DIR, JSON_DIR, CLASS_MAP, IMG_SIZE, img_paths=target_paths)
    dataloader = DataLoader(eval_dataset, batch_size=args.batch_size, shuffle=False, num_workers=eval_num_workers, pin_memory=True)
    
    # 3. 모델 초기화
    model = UNet(in_channels=3, out_channels=NUM_CLASSES).to(device)
    
    # 4. 각 에포크별 순회 평가
    all_results = []
    os.makedirs(RUNS_DIR, exist_ok=True)
    
    for ckpt_path in ckpt_files:
        epoch = get_epoch_num(ckpt_path)
        print(f"\n>> [Epoch {epoch:2d}] 가중치 로드 중: {os.path.basename(ckpt_path)}")
        model.load_state_dict(torch.load(ckpt_path, map_location=device))
        
        metrics = evaluate_single_epoch(model, dataloader, device)
        metrics["epoch"] = epoch
        all_results.append(metrics)
        
        print(f"   - 픽셀 정확도 (Pixel Acc): {metrics['pixel_accuracy']:.2f}%")
        print(f"   - 평균 겹침 스코어 (mIoU)  : {metrics['mean_iou']:.2f}%")
        print(f"   - 평균 Dice 스코어 (F1)  : {metrics['mean_dice']:.2f}%")

    # 5. 최종 종합 결과 표 출력
    print("\n" + "=" * 70)
    print("                  [+] 전체 에포크 성능 평가 종합 결과 표")
    print("=" * 70)
    print(f"{'Epoch':^8} | {'Pixel Accuracy':^18} | {'mIoU (겹침 점수)':^18} | {'Mean Dice (F1)':^18}")
    print("-" * 70)
    
    best_miou = -1
    best_epoch = -1
    
    for r in all_results:
        ep = r["epoch"]
        acc = r["pixel_accuracy"]
        miou = r["mean_iou"]
        dice = r["mean_dice"]
        
        if miou > best_miou:
            best_miou = miou
            best_epoch = ep
            
        print(f"{ep:^8} | {acc:^17.2f}% | {miou:^17.2f}% | {dice:^17.2f}%")
        
    print("=" * 70)
    print(f"[*] 가장 높은 겹침 스코어(mIoU)를 달성한 최고 모델: Epoch {best_epoch} ({best_miou:.2f}%)\n")

    # 6. CSV 및 마크다운 리포트 저장
    csv_path = os.path.join(RUNS_DIR, "epoch_evaluation_metrics.csv")
    with open(csv_path, "w", encoding="utf-8-sig") as f:
        f.write("Epoch,Pixel_Accuracy(%),mIoU(%),Mean_Dice(%)\n")
        for r in all_results:
            f.write(f"{r['epoch']},{r['pixel_accuracy']:.2f},{r['mean_iou']:.2f},{r['mean_dice']:.2f}\n")
    print(f"[*] 평가 통계 CSV 저장 완료: '{csv_path}'")
    
    # 7. 그래프 시각화 저장
    plot_path = os.path.join(RUNS_DIR, "epoch_evaluation_trends.png")
    plot_evaluation_trends(all_results, plot_path)

    # 8. 최고 에포크의 클래스별 세부 점수 출력
    best_result = next(r for r in all_results if r["epoch"] == best_epoch)
    print("\n" + "=" * 70)
    print(f"         [*] [Epoch {best_epoch}] 클래스별 상세 겹침 스코어 (IoU & Dice)")
    print("=" * 70)
    print(f"{'Class Name':<28} | {'IoU (%)':^15} | {'Dice (%)':^15}")
    print("-" * 70)
    for c_name in best_result["class_iou"]:
        iou_val = best_result["class_iou"][c_name]
        dice_val = best_result["class_dice"][c_name]
        iou_str = f"{iou_val:.2f}%" if not np.isnan(iou_val) else "N/A"
        dice_str = f"{dice_val:.2f}%" if not np.isnan(dice_val) else "N/A"
        print(f"{c_name:<28} | {iou_str:^15} | {dice_str:^15}")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    main()
