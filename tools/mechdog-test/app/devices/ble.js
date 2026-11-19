// MechDog 본체 BLE (기본 앱과 같은 서비스). 다른 통신(ws.js·mqtt.js)도 이 모양만 맞추면 된다:
// connect(onChunk, onClose) -> { name, write(text), close() }
const uuid = (n) => `0000${n}-0000-1000-8000-00805f9b34fb`;
export const SERVICE = uuid("ffe0"), WRITE = uuid("ffe1"), NOTIFY = uuid("ffe2");
const CHUNK = 20; // BLE 기본 한 번 전송 크기

export const label = "블루투스 (MechDog)";
export const available = () => !!navigator.bluetooth;

export async function connect(onChunk, onClose, namePrefix = "MechDog") {
  const dev = await navigator.bluetooth.requestDevice({ filters: [{ namePrefix }], optionalServices: [SERVICE] });
  dev.addEventListener("gattserverdisconnected", onClose);
  const svc = await (await dev.gatt.connect()).getPrimaryService(SERVICE);
  const [w, n] = await Promise.all([svc.getCharacteristic(WRITE), svc.getCharacteristic(NOTIFY)]);
  const dec = new TextDecoder(), enc = new TextEncoder();
  n.addEventListener("characteristicvaluechanged", (e) => onChunk(dec.decode(e.target.value)));
  await n.startNotifications();
  let queue = Promise.resolve(); // GATT 쓰기는 한 번에 하나만 (겹치면 'operation in progress' 오류)
  const write = (text) => (queue = queue.then(async () => {
    const b = enc.encode(text);
    for (let i = 0; i < b.length; i += CHUNK) await w.writeValueWithoutResponse(b.slice(i, i + CHUNK));
  }));
  return { name: dev.name, write, close: () => dev.gatt.disconnect() };
}
