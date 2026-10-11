import os
import glob
import json
import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import torchvision.transforms.functional as TF
from tqdm import tqdm

# =============================================================================
# 1. 설정 (Settings)
# =============================================================================
# 프로젝트 루트 디렉토리 기준 경로 설정 (어디서 실행해도 동작하도록 보장)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
IMG_DIR = os.path.join(BASE_DIR, "data", "raw_images")
JSON_DIR = os.path.join(BASE_DIR, "data", "processed_json")
IMG_SIZE = (512, 512)
BATCH_SIZE = 4
EPOCHS = 1
LEARNING_RATE = 1e-4

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

def imread_unicode(path):
    """Windows 환경에서 한글 경로가 포함된 이미지를 안전하게 읽기 위한 함수"""
    try:
        img_array = np.fromfile(path, np.uint8)
        return cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    except Exception:
        return None

# =============================================================================
# 2. 커스텀 데이터셋 (Dataset)
# =============================================================================
class SegmentationDataset(Dataset):
    def __init__(self, img_dir, json_dir, class_map, img_size=(512, 512)):
        self.img_dir = img_dir
        self.json_dir = json_dir
        self.class_map = class_map
        self.img_size = img_size
        
        # 이미지 파일 리스트 가져오기 (확장자 jpg 기준)
        self.img_paths = glob.glob(os.path.join(img_dir, "*.jpg"))
        
    def __len__(self):
        return len(self.img_paths)
    
    def __getitem__(self, idx):
        img_path = self.img_paths[idx]
        filename = os.path.basename(img_path)
        json_path = os.path.join(self.json_dir, os.path.splitext(filename)[0] + ".json")
        
        # 1) 이미지 읽기 (한글 경로 지원)
        image = imread_unicode(img_path)
        if image is None:
            raise RuntimeError(f"이미지 읽기 실패: {img_path}")
            
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        orig_h, orig_w = image.shape[:2]
        
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
                # 손상되거나 불완전한 JSON 파일은 무시하고 빈 마스크(배경)로 처리
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
# 4. 학습 루프 (Training Loop)
# =============================================================================
def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[*] 사용할 디바이스: {device}")
    
    # 1) 데이터 로더 준비
    print("[*] 데이터셋을 준비하는 중입니다...")
    dataset = SegmentationDataset(IMG_DIR, JSON_DIR, CLASS_MAP, IMG_SIZE)
    # 데이터를 나누지 않고 전체로 학습합니다 (필요시 train/val 분할 가능)
    dataloader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=1)
    
    print(f"[*] 총 {len(dataset)}장의 사진을 찾았습니다.")
    
    # 2) 모델, 손실 함수, 옵티마이저 초기화
    model = UNet(in_channels=3, out_channels=NUM_CLASSES).to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE)
    
    weights_dir = os.path.join(BASE_DIR, "weights")
    os.makedirs(weights_dir, exist_ok=True)

    # 3) 에포크(Epoch) 진행
    print("[*] 모델 학습을 시작합니다!")
    for epoch in range(1, EPOCHS + 1):
        model.train()
        epoch_loss = 0.0
        
        loop = tqdm(dataloader, desc=f"Epoch [{epoch}/{EPOCHS}]")
        for images, masks in loop:
            images = images.to(device)
            masks = masks.to(device)
            
            # 순전파
            outputs = model(images)
            loss = criterion(outputs, masks)
            
            # 역전파
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            
            epoch_loss += loss.item()
            loop.set_postfix(loss=loss.item())
            
        avg_loss = epoch_loss / len(dataloader)
        print(f"====> Epoch {epoch} 종료 | 평균 Loss: {avg_loss:.4f}")
        
        # 10 에포크마다 중간 체크포인트 가중치 저장
        if epoch % 10 == 0:
            ckpt_path = os.path.join(weights_dir, f"unet_epoch_{epoch}.pth")
            torch.save(model.state_dict(), ckpt_path)
            print(f"[*] [체크포인트] {epoch} 에포크 가중치가 '{ckpt_path}'에 안전하게 저장되었습니다.")
        
    # 4) 최종 가중치 저장
    save_path = os.path.join(weights_dir, "unet_final.pth")
    torch.save(model.state_dict(), save_path)
    print(f"[*] 전체 학습이 완료되었습니다! 최종 가중치가 '{save_path}'에 저장되었습니다.")

if __name__ == "__main__":
    main()

