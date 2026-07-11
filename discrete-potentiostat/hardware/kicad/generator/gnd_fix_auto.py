#!/usr/bin/env python3
"""Generic GND finisher for a routed board (env PCB_FILE, BOARD_W/H):
1. run DRC, find GND pads with no plane/pour connection -> stitch via+track
2. stitch isolated GND zone islands with vias (loop to convergence)
3. min island area 0.7mm^2 (slivers removed by fill)
Edge- and corner-arc-aware via placement. Iterates DRC until no GND
unconnected items remain (max 4 rounds).
"""
import os, math, re
import pcbnew

PCB = os.environ['PCB_FILE']
BW = float(os.environ.get('BOARD_W', '26.0'))
BH = float(os.environ.get('BOARD_H', '24.0'))
board = pcbnew.LoadBoard(PCB)
mm = lambda v: v / 1e6
nm = lambda v: int(round(v * 1e6))
F, B = pcbnew.F_Cu, pcbnew.B_Cu
GNDNET = board.GetNetcodeFromNetname('GND')
HERE = os.path.dirname(os.path.abspath(__file__))
RPT = os.path.join(HERE, 'drc_gndfix.txt')

pads = []; tracks = []; vias = []
padobj = {}
for fp in board.GetFootprints():
    for p in fp.Pads():
        pos = p.GetPosition(); sz = p.GetSize()
        deg = p.GetOrientation().AsDegrees() % 180.0
        w, h = (mm(sz.x), mm(sz.y)) if deg < 45 else (mm(sz.y), mm(sz.x))
        th = p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH)
        pads.append((mm(pos.x), mm(pos.y), w/2, h/2, p.GetNetname(), th))
        padobj[(fp.GetReference(), p.GetNumber())] = p
for t in board.GetTracks():
    if t.GetClass() == 'PCB_VIA':
        pos = t.GetPosition()
        vias.append((mm(pos.x), mm(pos.y), mm(t.GetWidth())/2, t.GetNetname()))
    else:
        s, e = t.GetStart(), t.GetEnd()
        tracks.append((mm(s.x), mm(s.y), mm(e.x), mm(e.y), mm(t.GetWidth())/2, t.GetNetname(), t.GetLayer()))

def d_seg(px, py, x0, y0, x1, y1):
    dx, dy = x1-x0, y1-y0
    L2 = dx*dx + dy*dy
    if L2 == 0: return math.hypot(px-x0, py-y0)
    t = max(0.0, min(1.0, ((px-x0)*dx + (py-y0)*dy) / L2))
    return math.hypot(px - (x0+t*dx), py - (y0+t*dy))

def in_board(x, y, margin):
    if not (100+margin < x < 100+BW-margin and 100+margin < y < 100+BH-margin):
        return False
    R = 1.5
    for cx, cy in ((100+R, 100+R), (100+BW-R, 100+R), (100+R, 100+BH-R), (100+BW-R, 100+BH-R)):
        qx = x < cx if cx < 100+BW/2 else x > cx
        qy = y < cy if cy < 100+BH/2 else y > cy
        if qx and qy and math.hypot(x-cx, y-cy) > R - margin:
            return False
    return True

def via_ok(x, y):
    if not in_board(x, y, 0.55): return False
    for (px, py, rx, ry, net, th) in pads:
        d = math.hypot(max(abs(x-px)-rx, 0.0), max(abs(y-py)-ry, 0.0))
        if net != 'GND' and d < 0.3: return False
        if th and d < 0.3: return False
    for (x0, y0, x1, y1, hw, net, lay) in tracks:
        if net == 'GND': continue
        if d_seg(x, y, x0, y0, x1, y1) < 0.3 + hw: return False
    for (vx, vy, vr, net) in vias:
        d = math.hypot(x-vx, y-vy)
        if d < (0.45 if net == 'GND' else 0.3 + vr): return False
    return True

def seg_ok(ax, ay, bx, by, layer, w=0.15):
    n = max(2, int(math.hypot(bx-ax, by-ay) / 0.03))
    for k in range(n+1):
        x = ax + (bx-ax)*k/n; y = ay + (by-ay)*k/n
        for (px, py, rx, ry, net, th) in pads:
            if net == 'GND': continue
            d = math.hypot(max(abs(x-px)-rx, 0.0), max(abs(y-py)-ry, 0.0))
            if d < w/2 + 0.09: return False
        for (x0, y0, x1, y1, hw, net, lay) in tracks:
            if net == 'GND' or lay != layer: continue
            if d_seg(x, y, x0, y0, x1, y1) < w/2 + 0.09 + hw: return False
        for (vx, vy, vr, net) in vias:
            if net == 'GND': continue
            if math.hypot(x-vx, y-vy) < w/2 + 0.09 + vr: return False
    return True

def add_via(x, y):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(nm(x), nm(y)))
    v.SetWidth(nm(0.4)); v.SetDrill(nm(0.2))
    v.SetLayerPair(F, B); v.SetNetCode(GNDNET)
    board.Add(v)
    vias.append((x, y, 0.2, 'GND'))

def add_trk(ax, ay, bx, by, layer):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(nm(ax), nm(ay)))
    t.SetEnd(pcbnew.VECTOR2I(nm(bx), nm(by)))
    t.SetWidth(nm(0.15)); t.SetLayer(layer); t.SetNetCode(GNDNET)
    board.Add(t)
    tracks.append((ax, ay, bx, by, 0.075, 'GND', layer))

def stitch_pad(ref, num):
    p = padobj.get((ref, num))
    if p is None:
        print(f'  ?? pad {ref}.{num} not found'); return False
    layer = B if p.IsFlipped() or (p.IsOnLayer(B) and not p.IsOnLayer(F)) else F
    pos = p.GetPosition(); px, py = mm(pos.x), mm(pos.y)
    r = 0.45
    while r < 2.5:
        for ang in range(0, 360, 15):
            x = px + r*math.cos(math.radians(ang))
            y = py + r*math.sin(math.radians(ang))
            if via_ok(x, y) and seg_ok(px, py, x, y, layer):
                add_via(x, y); add_trk(px, py, x, y, layer)
                print(f'  stitch {ref}.{num} via ({x-100:.2f},{y-100:.2f})')
                return True
        r += 0.1
    print(f'  !! no spot for {ref}.{num}')
    return False

for z in board.Zones():
    if z.GetNetname() == 'GND':
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_AREA)
        z.SetMinIslandArea(int(0.7 * 1e12))

filler = pcbnew.ZONE_FILLER(board)

def island_pass():
    added = 0
    for zone in board.Zones():
        if zone.GetNetname() != 'GND': continue
        for layer in (F, B):
            if not zone.IsOnLayer(layer): continue
            polys = zone.GetFilledPolysList(layer)
            for oi in range(polys.OutlineCount()):
                outline = polys.Outline(oi)
                touched = False
                for (vx, vy, vr, net) in vias:
                    if net == 'GND' and outline.PointInside(pcbnew.VECTOR2I(nm(vx), nm(vy)), 0, False):
                        touched = True; break
                if not touched:
                    for (px, py, rx, ry, net, th) in pads:
                        if net == 'GND' and th and outline.PointInside(pcbnew.VECTOR2I(nm(px), nm(py)), 0, False):
                            touched = True; break
                if touched: continue
                bb = outline.BBox(); cx, cy = mm(bb.Centre().x), mm(bb.Centre().y)
                for (dx, dy) in [(0, 0)] + [(0.15*i*math.cos(math.radians(a)), 0.15*i*math.sin(math.radians(a)))
                                            for i in range(1, 20) for a in range(0, 360, 20)]:
                    x, y = cx+dx, cy+dy
                    if outline.PointInside(pcbnew.VECTOR2I(nm(x), nm(y)), 0, False) and via_ok(x, y):
                        add_via(x, y); added += 1
                        break
                else:
                    print(f'  skip island ({cx-100:.2f},{cy-100:.2f}) area {outline.Area()/1e12:.2f}mm2')
    return added

for rnd in range(4):
    filler.Fill(board.Zones())
    pcbnew.WriteDRCReport(board, RPT, pcbnew.EDA_UNITS_MILLIMETRES, True)
    txt = open(RPT).read()
    orphans = set()
    for m in re.finditer(r'\[unconnected_items\][^\n]*\n((?:    [^\n]*\n){1,3})', txt):
        for pm in re.finditer(r'Pad (\w+) \[GND\] of (\w+)', m.group(1)):
            orphans.add((pm.group(2), pm.group(1)))
    print(f'round {rnd}: {len(orphans)} orphan GND pads')
    n_orph = sum(stitch_pad(r, n) for r, n in sorted(orphans))
    n_isl = island_pass()
    print(f'round {rnd}: stitched {n_orph} pads, {n_isl} island vias')
    if not orphans and n_isl == 0:
        break

filler.Fill(board.Zones())
pcbnew.SaveBoard(PCB, board)
import collections
pcbnew.WriteDRCReport(board, RPT, pcbnew.EDA_UNITS_MILLIMETRES, True)
txt = open(RPT).read()
print(dict(collections.Counter(re.findall(r'\[([a-z_]+)\]', txt))))
