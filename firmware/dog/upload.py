"""MechDog 보드에 main.py를 재시작 없이 올리고 그 자리에서 다시 실행한다 (PC·Pi 같음).

  python firmware/dog/upload.py [포트]             포트를 안 주면 .env의 DOG_PORT (모르면 python -m edge.hwcheck ports)

보드가 재시작되면 서보 힘이 빠져 로봇이 주저앉는다. mpremote는 포트를 열 때 DTR·RTS를 바꿔서(CH340 보드는 재시작)
올린 뒤 reset도 하므로 쓰지 않는다. 여기서는 재시작되지 않게 열고, Ctrl-C로 main을 멈춘 뒤(로봇은 선 채 정지)
raw REPL로 파일을 쓰고 바로 다시 실행한다. 이전 파일은 보드에 main_prev.py로 남는다.
"""
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).with_name("main.py")
CHUNK = 256  # raw REPL 한 번에 보내는 바이트


def read_until(s, end, timeout=10):
    buf, limit = b"", time.time() + timeout
    while not buf.endswith(end):
        if time.time() > limit:
            raise TimeoutError("보드 응답 없음: %r" % buf[-200:])
        buf += s.read(s.in_waiting or 1)
    return buf


def run(s, code):
    """raw REPL에서 code를 실행하고 출력을 돌려줌. 응답: OK 출력 \\x04 오류 \\x04 >"""
    s.write(code.encode() + b"\x04")
    resp = read_until(s, b"\x04>")
    out, err = resp[resp.index(b"OK") + 2:].split(b"\x04")[:2]
    if err:
        raise RuntimeError(err.decode(errors="replace"))
    return out.decode(errors="replace").strip()


def busy(port):
    """Linux: 다른 프로그램(edge.agent 등)이 포트를 잡고 있으면 True. fuser가 없으면 확인 안 함"""
    try:
        return subprocess.run(["fuser", port], capture_output=True).returncode == 0
    except OSError:
        return False


def main(port):
    import serial

    if sys.platform != "win32" and busy(port):  # 같이 열면 두 프로그램이 보드 답을 나눠 읽음
        sys.exit("%s를 다른 프로그램이 쓰는 중입니다. edge.agent 등을 멈추고 다시 하세요" % port)
    data = SRC.read_bytes()
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 115200, 0.1
    if sys.platform == "win32":  # Windows는 열기 전에 꺼 둬야, Linux는 커널이 켠 그대로 둬야 재시작되지 않음
        s.dtr = s.rts = False
    s.open()
    try:
        s.write(b"\r\x03\x03")  # main 멈춤 (main.py의 finally가 로봇을 세움)
        time.sleep(0.5)
        s.reset_input_buffer()
        s.write(b"\x01")  # raw REPL
        read_until(s, b"raw REPL; CTRL-B to exit\r\n>")
        # main_new.py에 다 쓰고 크기가 맞을 때만 바꿔 끼움. 중간에 끊겨도 main.py와 main_prev.py는 그대로
        run(s, "import os\nf=open('main_new.py','wb')\nw=f.write")
        for i in range(0, len(data), CHUNK):
            run(s, "w(%r)" % data[i:i + CHUNK])
        run(s, "f.close()")
        size = int(run(s, "print(os.stat('main_new.py')[6])"))
        if size != len(data):
            raise RuntimeError("크기가 다름: 보드 %d, 파일 %d (main.py는 그대로)" % (size, len(data)))
        # 파일 시스템에 따라 같은 이름이 있으면 rename이 실패하므로 지난 백업은 먼저 지움
        run(s, "try:\n os.remove('main_prev.py')\nexcept OSError:\n pass\n"
               "try:\n os.rename('main.py','main_prev.py')\nexcept OSError:\n pass\nos.rename('main_new.py','main.py')")
        s.write(b"exec(open('main.py').read())\x04")  # 재시작 없이 다시 실행. 끝을 기다리지 않음
        read_until(s, b"OK")
        # main이 명령에 답할 때까지 확인 (Pi에서 올린 뒤 main이 답하지 않은 적이 한 번 있음)
        buf, limit = b"", time.time() + 10
        while b"CMD|V|" not in buf and time.time() < limit:
            s.write(b"CMD|V|$\n")
            time.sleep(0.5)
            buf += s.read(s.in_waiting)
        if b"CMD|V|" not in buf:
            raise RuntimeError("main.py는 올렸지만 V에 답하지 않음: %r" % buf[-200:])
        print("올림: main.py %d바이트, 다시 실행해 V 응답 확인" % size)
    finally:
        s.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) > 1 or args[:1] in (["-h"], ["--help"]):
        sys.exit(__doc__)
    if args:
        main(args[0])
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from edge import config

        main(config.DOG_PORT)
