"""YOLO 추론. models/의 안전모 모델(person·head·helmet)과 불꽃 모델(fire)을 불러 씀

모델 두는 곳: models/{helmet|flame}/{버전}/ 안에
  *_ncnn_model/ 폴더(Pi 5 CPU용, 우선) 또는 *.onnx 또는 *.pt
버전 폴더는 이름순 마지막(v1 < v2 < v10)을 씀. 모델이 없으면 추론을 건너뛰고 경고만 남김.

속도 재기: python -m edge.hwcheck detect helmet
"""
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from edge import config

log = logging.getLogger("edge.detect")

HELMET, FLAME = "helmet", "flame"


@dataclass
class Det:
    cls: str
    conf: float
    box: tuple  # (x1, y1, x2, y2) 픽셀


def _natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def find_model(name, models_dir=config.MODELS_DIR):
    """가장 최근 버전의 모델 경로, 없으면 None"""
    base = Path(models_dir) / name
    if not base.is_dir():
        return None
    for ver in sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: _natural(p.name), reverse=True):
        for pattern in ("*_ncnn_model", "*.onnx", "*.pt"):
            hits = sorted(ver.glob(pattern))
            if hits:
                return hits[0]
    return None


class Detector:
    def __init__(self, name, conf, imgsz=config.DETECT_IMGSZ, models_dir=config.MODELS_DIR):
        self.name, self.conf, self.imgsz = name, conf, imgsz
        self.path = find_model(name, models_dir)
        self.model = None
        self.ms = None  # 마지막 추론 시간
        if self.path is None:
            log.warning("%s 모델 없음 (%s/%s/버전/) → 추론 건너뜀", name, models_dir, name)
            return
        try:
            from ultralytics import YOLO

            self.model = YOLO(str(self.path), task="detect")
            log.info("%s 모델 %s", name, self.path)
        except Exception as e:
            log.warning("%s 모델 불러오기 실패: %s", name, e)

    @property
    def ready(self):
        return self.model is not None

    @property
    def version(self):
        return self.path.parent.name if self.path else None

    def __call__(self, image):
        """한 장 → [Det]. 모델이 없으면 []"""
        if self.model is None or image is None:
            return []
        t = time.perf_counter()
        r = self.model.predict(image, imgsz=self.imgsz, conf=self.conf, verbose=False)[0]
        self.ms = (time.perf_counter() - t) * 1000
        names = r.names
        out = []
        for b in r.boxes:
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
            out.append(Det(names[int(b.cls[0])], float(b.conf[0]), (x1, y1, x2, y2)))
        return out


class Voter:
    """최근 n장 중 k장이 참이면 확정 (F-06: 5장 중 3장). 확정되면 비우고 다시 셈"""

    def __init__(self, n=config.VOTE_N, k=config.VOTE_K):
        self.k = k
        self.hist = deque(maxlen=n)

    def push(self, hit):
        self.hist.append(bool(hit))
        if sum(self.hist) >= self.k:
            self.hist.clear()
            return True
        return False

    def reset(self):
        self.hist.clear()


# ---------- 한 장 판정 ----------
def best(dets, cls):
    """그 클래스 중 가장 높은 신뢰도, 없으면 0"""
    return max((d.conf for d in dets if d.cls == cls), default=0.0)


def has_person(dets):
    """F-07: 사람이 보이면 장애물이 아님. 안전모·맨머리만 보여도 사람으로 봄"""
    return any(d.cls in ("person", "head", "helmet") for d in dets)


def no_helmet(dets):
    """F-06: 맨머리(head)가 하나라도 있으면 미착용 장. → (참거짓, 신뢰도)"""
    c = best(dets, "head")
    return c > 0, c


def fire(dets):
    c = best(dets, "fire")
    return c > 0, c
