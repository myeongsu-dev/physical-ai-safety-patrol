"""장치 하나씩 확인 (Pi에서 처음 꽂았을 때, 통합 전에)

  python -m edge.hwcheck ports                 # 시리얼 포트 목록 (보드·XIAO가 어느 ttyACM인지)
  python -m edge.hwcheck dog [--secs 5]        # 보드 V 응답, T 측정값 스트림, DTR·RTS로 다시 켜지지 않는지
  python -m edge.hwcheck cam [--secs 5]        # 초당 장 수 (목표 15), 마지막 장을 cam_check.jpg로
  python -m edge.hwcheck thermo                # GY-906 표면·주변 온도 10번
  python -m edge.hwcheck detect helmet [--image a.jpg] [--n 30]   # 추론 속도 (목표 초당 10장)
  python -m edge.hwcheck mqtt                  # 브로커에 state 한 번 보내고 cmd·mode 10초 듣기
"""
import argparse
import time

from edge import config


def c_ports(a):
    from serial.tools import list_ports

    for p in list_ports.comports():
        print(f"{p.device:16} {p.vid or '':>6} {p.pid or '':>6}  {p.description}")


def c_dog(a):
    from edge.dog_link import BoardState, DogLink

    link = DogLink(a.port or config.DOG_PORT)
    link.open()
    time.sleep(1.0)
    if not link.connected:
        print("보드 포트를 못 엶")
        return
    st = BoardState()
    link.send("V")
    link.send("T", config.STREAM_MS, config.STREAM_CHANS)
    end, n = time.monotonic() + a.secs, 0
    while time.monotonic() < end:
        try:
            parts = link.inbox.get(timeout=0.5)
        except Exception:
            continue
        n += 1
        st.update(parts)
        print("|".join(parts))
    link.send("T", 0)
    print(f"--- {n}줄, 버전 {st.version}, 배터리 {st.battery}%, 초음파 {st.sonar_cm}cm")
    print("확인: 포트를 열 때 보드가 다시 켜지지 않았는지(자세가 풀리지 않음), V에 답했는지")
    link.close()


def c_cam(a):
    from edge.camera import Camera

    cam = Camera(a.port or config.CAM_PORT)
    cam.open()
    time.sleep(1.0)
    n0, t0 = cam.frames, time.monotonic()
    time.sleep(a.secs)
    fps = (cam.frames - n0) / (time.monotonic() - t0)
    f = cam.latest()
    print(f"초당 {fps:.1f}장 (목표 15)")
    if f is not None and f.jpeg:
        open("cam_check.jpg", "wb").write(f.jpeg)
        img = f.image
        print(f"마지막 장 {len(f.jpeg) // 1024}KB {'' if img is None else img.shape} → cam_check.jpg")
    else:
        print("받은 장 없음: CAM_PORT와 XIAO 프로그램(edge/xiao_cam) 확인")
    cam.close()


def c_thermo(a):
    from edge.thermo import Thermo

    th = Thermo()
    th.open()
    for _ in range(10):
        print(f"표면 {th.read()}℃  주변 {th.ambient()}℃")
        time.sleep(0.5)
    print(f"과열 의심 기준 {config.HEAT_SUSPECT_C}℃ 초과. 손바닥·뜨거운 물컵으로 값이 오르는지 보세요")


def c_detect(a):
    from edge.camera import Camera, decode
    from edge.detect import Detector

    det = Detector(a.name, 0.25, imgsz=a.imgsz)
    if not det.ready:
        print("모델 없음: models/{}/버전/ 아래 *_ncnn_model 또는 .pt".format(a.name))
        return
    if a.image:
        img = decode(open(a.image, "rb").read())
        get = lambda: img  # noqa: E731
    else:
        cam = Camera(a.port or config.CAM_PORT)
        cam.open()
        time.sleep(1.5)

        def get():
            f = cam.latest()
            return f.image if f else None
    det(get())  # 첫 장은 준비 시간이라 뺌
    times, dets = [], []
    for _ in range(a.n):
        img = get()
        if img is None:
            time.sleep(0.05)
            continue
        dets = det(img)
        times.append(det.ms)
    if not times:
        print("그림을 못 받음")
        return
    ms = sum(times) / len(times)
    print(f"{det.path}  입력 {a.imgsz}  평균 {ms:.0f}ms → 초당 {1000 / ms:.1f}장 (목표 10)")
    print("마지막 장:", [(d.cls, round(d.conf, 2)) for d in dets])


def c_mqtt(a):
    from common import protocol
    from edge.reporter import Reporter

    r = Reporter()
    r.start()
    for _ in range(20):
        if r.connected:
            break
        time.sleep(0.25)
    print("연결됨" if r.connected else f"연결 안 됨: {config.MQTT_HOST}:{config.MQTT_PORT}")
    r.publish_state({"marker": None, "status": "idle", "battery": None, "sonar_cm": None,
                     "temp_c": None, "version": "hwcheck"})
    print(f"{protocol.topic(config.ROBOT_ID, 'state')} 보냄. 10초 동안 cmd·mode 기다림")
    end = time.monotonic() + 10
    while time.monotonic() < end:
        try:
            print("받음:", r.inbox.get(timeout=0.5))
        except Exception:
            pass
    r.stop()


def main(argv=None):
    ap = argparse.ArgumentParser(description="edge 장치 확인")
    sub = ap.add_subparsers(dest="what", required=True)
    sub.add_parser("ports")
    for name in ("dog", "cam"):
        p = sub.add_parser(name)
        p.add_argument("--port")
        p.add_argument("--secs", type=float, default=5)
    sub.add_parser("thermo")
    p = sub.add_parser("detect")
    p.add_argument("name", choices=("helmet", "flame"))
    p.add_argument("--image")
    p.add_argument("--port")
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--imgsz", type=int, default=config.DETECT_IMGSZ)
    sub.add_parser("mqtt")
    a = ap.parse_args(argv)
    {"ports": c_ports, "dog": c_dog, "cam": c_cam, "thermo": c_thermo, "detect": c_detect, "mqtt": c_mqtt}[a.what](a)


if __name__ == "__main__":
    main()
