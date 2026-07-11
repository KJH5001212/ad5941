#!/usr/bin/env python3
"""Hand-routed closures for the last 4 links on RevC 6L (validated)."""
import os, math, re, collections
import pcbnew

PCB = os.environ.get('PCB_FILE', os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'discrete-potentiostat.kicad_pcb')))
board = pcbnew.LoadBoard(PCB)
mm = lambda v: v / 1e6
nm = lambda v: int(round(v * 1e6))
F, B = pcbnew.F_Cu, pcbnew.B_Cu
IN3 = pcbnew.In3_Cu
CLR = 0.092

# Build collision model from the untouched board (SWIG iterators break
# after Remove), then mutate: rotate C30 180deg + drop its old GND stitch.
pads = []; tracks = []; vias = []
_drop = []
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
    if fp.GetReference() == 'C30':
        fp.SetOrientationDegrees(fp.GetOrientation().AsDegrees() + 180)
for t in list(board.GetTracks()):
    net = t.GetNetname()
    if t.GetClass() == 'PCB_VIA':
        p = t.GetPosition()
        if net == 'GND' and abs(mm(p.x)-116.15) < 0.05 and abs(mm(p.y)-115.9) < 0.05:
            board.Remove(t); continue
        vias.append((mm(p.x), mm(p.y), mm(t.GetWidth())/2, net))
    else:
        sp, ep = t.GetStart(), t.GetEnd()
        if net == 'GND' and abs(mm(sp.x)-116.16) < 0.06 and abs(mm(sp.y)-115.82) < 0.12 and abs(mm(ep.y)-115.9) < 0.12:
            board.Remove(t); continue
        tracks.append((mm(sp.x), mm(sp.y), mm(ep.x), mm(ep.y), mm(t.GetWidth())/2, net, t.GetLayer()))
# patch model for C30 rotation (180deg about center = pads swap places -> swap nets)
for i, (px, py, rx, ry, net, lay, th) in enumerate(pads):
    if abs(px-116.16) < 0.03 and abs(py-116.78) < 0.03 and net == 'DEC1':
        pads[i] = (px, py, rx, ry, 'GND', lay, th)
    elif abs(px-116.16) < 0.03 and abs(py-115.82) < 0.03 and net == 'GND':
        pads[i] = (px, py, rx, ry, 'DEC1', lay, th)

def d_seg(px, py, x0, y0, x1, y1):
    dx, dy = x1-x0, y1-y0
    L2 = dx*dx + dy*dy
    if L2 == 0: return math.hypot(px-x0, py-y0)
    t = max(0.0, min(1.0, ((px-x0)*dx + (py-y0)*dy) / L2))
    return math.hypot(px - (x0+t*dx), py - (y0+t*dy))

def check_seg(netn, ax, ay, bx, by, layer, w):
    bad = []
    n = max(2, int(math.hypot(bx-ax, by-ay) / 0.025))
    inner = layer not in (F, B)
    for k in range(n+1):
        x = ax + (bx-ax)*k/n; y = ay + (by-ay)*k/n
        for (px, py, rx, ry, net, lay, th) in pads:
            if net == netn: continue
            if not th and (inner or layer not in lay): continue
            d = math.hypot(max(abs(x-px)-rx, 0.0), max(abs(y-py)-ry, 0.0))
            if d < w/2 + CLR: bad.append(f'pad {net}({px:.2f},{py:.2f}) d={d:.3f}')
        for (x0, y0, x1, y1, hw, net, lay) in tracks:
            if net == netn or lay != layer: continue
            d = d_seg(x, y, x0, y0, x1, y1)
            if d < w/2 + CLR + hw: bad.append(f'trk {net}({x0:.2f},{y0:.2f}) d={d:.3f}')
        for (vx, vy, vr, net) in vias:
            if net == netn: continue
            d = math.hypot(x-vx, y-vy)
            if d < w/2 + CLR + vr: bad.append(f'via {net}({vx:.2f},{vy:.2f}) d={d:.3f}')
    return sorted(set(bad))

def check_via(netn, x, y):
    bad = []
    for (px, py, rx, ry, net, lay, th) in pads:
        d = math.hypot(max(abs(x-px)-rx, 0.0), max(abs(y-py)-ry, 0.0))
        if net != netn and d < 0.2 + CLR: bad.append(f'pad {net}({px:.2f},{py:.2f}) d={d:.3f}')
        if net != netn and th and d < 0.3: bad.append(f'padTH {net} d={d:.3f}')
    for (x0, y0, x1, y1, hw, net, lay) in tracks:
        if net == netn: continue
        d = d_seg(x, y, x0, y0, x1, y1)
        if d < 0.302 + hw: bad.append(f'trk {net}({x0:.2f},{y0:.2f}) d={d:.3f}')
    for (vx, vy, vr, net) in vias:
        d = math.hypot(x-vx, y-vy)
        if d < 0.5: bad.append(f'via {net}({vx:.2f},{vy:.2f}) d={d:.3f}')
    return sorted(set(bad))

adds = []   # deferred: ('seg'|'via', net, ...)
def seg(netn, pts, layer, w=0.1):
    for i in range(len(pts)-1):
        bad = check_seg(netn, *pts[i], *pts[i+1], layer, w)
        if bad:
            print(f'!! {netn} seg {pts[i]}->{pts[i+1]} L{layer}:'); [print('   ', s) for s in bad[:4]]
            return False
    for i in range(len(pts)-1):
        adds.append(('seg', netn, pts[i], pts[i+1], layer, w))
        tracks.append((pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1], w/2, netn, layer))
    return True

def via(netn, x, y, allow=()):
    bad = [b for b in check_via(netn, x, y) if not any(a in b for a in allow)]
    if bad:
        print(f'!! {netn} via ({x},{y}):'); [print('   ', s) for s in bad[:4]]
        return False
    adds.append(('via', netn, x, y))
    vias.append((x, y, 0.2, netn))
    return True

ok = True
# ---- DCC: via-in-pad U3.47 -> B west/south detour -> via-in-pad L2.1 ----
ok &= via('DCC', 113.85, 111.8, allow=('pad +3V3(113.85,112.20)', 'pad DEC4(113.85,111.40)', 'pad GND(113.85,111.00)'))
ok &= seg('DCC', [(113.85,111.8),(113.85,111.15),(110.55,111.15),(110.55,114.55),(111.3,115.3),(112.05,115.3),(112.05,114.44)], B)
ok &= via('DCC', 112.05, 114.44, allow=('pad GND(112.30,115.08)',))

# ---- CLAMP2: U1.16 -> F -> via -> B diagonal (sum 214.4) -> via N of C8.1 ----
ok &= seg('CLAMP2', [(105.65,106.7),(106.85,106.7)], F)
ok &= via('CLAMP2', 106.85, 106.7)
ok &= seg('CLAMP2', [(106.85,106.7),(107.275,107.125),(111.35,103.05),(111.35,102.3),(110.8,101.75),(110.55,101.75)], B)
ok &= via('CLAMP2', 110.55, 101.75)
ok &= seg('CLAMP2', [(110.55,101.75),(110.1,102.2),(110.1,102.58)], F)

# ---- DEC1: U3.1 escape between L3 pads -> via -> In3 -> via-in-pad C30.1 (rotated) ----
ok &= seg('DEC1', [(114.6,113.3),(114.6,113.5),(114.23,113.87),(114.23,114.8),(115.35,114.8),(115.35,114.6)], F)
ok &= via('DEC1', 115.35, 114.6)
ok &= seg('DEC1', [(115.35,114.6),(116.16,115.41),(116.16,115.82)], IN3)
ok &= via('DEC1', 116.16, 115.82)

# ---- ADC_P: all-F route through the C25/C30 channel to C23.1 ----
ok &= seg('ADC_P', [(116.2,113.35),(116.2,114.85),(116.05,115.0),(115.25,115.0),(115.1,115.15),(115.1,116.1),(114.95,116.25),(113.9,116.25),(113.55,116.6),(113.55,117.85),(113.1,117.85)], F)
print('ALL VALID' if ok else 'SOME PATHS INVALID (nothing written)')
if ok:
    for a in adds:
        if a[0] == 'seg':
            _, netn, p1, p2, layer, w = a
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(pcbnew.VECTOR2I(nm(p1[0]), nm(p1[1])))
            t.SetEnd(pcbnew.VECTOR2I(nm(p2[0]), nm(p2[1])))
            t.SetWidth(nm(w)); t.SetLayer(layer)
            t.SetNetCode(board.GetNetcodeFromNetname(netn))
            board.Add(t)
        else:
            _, netn, x, y = a
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(pcbnew.VECTOR2I(nm(x), nm(y)))
            v.SetWidth(nm(0.4)); v.SetDrill(nm(0.2))
            v.SetLayerPair(F, B)
            v.SetNetCode(board.GetNetcodeFromNetname(netn))
            board.Add(v)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(PCB, board)
    RPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'drc_close4.txt')
    pcbnew.WriteDRCReport(board, RPT, pcbnew.EDA_UNITS_MILLIMETRES, True)
    txt = open(RPT).read()
    print(dict(collections.Counter(re.findall(r'\[([a-z_]+)\]', txt))))
