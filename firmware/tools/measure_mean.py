"""Start a run, collect samples for DUR seconds, stop, report mean/std/min/max."""
import asyncio, json, os, statistics, sys

from bleak import BleakClient, BleakScanner

RX = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
TX = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

DUR = float(os.environ.get("DUR", "10"))
CMD = os.environ.get("CMD", '{"cmd":"start","mode":"continuous","rate":10,"auto":1,"dur":60,"on":5,"off":295,"cycles":0}')

async def main():
    dev = await BleakScanner.find_device_by_name("ad5941", timeout=15)
    if not dev:
        print("RESULT: ad5941 not found"); return 1
    frames = []
    def cb(_, data): frames.append(bytes(data))
    async with BleakClient(dev) as client:
        await client.start_notify(TX, cb)
        await asyncio.sleep(0.5)
        await client.write_gatt_char(RX, CMD.encode(), response=True)
        t = 0.0
        while t < DUR:
            await asyncio.sleep(1.0); t += 1.0
            # periodic ACK to keep device buffer free
            text = b"".join(frames).decode(errors="replace")
            maxseq = -1
            for l in text.split("\n"):
                if l.startswith('{"d"'):
                    try:
                        for s in json.loads(l)["d"]: maxseq = max(maxseq, s[0])
                    except Exception: pass
            if maxseq >= 0:
                try: await client.write_gatt_char(RX, b'{"cmd":"ack","seq":%d}' % maxseq, response=False)
                except Exception: pass
        await client.write_gatt_char(RX, b'{"cmd":"stop"}', response=True)
        await asyncio.sleep(0.5)
        try: await client.stop_notify(TX)
        except Exception: pass

    text = b"".join(frames).decode(errors="replace")
    want = os.environ.get("RANGE")           # filter to this range tag if set
    vals, ranges, states = [], set(), []
    for l in text.split("\n"):
        l = l.strip()
        if l.startswith('{"d"'):
            try:
                for s in json.loads(l)["d"]:
                    ranges.add(int(s[3]))
                    if want is None or int(s[3]) == int(want):
                        vals.append(float(s[2]))
            except Exception: pass
        elif l.startswith('{"st"'):
            try: states.append(json.loads(l).get("st"))
            except Exception: pass
    if not vals:
        print("RESULT: NO DATA (states seen: %s)" % sorted(set(states)))
        return 2
    # drop first 10 samples (settle)
    core = vals[10:] if len(vals) > 20 else vals
    print("samples=%d  (used %d after settle-drop)" % (len(vals), len(core)))
    print("mean=%.3f nA  std=%.3f  min=%.3f  max=%.3f" %
          (statistics.mean(core), statistics.pstdev(core), min(core), max(core)))
    print("ranges seen:", sorted(ranges))
    return 0

sys.exit(asyncio.run(main()))
