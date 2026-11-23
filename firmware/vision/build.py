"""patrol_cam을 빌드해 저장소의 firmware/vision/patrol_cam.bin을 새로 만든다 (patrol_cam.ino를 고친 사람만).

  python firmware/vision/build.py

필요: arduino-cli와 ESP32 보드 패키지 (arduino-cli core install esp32:esp32).
arduino-cli의 merged.bin은 플래시 크기(8MB)까지 0xFF로 채워져 있어 뒤의 0xFF를 잘라 저장소에 넣는다.
굽기만 할 사람은 빌드 없이 python firmware/vision/flash.py.
"""
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FQBN = "esp32:esp32:esp32s3:PSRAM=opi,FlashSize=8M,PartitionScheme=default_8MB"
OUT = HERE / "patrol_cam.bin"


def trim(data, block=4096):
    """뒤쪽 0xFF(지운 플래시와 같음)를 block 단위로 잘라냄"""
    end = len(data.rstrip(b"\xff"))
    return data[:-(-end // block) * block]


def main():
    if not shutil.which("arduino-cli"):
        sys.exit("arduino-cli가 없습니다. 굽기만 하려면 python firmware/vision/flash.py")
    build = HERE / "patrol_cam" / "build"
    subprocess.run(["arduino-cli", "compile", "-b", FQBN, "--output-dir", str(build), str(HERE / "patrol_cam")], check=True)
    data = trim((build / "patrol_cam.ino.merged.bin").read_bytes())
    OUT.write_bytes(data)
    print("만듦: %s (%d바이트). 커밋해서 팀원이 빌드 없이 굽게 합니다" % (OUT.relative_to(HERE.parents[1]), len(data)))


if __name__ == "__main__":
    assert trim(b"\x01" + b"\xff" * 5000) == b"\x01" + b"\xff" * 4095 and trim(b"\xff" * 10) == b""
    main()
