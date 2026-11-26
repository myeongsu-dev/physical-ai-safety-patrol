"""보드 main을 멈추고 raw REPL에서 파일 코드를 돌린 뒤 main.py를 다시 띄움 (시험용, Pi ~/psp-dev에서)
  python ~/psp-tools/repl.py 코드파일"""
import sys
import time

sys.path.insert(0, ".")
import serial
from edge import config
from firmware.dog.upload import read_until, run

code = open(sys.argv[1]).read()
s = serial.Serial()
s.port, s.baudrate, s.timeout = config.DOG_PORT, 115200, 0.1
s.open()
try:
    s.write(b"\r\x03\x03")  # main 멈춤 (finally가 로봇을 세움)
    time.sleep(0.5)
    s.reset_input_buffer()
    s.write(b"\x01")
    read_until(s, b"raw REPL; CTRL-B to exit\r\n>")
    try:
        print(run(s, code))
    finally:
        run(s, "dog.move(0, 0)")
        s.write(b"\x02")  # 보통 REPL로
        time.sleep(0.3)
        s.write(b"\x04")  # 소프트 리셋 → main.py 새로 (메모리 부족 없이)
        buf, limit = b"", time.time() + 15
        while b"CMD|V|" not in buf and time.time() < limit:
            time.sleep(0.5)
            s.write(b"CMD|V|$\n")
            buf += s.read(s.in_waiting)
        print("main 다시 띄움:", b"CMD|V|" in buf)
finally:
    s.close()
