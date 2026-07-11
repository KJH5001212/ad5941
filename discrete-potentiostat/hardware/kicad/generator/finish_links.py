#!/usr/bin/env python3
"""Close remaining DRC-unconnected signal links with locally validated paths.

For each unconnected link (pad -> nearest same-net copper), search candidate
paths: direct / L-shaped (both corners) / 45-deg elbow on every layer the
endpoints share, then via-escape variants (seg-via-seg through any layer).
Validation = exact collision model at 0.09 clearance (tighter than the grid
router's 0.10, which is usually why these links failed).

env: PCB_FILE, BOARD_W, BOARD_H
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
IN2, IN3 = pcbnew.In2_Cu, pcbnew.In3_Cu
ROUTE_LAYERS = [F, IN2, IN3, B] if board.GetCopperLayerCount() == 6 else [F, pcbnew.In2_Cu, B]
CLR = 0.095
W = 0.1
HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------- collision model ----------------
pads = []; tracks = []; vias = []
padobj = {}
for fp in board.GetFootprints():
    for p in fp.Pads():
        pos = p.GetPosition(); sz = p.GetSize()
        deg = p.GetOrientation().AsDegrees() % 180.0
        w, h = (mm(sz.x), mm(sz.y)) if deg < 45 else (mm(sz.y), mm(sz.x))
        th = p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH)
        lay = set()
        if p.IsOnLayer(F): lay.add(F)
        if p.IsOnLayer(B): lay.add(B)
        pads.append((mm(pos.x), mm(pos.y), w/2, h/2, p.GetNetname(), lay, th))
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

def seg_clear(netn, ax, ay, bx, by, layer, P=None, T=None, V=None):
    if not (in_board(ax, ay, 0.35+W/2) and in_board(bx, by, 0.35+W/2)):
        return False
    n = max(2, int(math.hypot(bx-ax, by-ay) / 0.03))
    inner = layer not in (F, B)
    for k in range(n+1):
        x = ax + (bx-ax)*k/n; y = ay + (by-ay)*k/n
        for (px, py, rx, ry, net, lay, th) in (P if P is not None else pads):
            if net == netn: continue
            if not th and not inner and layer not in lay: continue
            if not th and inner: continue
            d = math.hypot(max(abs(x-px)-rx, 0.0), max(abs(y-py)-ry, 0.0))
            if d < W/2 + CLR: return False
        for (x0, y0, x1, y1, hw, net, lay) in (T if T is not None else tracks):
            if net == netn or lay != layer: continue
            if d_seg(x, y, x0, y0, x1, y1) < W/2 + CLR + hw: return False
        for (vx, vy, vr, net) in (V if V is not None else vias):
            if net == netn: continue
            if math.hypot(x-vx, y-vy) < W/2 + CLR + vr: return False
    return True

def local_items(x0, y0, x1, y1, m=1.6):
    P = [p for p in pads if x0-m <= p[0] <= x1+m and y0-m <= p[1] <= y1+m]
    T = [t for t in tracks if not (max(t[0], t[2]) < x0-m or min(t[0], t[2]) > x1+m
                                   or max(t[1], t[3]) < y0-m or min(t[1], t[3]) > y1+m)]
    V = [v for v in vias if x0-m <= v[0] <= x1+m and y0-m <= v[1] <= y1+m]
    return P, T, V

def via_clear(netn, x, y):
    if not in_board(x, y, 0.5): return False
    for (px, py, rx, ry, net, lay, th) in pads:
        d = math.hypot(max(abs(x-px)-rx, 0.0), max(abs(y-py)-ry, 0.0))
        if net != netn and d < 0.2 + CLR: return False
        if th and d < 0.3: return False
    for (x0, y0, x1, y1, hw, net, lay) in tracks:
        if net == netn: continue
        if d_seg(x, y, x0, y0, x1, y1) < 0.36 + hw: return False
    for (vx, vy, vr, net) in vias:
        d = math.hypot(x-vx, y-vy)
        if d < 0.5: return False
    return True

def add_seg(netn, ax, ay, bx, by, layer):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(nm(ax), nm(ay)))
    t.SetEnd(pcbnew.VECTOR2I(nm(bx), nm(by)))
    t.SetWidth(nm(W)); t.SetLayer(layer)
    t.SetNetCode(board.GetNetcodeFromNetname(netn))
    board.Add(t)
    tracks.append((ax, ay, bx, by, W/2, netn, layer))

def add_via(netn, x, y):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(nm(x), nm(y)))
    v.SetWidth(nm(0.4)); v.SetDrill(nm(0.2))
    v.SetLayerPair(F, B)
    v.SetNetCode(board.GetNetcodeFromNetname(netn))
    board.Add(v)
    vias.append((x, y, 0.2, netn))

# ---------------- targets from DRC ----------------
RPT = os.path.join(HERE, 'drc_finish.txt')
pcbnew.WriteDRCReport(board, RPT, pcbnew.EDA_UNITS_MILLIMETRES, True)
txt = open(RPT).read()
links = []   # (net, ref, num)
seen = set()
for m in re.finditer(r'\[unconnected_items\][^\n]*\n((?:    [^\n]*\n){1,3})', txt):
    for pm in re.finditer(r'Pad (\w+) \[([^\]]*)\] of (\w+)', m.group(1)):
        num, net, ref = pm.groups()
        if net == 'GND': continue
        if (ref, num) not in seen:
            seen.add((ref, num))
            links.append((net, ref, num))
print('links to close:', links)

def net_points(netn, exclude):
    """same-net attachment points: pad centers + track endpoints, with layers"""
    pts = []
    for (ref, num), p in padobj.items():
        if p.GetNetname() != netn or (ref, num) == exclude: continue
        pos = p.GetPosition()
        lay = [L for L in (F, B) if p.IsOnLayer(L)]
        pts.append((mm(pos.x), mm(pos.y), lay))
    for (x0, y0, x1, y1, hw, net, lay) in tracks:
        if net != netn: continue
        pts.append((x0, y0, [lay])); pts.append((x1, y1, [lay]))
        pts.append(((x0+x1)/2, (y0+y1)/2, [lay]))
    for (vx, vy, vr, net) in vias:
        if net != netn: continue
        pts.append((vx, vy, list(ROUTE_LAYERS)))
    return pts

def try_planar(netn, ax, ay, bx, by, layer):
    P, T, V = local_items(min(ax,bx), min(ay,by), max(ax,bx), max(ay,by))
    cands = [
        [(ax, ay), (bx, by)],
        [(ax, ay), (bx, ay), (bx, by)],
        [(ax, ay), (ax, by), (bx, by)],
    ]
    # 45-deg elbows
    dx, dy = bx-ax, by-ay
    if abs(dx) > abs(dy) and dy != 0:
        cands.append([(ax, ay), (bx - math.copysign(abs(dy), dx), ay), (bx, by)])
        cands.append([(ax, ay), (ax + math.copysign(abs(dy), dx), by), (bx, by)])
    elif abs(dy) > abs(dx) and dx != 0:
        cands.append([(ax, ay), (ax, by - math.copysign(abs(dx), dy)), (bx, by)])
        cands.append([(ax, ay), (bx, ay + math.copysign(abs(dx), dy)), (bx, by)])
    for pts in cands:
        ok = all(seg_clear(netn, *pts[i], *pts[i+1], layer, P, T, V) for i in range(len(pts)-1))
        if ok:
            for i in range(len(pts)-1):
                add_seg(netn, *pts[i], *pts[i+1], layer)
            return True
    # any-angle 2-seg via a searched waypoint (0.1mm grid over local bbox+1.2mm)
    x0, x1 = min(ax, bx)-1.2, max(ax, bx)+1.2
    y0, y1 = min(ay, by)-1.2, max(ay, by)+1.2
    ways = []
    wx = x0
    while wx <= x1:
        wy = y0
        while wy <= y1:
            ways.append((wx, wy, math.hypot(wx-ax, wy-ay)+math.hypot(bx-wx, by-wy)))
            wy += 0.15
        wx += 0.15
    for wx, wy, _ in sorted(ways, key=lambda w: w[2]):
        if seg_clear(netn, ax, ay, wx, wy, layer, P, T, V) and seg_clear(netn, wx, wy, bx, by, layer, P, T, V):
            add_seg(netn, ax, ay, wx, wy, layer)
            add_seg(netn, wx, wy, bx, by, layer)
            return True
    return False

closed = []
for netn, ref, num in links:
    p = padobj[(ref, num)]
    pos = p.GetPosition(); ax, ay = mm(pos.x), mm(pos.y)
    alay = [L for L in (F, B) if p.IsOnLayer(L)]
    targets = sorted(net_points(netn, (ref, num)), key=lambda t: math.hypot(t[0]-ax, t[1]-ay))
    done = False
    # 1) planar attempts on shared layers, nearest targets first
    for (bx, by, blay) in targets[:12]:
        for L in alay:
            if L in blay or set(blay) & set(ROUTE_LAYERS[1:-1]):
                pass
        for L in alay:
            if L in blay and try_planar(netn, ax, ay, bx, by, L):
                closed.append((netn, ref, num, 'planar')); done = True; break
        if done: break
    if done: continue
    # 2) via escape: stub from pad + via, then planar on any layer to target
    for (bx, by, blay) in targets[:12]:
        if done: break
        for r in (0.35, 0.45, 0.55, 0.7, 0.9):
            if done: break
            for ang in range(0, 360, 20):
                vx = ax + r*math.cos(math.radians(ang))
                vy = ay + r*math.sin(math.radians(ang))
                if not via_clear(netn, vx, vy): continue
                if not seg_clear(netn, ax, ay, vx, vy, alay[0]): continue
                hit = False
                for L in ROUTE_LAYERS:
                    inner = L not in (F, B)
                    tgt_ok = (L in blay) or (inner and len(blay) > 1) or (set(blay) == set(ROUTE_LAYERS))
                    if not tgt_ok: continue
                    if try_planar(netn, vx, vy, bx, by, L):
                        add_seg(netn, ax, ay, vx, vy, alay[0])
                        add_via(netn, vx, vy)
                        closed.append((netn, ref, num, f'via@{L}')); hit = True; break
                if hit: done = True; break
    if not done:
        print(f'  !! could not close {netn} {ref}.{num}')
for c in closed:
    print('closed:', c)
pcbnew.SaveBoard(PCB, board)
pcbnew.WriteDRCReport(board, RPT, pcbnew.EDA_UNITS_MILLIMETRES, True)
txt = open(RPT).read()
import collections
print(dict(collections.Counter(re.findall(r'\[([a-z_]+)\]', txt))))
