import asyncio, json
from bleak import BleakClient, BleakScanner

RX = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
TX = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
START = b'{"cmd":"start","mode":"continuous","rate":10,"auto":1,"dur":60,"on":5,"off":295,"cycles":0}'
STOP  = b'{"cmd":"stop"}'
STATUS = b'{"cmd":"status"}'

NCYCLES = 5
RUN_S   = 3.0
GAP_S   = 1.5

def analyze(frames):
    text = b"".join(frames).decode(errors="replace")
    nsamp = 0; nstat = 0; states = []
    for l in text.split("\n"):
        l = l.strip()
        if l.startswith('{"d"'):
            try: nsamp += len(json.loads(l)["d"])
            except Exception: pass
        elif l.startswith('{"st"'):
            nstat += 1
            try: states.append(json.loads(l).get("st"))
            except Exception: pass
    return nsamp, nstat, states

async def main():
    print("scanning for ad5941...")
    dev = await BleakScanner.find_device_by_name("ad5941", timeout=15)
    if not dev:
        print("RESULT: ad5941 not found"); return
    print("found", dev.address, "connecting...")
    buf = []
    def cb(_, data): buf.append(bytes(data))
    async with BleakClient(dev) as client:
        print("connected. mtu =", client.mtu_size)
        await client.start_notify(TX, cb)
        await asyncio.sleep(0.5)
        results = []
        for c in range(1, NCYCLES + 1):
            if not client.is_connected:
                print(f"CYCLE {c}: *** DISCONNECTED before start (device rebooted / brownout) ***")
                results.append((c, -1, "DISC"))
                break
            buf.clear()
            try:
                await client.write_gatt_char(RX, START, response=True)
            except Exception as e:
                print(f"CYCLE {c}: START write FAILED ({e}) -> device gone")
                results.append((c, -1, "TXFAIL"))
                break
            # ACK a few times during the run to free the device buffer (mimic app)
            t = 0.0
            while t < RUN_S:
                await asyncio.sleep(0.5); t += 0.5
                ns, _, _ = analyze(buf)
                if ns > 0:
                    try: await client.write_gatt_char(RX, ('{"cmd":"ack","seq":%d}' % (ns - 1)).encode(), response=False)
                    except Exception: pass
            try:
                await client.write_gatt_char(RX, STOP, response=True)
            except Exception as e:
                print(f"CYCLE {c}: STOP write FAILED ({e})")
            await asyncio.sleep(0.3)
            nsamp, nstat, states = analyze(buf)
            conn = client.is_connected
            uniq = []
            for s in states:
                if s not in uniq: uniq.append(s)
            print(f"CYCLE {c}: samples={nsamp:4d}  status={nstat}  states={uniq}  connected={conn}")
            results.append((c, nsamp, ",".join(uniq)))
            await asyncio.sleep(GAP_S)
        try: await client.stop_notify(TX)
        except Exception: pass
    print("=" * 50)
    for c, n, s in results:
        tag = "OK" if n > 0 else ("DISC" if n == -1 else "NO-DATA")
        print(f"  cycle {c}: {n:>4} samples  [{tag}]  states={s}")
    good = [r for r in results if r[1] > 0]
    print(f"RESULT: {len(good)}/{len(results)} cycles produced data")

asyncio.run(main())
