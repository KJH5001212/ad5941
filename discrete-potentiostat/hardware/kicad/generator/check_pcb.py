#!/usr/bin/env python3
"""Placement checks without touching risky bbox APIs."""
import pcbnew
from pcbnew import ToMM
board = pcbnew.LoadBoard('/home/user/ad5941/discrete-potentiostat/hardware/kicad/discrete-potentiostat.kicad_pcb')
BX, BY, BW, BH = 100.0, 100.0, 24.0, 22.0
items = []
for fp in board.GetFootprints():
    ref = fp.GetReference()
    side = 'B' if fp.IsFlipped() else 'F'
    xs, ys = [], []
    for pad in fp.Pads():
        bb = pad.GetBoundingBox()
        xs += [ToMM(bb.GetLeft()) - BX, ToMM(bb.GetRight()) - BX]
        ys += [ToMM(bb.GetTop()) - BY, ToMM(bb.GetBottom()) - BY]
    if not xs: continue
    items.append((ref, side, min(xs), min(ys), max(xs), max(ys)))
for ref, side, x0, y0, x1, y1 in items:
    m = 0.0
    if x0 < -m or y0 < -m or x1 > BW + m or y1 > BH + m:
        print(f'EDGE  {ref}({side}): ({x0:.2f},{y0:.2f})-({x1:.2f},{y1:.2f})')
bad = 0
CL = 0.12   # min body-to-body clearance
for i in range(len(items)):
    for j in range(i + 1, len(items)):
        r1, s1, ax0, ay0, ax1, ay1 = items[i]
        r2, s2, bx0, by0, bx1, by1 = items[j]
        if s1 != s2: continue
        ox = min(ax1, bx1) - max(ax0, bx0) + CL
        oy = min(ay1, by1) - max(ay0, by0) + CL
        if ox > 0 and oy > 0:
            print(f'OVERLAP {r1}/{r2} ({s1}): {ox:.2f} x {oy:.2f}')
            bad += 1
print('flagged:', bad)
