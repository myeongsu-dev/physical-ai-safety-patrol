"""edge 설정. 저장소 맨 위 .env에서 읽고, 없으면 아래 기본값을 쓴다.

기준값(45℃, 5장 중 3장, 14·18cm)은 기능 정의서 값. 바꿀 때는 .env에 같은 이름으로 적는다.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # python-dotenv가 없어도 환경 변수로 돌게
    pass


def _get(name, default, cast=str):
    v = os.environ.get(name)
    if v is None:
        return default
    v = v.split("#")[0].strip()  # .env 줄 끝 주석
    if v == "":
        return default
    return cast(v)


def _int(v):
    return int(v, 0)  # 0x5A 같은 16진수도


# ---------- 로봇 · 장치 ----------
ROBOT_ID = _get("ROBOT_ID", "A")
DOG_PORT = _get("DOG_PORT", "/dev/ttyACM0")     # MechDog 보드 USB 시리얼
DOG_BAUD = _get("DOG_BAUD", 115200, _int)       # 확인 필요: 보드 main.py와 맞출 것
CAM_PORT = _get("CAM_PORT", "/dev/ttyACM1")     # XIAO: 시리얼 경로면 JPEG 스트림, 숫자·/dev/video*면 UVC
CAM_BAUD = _get("CAM_BAUD", 2000000, _int)      # USB CDC라 실제 속도와는 무관
I2C_BUS = _get("I2C_BUS", 1, _int)              # Pi 5 GPIO 2·3 = I2C 1번
THERMO_ADDR = _get("THERMO_ADDR", 0x5A, _int)   # GY-906(MLX90614) 기본 주소

# ---------- 서버 ----------
MQTT_HOST = _get("MQTT_HOST", "localhost")
MQTT_PORT = _get("MQTT_PORT", 1883, _int)
SERVER_URL = _get("SERVER_URL", "http://localhost:8000").rstrip("/")
SPOOL_DIR = Path(_get("EDGE_SPOOL", str(Path.home() / "potent-spool")))  # 못 보낸 이벤트·사진 (저장소 밖)

# ---------- 보드 명령 ----------
STREAM_MS = _get("STREAM_MS", 200, _int)                 # T 측정값 주기
STREAM_CHANS = _get("STREAM_CHANS", "bat,sonar,color")   # color는 이벤트 채널(바뀔 때만 옴)
HEARTBEAT_S = _get("HEARTBEAT_S", 0.8, float)            # 보드 2초 감시보다 짧게
HEARTBEAT_CMD = _get("HEARTBEAT_CMD", "V")               # 확인 필요: 전용 명령이 생기면 바꿈
PATROL_STRIDE = _get("PATROL_STRIDE", 50, _int)
PATROL_ANGLE = _get("PATROL_ANGLE", 25, _int)
MOVE_MS = _get("MOVE_MS", 10000, _int)                   # 보드 최대 15초 → 끝나기 전에 다시 보냄
PI_GUARD = _get("PI_GUARD", 1, _int)                     # F(선 따라가기) 중 Pi도 초음파로 멈춤(보조)
SONAR_STOP_CM = _get("SONAR_STOP_CM", 14.0, float)
SONAR_RESUME_CM = _get("SONAR_RESUME_CM", 18.0, float)
VOICE_VOLUME = _get("VOICE_VOLUME", 20, _int)
VOICE_OBSTACLE = _get("VOICE_OBSTACLE", 1, _int)         # MP3/0001.mp3 "통로의 물건을 치워 주세요"
VOICE_HELMET = _get("VOICE_HELMET", 2, _int)             # MP3/0002.mp3 "안전모를 착용해 주세요"

# ---------- 판단 ----------
HEAT_SUSPECT_C = _get("HEAT_SUSPECT_C", 45.0, float)     # F-05 과열 의심
HEAT_RECHECK_S = _get("HEAT_RECHECK_S", 2.0, float)      # 멈춰서 2초마다 다시 잼
HEAT_CHECKS = _get("HEAT_CHECKS", 3, _int)               # 다시 재는 횟수
HEAT_HOLD_S = _get("HEAT_HOLD_S", 60.0, float)           # 보고 뒤 서버 명령을 기다리는 최대 시간
HEAT_COOLDOWN_S = _get("HEAT_COOLDOWN_S", 30.0, float)
THERMO_PERIOD_S = _get("THERMO_PERIOD_S", 0.5, float)
VOTE_N = _get("VOTE_N", 5, _int)                         # 최근 5장 중
VOTE_K = _get("VOTE_K", 3, _int)                         # 3장이면 확정
HELMET_CONF = _get("HELMET_CONF", 0.5, float)
FLAME_CONF = _get("FLAME_CONF", 0.4, float)
EVENT_COOLDOWN_S = _get("EVENT_COOLDOWN_S", 30.0, float)  # 같은 종류 이벤트 반복 막기

# ---------- 모델 ----------
MODELS_DIR = Path(_get("MODELS_DIR", str(ROOT / "models")))
DETECT_IMGSZ = _get("DETECT_IMGSZ", 320, _int)
