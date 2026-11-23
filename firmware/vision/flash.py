"""비전 모듈(ESP32-S3)에 Hiwonder 펌웨어(.bin)를 굽는다 (PC·Pi 같음).

  python firmware/vision/flash.py <펌웨어.bin> [포트]     포트를 안 주면 .env의 VISION_PORT

비전 모듈 USB-C를 PC나 Pi에 직접 꽂고 굽는다 (MechDog 보드를 거쳐서는 못 구움, 둘 사이는 I2C 숫자 통로).
설정은 Hiwonder 안내와 같다: ESP32-S3, 주소 0x0, 460800, 80MHz, DIO.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main(args):
    if not 1 <= len(args) <= 2 or args[0] in ("-h", "--help"):
        sys.exit(__doc__)
    sys.path.insert(0, str(ROOT))
    from edge import config  # .env를 읽음

    bin_path = Path(args[0])
    port = args[1] if len(args) == 2 else config._get("VISION_PORT", None)
    if not bin_path.is_file():
        sys.exit("펌웨어 파일이 없습니다: %s" % bin_path)
    if not port:
        sys.exit("포트를 주거나 .env에 VISION_PORT를 적으세요 (찾기: python -m edge.hwcheck ports)")
    # 같은 CH340 칩이라 포트를 헷갈리기 쉽다. MechDog 보드에 굽기를 시도하면 보드가 재시작돼 로봇이 주저앉음
    if os.path.realpath(port) == os.path.realpath(config.DOG_PORT):
        sys.exit("%s는 .env의 DOG_PORT(MechDog 보드)입니다. 비전 모듈 포트를 확인하세요" % port)
    cmd = [sys.executable, "-m", "esptool", "--chip", "esp32s3", "--port", port, "--baud", "460800",
           "write_flash", "--flash_mode", "dio", "--flash_freq", "80m", "0x0", str(bin_path)]
    print("굽기:", bin_path.name, "→", port)
    try:
        return subprocess.run(cmd).returncode
    except FileNotFoundError:
        sys.exit("esptool이 없습니다: pip install esptool")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
