"""보드에서 비전 모듈 0xB0 21바이트를 그대로 찍고 main.py를 다시 띄움 (시험용, Pi ~/psp-dev에서 실행)
  python ~/psp-tools/i2cdump.py [횟수]
"""
import sys
import time

sys.path.insert(0, ".")
import serial
from edge import config
from firmware.dog.upload import read_until, run

n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
s = serial.Serial()
s.port, s.baudrate, s.timeout = config.DOG_PORT, 115200, 0.1
s.open()
try:
    s.write(b"\r\x03\x03")
    time.sleep(0.5)
    s.reset_input_buffer()
    s.write(b"\x01")
    read_until(s, b"raw REPL; CTRL-B to exit\r\n>")
    print(run(s, (
        "import time\n"
        "i=cam.iic\n"
        "for k in range(%d):\n"
        " i.writeto(0x53,bytes([0xB0]))\n"
        " c=i.readfrom(0x53,21)\n"
        " print(list(c))\n"
        " time.sleep_ms(100)\n") % n))
    run(s, "st=None")
    time.sleep(0.3)
    run(s, "import gc\nfor k in [k for k in globals() if k not in ('gc', '__name__')]:\n del globals()[k]\ngc.collect()")
    s.write(b"exec(open('main.py').read())\x04")
    read_until(s, b"OK")
    buf, limit = b"", time.time() + 10
    while b"CMD|V|" not in buf and time.time() < limit:
        s.write(b"CMD|V|$\n")
        time.sleep(0.5)
        buf += s.read(s.in_waiting)
    print("main 다시 띄움:", b"CMD|V|" in buf)
finally:
    s.close()
