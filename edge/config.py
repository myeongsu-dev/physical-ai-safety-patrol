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
DOG_PORT = _get("DOG_PORT", "/dev/ttyUSB0")     # MechDog 보드 USB 시리얼 (CH340). 포트는 기기마다 달라 .env에서 바꿈
DOG_BAUD = _get("DOG_BAUD", 115200, _int)       # 확인 필요: 보드 main.py와 맞출 것
CAM_PORT = _get("CAM_PORT", "/dev/ttyACM1")     # XIAO: 시리얼 경로면 JPEG 스트림, 숫자·/dev/video*면 UVC
CAM_BAUD = _get("CAM_BAUD", 2000000, _int)      # USB CDC라 실제 속도와는 무관
I2C_BUS = _get("I2C_BUS", 1, _int)              # Pi 5 GPIO 2·3 = I2C 1번
THERMO_ADDR = _get("THERMO_ADDR", 0x5A, _int)   # GY-906(MLX90614) 기본 주소
DOCK_MARKER = _get("DOCK_MARKER", 16, _int)     # 충전 스테이션 마커. 서버 dock에 zone이 없으면 여기까지 감 (새 맵 12-15-16)

# ---------- 서버 ----------
MQTT_HOST = _get("MQTT_HOST", "localhost")
MQTT_PORT = _get("MQTT_PORT", 1883, _int)
SERVER_URL = _get("SERVER_URL", "http://localhost:8000").rstrip("/")
SPOOL_DIR = Path(_get("EDGE_SPOOL", str(Path.home() / "potent-spool")))  # 못 보낸 이벤트·사진 (저장소 밖)

# ---------- 보드 명령 ----------
STREAM_MS = _get("STREAM_MS", 200, _int)                 # T 측정값 주기
STREAM_CHANS = _get("STREAM_CHANS", "bat,sonar,line,color")  # line: 선 중심 x(0~159, 못 찾으면 -1). color는 이벤트 채널(바뀔 때만 옴)
HEARTBEAT_S = _get("HEARTBEAT_S", 0.8, float)            # 보드 2초 감시보다 짧게
HEARTBEAT_CMD = _get("HEARTBEAT_CMD", "V")               # 확인 필요: 전용 명령이 생기면 바꿈
BOARD_QUIET_S = _get("BOARD_QUIET_S", 2.0, float)        # 보드 감시 시간 (firmware/dog/main.py QUIET_MS). 넘게 못 보냈으면 보드가 멈췄다고 봄
PATROL_STRIDE = _get("PATROL_STRIDE", 50, _int)          # F 보폭(속도 %가 아님)
PATROL_ANGLE = _get("PATROL_ANGLE", 40, _int)            # F 최대 조향. 25는 반지름 약 45 cm, 40은 약 22 cm까지 걸으며 돎 (2026-11-27 실측)
MOVE_MS = _get("MOVE_MS", 10000, _int)                   # 보드 최대 15초 → 끝나기 전에 다시 보냄 (정지·안전 정지 뒤에는 안 보냄)
ACK_TIMEOUT_S = _get("ACK_TIMEOUT_S", 1.5, float)        # F를 보낸 뒤 보드 ok·!를 기다리는 시간. 없으면 멈춤
SENSOR_STALE_S = _get("SENSOR_STALE_S", 1.0, float)     # 걷는 중 측정값(D)이 이만큼 안 오면 Pi가 멈춤 (보드 끊김·재시작)
LINE_GUARD = _get("LINE_GUARD", 1, _int)                 # 선을 못 찾으면(line=-1) Pi가 멈춤 (보드 F는 선을 놓치면 직진함)
LINE_LOST_S = _get("LINE_LOST_S", 10.0, float)           # 선이 이만큼 계속 안 보이면 멈춤 (보드가 먼저 0.6초 걸으며 꺾고 최대 8초 제자리 돌며 찾은 뒤 E lost를 보냄, 이건 그 뒤 예비)
ALIVE_TIMEOUT_S = _get("ALIVE_TIMEOUT_S", 5.0, float)    # 서버 신호(potent/server/alive)가 이만큼 안 오면 단독 모드: 다음 마커에서 멈춤 (0이면 끔)
SERVER_STOP_MAX_S = _get("SERVER_STOP_MAX_S", 20.0, float)  # 단독 모드에서 다음 마커가 이만큼 안 나오면 그 자리에서 멈춤
BATTERY_STOP_PCT = _get("BATTERY_STOP_PCT", 15, _int)   # 배터리가 이 % 이하로 BATTERY_LOW_S 동안 계속되면 그 자리에서 멈춤 (map_config BATTERY_EMERGENCY)
BATTERY_LOW_S = _get("BATTERY_LOW_S", 3.0, float)        # 걸을 때 전압이 잠깐 떨어지는 것은 무시
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

# ---------- 디버그 웹 화면 (edge/webview.py) ----------
WEB_PORT = _get("WEB_PORT", 8080, _int)                  # 0이면 안 띄움. 브라우저에서 http://<Pi IP>:8080
WEB_FPS = _get("WEB_FPS", 10, _int)                      # 웹으로 보내는 영상 최대 장/초 (Pi CPU 아끼기)

# ---------- 모델 ----------
MODELS_DIR = Path(_get("MODELS_DIR", str(ROOT / "models")))
DETECT_IMGSZ = _get("DETECT_IMGSZ", 320, _int)
