import asyncio, json
from bleak import BleakClient, BleakScanner

TX = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # notify
RTIA_NOM = [512000,256000,128000,64000,32000,16000,8000,4000,2000,1000,110]

async def main():
    print("scanning for ad5941...")
    dev = await BleakScanner.find_device_by_name("ad5941", timeout=15)
    if not dev:
        print("RESULT: ad5941 not found"); return
    print("found", dev.address, "connecting...")
    frames = []
    def cb(_, data): frames.append(bytes(data))
    async with BleakClient(dev) as client:
        print("connected. mtu =", client.mtu_size)
        await client.start_notify(TX, cb)
        await asyncio.sleep(1.0)
        RXC = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
        print("sending {\"cmd\":\"cal\"} ... (on-demand cal, ~12s)")
        await client.write_gatt_char(RXC, b'{"cmd":"cal"}', response=True)
        await asyncio.sleep(22)   # 17 cal runs + report
        try: await client.stop_notify(TX)
        except Exception: pass
    text = b"".join(frames).decode(errors="replace")
    cal = None
    last = None
    for l in text.split("\n"):
        l = l.strip()
        if l.startswith('{"cal"'):
            last = l   # 연결 직후 stale 프레임 대신 마지막(온디맨드 결과) 사용
    if last:
        try:
            obj = json.loads(last)
            cal = obj["cal"]
            print("bias_mv =", obj.get("bias_mv", "?"), "  cal applied =", obj.get("applied", "?"))
            exp = obj.get("exp")
            if exp:
                m = sum(exp)/len(exp)
                spread = (max(exp)-min(exp))/m*100 if m else 0
                print("repeatability exp(1k x%d):" % len(exp), exp)
                print("  mean=%.0f  spread=%.1f%%  (gate needs <3%% between first two)" % (m, spread))
        except Exception as e: print("parse err", e, last)
    if cal is None:
        print("NO cal frame received. Raw frames:")
        for l in text.split("\n"):
            if l.strip(): print("  ", l[:120])
        return
    print("=" * 60)
    print(f"{'idx':>3} {'nominal':>9} {'ret':>5} {'raw_ohm':>10}  note")
    for row in cal:
        i, ret, raw = row[0], row[1], row[2]
        nom = RTIA_NOM[i] if i < len(RTIA_NOM) else 0
        note = "OK" if ret == 0 else f"ERR({ret})"
        if ret == 0 and nom:
            dev_pct = (raw/nom - 1)*100
            note += f"  {dev_pct:+.1f}% vs nominal"
        print(f"{i:>3} {nom:>9} {ret:>5} {raw:>10}  {note}")

asyncio.run(main())
