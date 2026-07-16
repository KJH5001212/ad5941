"""AD5941 potentiostat full functional test suite (PC BLE).
Tests: timed mode, cycle mode, 50Hz rate, 3-min stability. Prints PASS/FAIL."""
import asyncio, json, time
from bleak import BleakClient, BleakScanner

RX = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
TX = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

class Collector:
    def __init__(self):
        self.frames = []
        self.samples = []       # (t_recv, seq, t_ms, nA, range)
        self.states = []        # (t_recv, st)
        self.buf = b""
        self.maxseq = -1
    def cb(self, _, data):
        self.buf += bytes(data)
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            l = line.decode(errors="replace").strip()
            now = time.time()
            if l.startswith('{"d"'):
                try:
                    for s in json.loads(l)["d"]:
                        self.samples.append((now, s[0], s[1], float(s[2]), int(s[3])))
                        if s[0] > self.maxseq: self.maxseq = s[0]
                except Exception: pass
            elif l.startswith('{"st"'):
                try: self.states.append((now, json.loads(l).get("st")))
                except Exception: pass

async def ack_loop(client, col, stop_evt):
    last = -1
    while not stop_evt.is_set():
        await asyncio.sleep(0.5)
        if col.maxseq > last:
            last = col.maxseq
            try: await client.write_gatt_char(RX, b'{"cmd":"ack","seq":%d}' % last, response=False)
            except Exception: pass

def verdict(name, ok, detail):
    print(("PASS" if ok else "FAIL"), "|", name, "|", detail)
    return ok

async def main():
    dev = await BleakScanner.find_device_by_name("ad5941", timeout=15)
    if not dev:
        print("FAIL | connect | ad5941 not found"); return
    col = Collector()
    results = []
    async with BleakClient(dev) as client:
        await client.start_notify(TX, col.cb)
        stop_evt = asyncio.Event()
        acker = asyncio.create_task(ack_loop(client, col, stop_evt))
        await asyncio.sleep(1.0)

        # ---------- TEST 1: timed mode auto-stop ----------
        n0 = len(col.samples)
        await client.write_gatt_char(RX, b'{"cmd":"start","mode":"timed","rate":10,"auto":1,"dur":5}', response=True)
        await asyncio.sleep(9.0)   # 5s run + margin
        n_timed = len([s for s in col.samples[n0:]])
        st_after = [s for t, s in col.states[-4:]]
        idle_after = all(s == "idle" for s in st_after[-2:]) if len(st_after) >= 2 else False
        n1 = len(col.samples)
        await asyncio.sleep(3.0)
        extra = len(col.samples) - n1   # samples after run should be 0
        results.append(verdict("timed(dur=5,10Hz)",
            35 <= n_timed <= 60 and idle_after and extra == 0,
            f"samples={n_timed} (expect ~50), idle_after={idle_after}, extra_after_stop={extra}"))

        # ---------- TEST 2: cycle mode ----------
        n0 = len(col.samples); s0 = len(col.states)
        await client.write_gatt_char(RX, b'{"cmd":"start","mode":"cycle","rate":10,"auto":1,"on":2,"off":3,"cycles":2}', response=True)
        await asyncio.sleep(11.0)  # 2+3+2 = 7s + margin
        n_cyc = len(col.samples) - n0
        sts = [s for t, s in col.states[s0:]]
        saw_rest = "rest" in sts
        ends_idle = sts[-1] == "idle" if sts else False
        results.append(verdict("cycle(on=2,off=3,x2)",
            25 <= n_cyc <= 55 and saw_rest and ends_idle,
            f"samples={n_cyc} (expect ~40), rest_seen={saw_rest}, ends_idle={ends_idle}"))

        # ---------- TEST 3: 50 Hz rate ----------
        n0 = len(col.samples)
        await client.write_gatt_char(RX, b'{"cmd":"start","mode":"timed","rate":50,"auto":1,"dur":10}', response=True)
        await asyncio.sleep(14.0)
        n_fast = len(col.samples) - n0
        results.append(verdict("rate50(dur=10)",
            420 <= n_fast <= 560,
            f"samples={n_fast} (expect ~500)"))

        # ---------- TEST 4: repeated start/stop x5 ----------
        ok_rep, rep_counts = True, []
        for c in range(5):
            n0 = len(col.samples)
            await client.write_gatt_char(RX, b'{"cmd":"start","mode":"continuous","rate":10,"auto":1}', response=True)
            await asyncio.sleep(3.0)
            await client.write_gatt_char(RX, b'{"cmd":"stop"}', response=True)
            await asyncio.sleep(1.0)
            got = len(col.samples) - n0
            rep_counts.append(got)
            if got < 15: ok_rep = False
        results.append(verdict("start/stop x5", ok_rep, f"samples per cycle={rep_counts}"))

        # ---------- TEST 5: 3-min stability + accuracy ----------
        n0 = len(col.samples)
        await client.write_gatt_char(RX, b'{"cmd":"start","mode":"continuous","rate":10,"auto":1}', response=True)
        t_start = time.time()
        disconnected = False
        while time.time() - t_start < 180:
            await asyncio.sleep(2.0)
            if not client.is_connected:
                disconnected = True; break
        if client.is_connected:
            await client.write_gatt_char(RX, b'{"cmd":"stop"}', response=True)
            await asyncio.sleep(1.0)
        vals = [s[3] for s in col.samples[n0+10:]]
        n_stab = len(vals)
        if vals:
            mean = sum(vals)/len(vals)
            var = sum((v-mean)**2 for v in vals)/len(vals)
            std = var ** 0.5
        else:
            mean = std = -1
        acc_ok = 93.0 <= mean <= 103.0   # 98.04nA +-5%
        results.append(verdict("stability 3min @10Hz",
            (not disconnected) and n_stab >= 1600 and acc_ok,
            f"samples={n_stab} (expect ~1790), mean={mean:.2f}nA (expect 98.0+-5%), std={std:.2f}, disconnected={disconnected}"))

        stop_evt.set()
        try: await acker
        except Exception: pass
        try: await client.stop_notify(TX)
        except Exception: pass

    print("=" * 60)
    npass = sum(1 for r in results if r)
    print(f"RESULT: {npass}/{len(results)} PASS")

asyncio.run(main())
