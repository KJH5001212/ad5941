#!/usr/bin/env python3
"""Fix remaining copper DRC issues:
1. RF pre-route grazing/shorting FL1/C35/C34 GND pads -> reroute with validated paths
2. V5OUT B track through 2 GND stitch vias -> delete vias (island check re-covers)
3. R2.2 stitch via too close to board edge -> re-place edge-aware
4. GND track over U3.44 NC pad -> reroute
5. C34.2 GND stub crossing new ANT_FEED path -> delete (zone connects)
6. isolated_copper slivers -> min island area 0.7 mm^2
"""
import os, math
import pcbnew

PCB = os.environ.get('PCB_FILE', os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'discrete-potentiostat.kicad_pcb')))
board = pcbnew.LoadBoard(PCB)
mm = lambda v: v / 1e6
nm = lambda v: int(round(v * 1e6))
F, B = pcbnew.F_Cu, pcbnew.B_Cu
CLR = 0.09
BXMIN, BYMIN, BXMAX, BYMAX = 100.0, 100.0, 126.0, 124.0

# ---------------- deletion lists ----------------
def near(a, b, tol=0.02): return abs(a-b) < tol

del_tracks = [  # (net, x0,y0,x1,y1)
    ('ANT',      118.8, 107.25, 119.25, 104.15),
    ('ANT50',    119.95, 104.15, 120.7, 105.38),
    ('ANT50',    120.7, 105.38, 121.19, 104.0),
    ('ANT_FEED', 123.2, 105.38, 123.9, 101.6),
    ('GND',      123.315, 104.42, 124.1, 104.55),
    ('GND',      115.65, 111.2, 114.75, 110.45),
    ('GND',      108.5, 101.49, 108.5, 100.24),
]
del_vias = [(115.45, 115.15), (114.0, 111.55), (108.5, 100.24)]

def is_del_track(t):
    s, e = t.GetStart(), t.GetEnd()
    for (netn, x0, y0, x1, y1) in del_tracks:
        if t.GetNetname() == netn and (
           (near(mm(s.x),x0) and near(mm(s.y),y0) and near(mm(e.x),x1) and near(mm(e.y),y1)) or
           (near(mm(s.x),x1) and near(mm(s.y),y1) and near(mm(e.x),x0) and near(mm(e.y),y0))):
            return True
    return False

def is_del_via(t):
    p = t.GetPosition()
    return any(near(mm(p.x), x) and near(mm(p.y), y) for x, y in del_vias)

# ---------------- collision model (built BEFORE deletion; SWIG iterators
# break after board.Remove) -- deleted items excluded from the model ------
pads = []; tracks = []; vias = []
for fp in board.GetFootprints():
    for p in fp.Pads():
        pos = p.GetPosition(); sz = p.GetSize()
        deg = p.GetOrientation().AsDegrees() % 180.0
        w, h = (mm(sz.x), mm(sz.y)) if deg < 45 else (mm(sz.y), mm(sz.x))
        lay = set()
        if p.IsOnLayer(F): lay.add(F)
        if p.IsOnLayer(B): lay.add(B)
        th = p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH)
        pads.append((mm(pos.x), mm(pos.y), w/2, h/2, p.GetNetname(), lay, th))
to_remove = []
for t in board.GetTracks():
    if t.GetClass() == 'PCB_VIA':
        if is_del_via(t):
            to_remove.append(t); continue
        pos = t.GetPosition()
        vias.append((mm(pos.x), mm(pos.y), mm(t.GetWidth())/2, t.GetNetname()))
    else:
        if is_del_track(t):
            to_remove.append(t); continue
        s, e = t.GetStart(), t.GetEnd()
        tracks.append((mm(s.x), mm(s.y), mm(e.x), mm(e.y), mm(t.GetWidth())/2, t.GetNetname(), t.GetLayer()))
for t in to_remove:
    board.Remove(t)
print('removed:', len(to_remove), '(expect 10)')

def d_seg(px, py, x0, y0, x1, y1):
    dx, dy = x1-x0, y1-y0
    L2 = dx*dx + dy*dy
    if L2 == 0: return math.hypot(px-x0, py-y0)
    t = max(0.0, min(1.0, ((px-x0)*dx + (py-y0)*dy) / L2))
    return math.hypot(px - (x0+t*dx), py - (y0+t*dy))

def check_path(netn, pts, w, layer):
    """validate polyline pts (list of (x,y)) width w on layer against all
    foreign copper. Returns list of violation strings (empty = OK)."""
    bad = []
    for i in range(len(pts)-1):
        ax, ay = pts[i]; bx, by = pts[i+1]
        n = max(2, int(math.hypot(bx-ax, by-ay) / 0.025))
        for k in range(n+1):
            x = ax + (bx-ax)*k/n; y = ay + (by-ay)*k/n
            for (px, py, rx, ry, net, lay, th) in pads:
                if net == netn: continue
                if layer not in lay and not th: continue
                ddx = max(abs(x-px) - rx, 0.0); ddy = max(abs(y-py) - ry, 0.0)
                d = math.hypot(ddx, ddy)
                if d < w/2 + CLR:
                    bad.append(f'seg{i} vs pad {net} ({px:.2f},{py:.2f}) d={d:.3f}')
            for (x0, y0, x1, y1, hw, net, lay) in tracks:
                if net == netn or lay != layer: continue
                d = d_seg(x, y, x0, y0, x1, y1)
                if d < w/2 + CLR + hw:
                    bad.append(f'seg{i} vs trk {net} ({x0:.2f},{y0:.2f}) d={d:.3f}')
            for (vx, vy, vr, net) in vias:
                if net == netn: continue
                d = math.hypot(x-vx, y-vy)
                if d < w/2 + CLR + vr:
                    bad.append(f'seg{i} vs via {net} ({vx:.2f},{vy:.2f}) d={d:.3f}')
    return sorted(set(bad))

def add_path(netn, pts, w, layer):
    net = board.GetNetcodeFromNetname(netn)
    for i in range(len(pts)-1):
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pcbnew.VECTOR2I(nm(pts[i][0]), nm(pts[i][1])))
        t.SetEnd(pcbnew.VECTOR2I(nm(pts[i+1][0]), nm(pts[i+1][1])))
        t.SetWidth(nm(w)); t.SetLayer(layer); t.SetNetCode(net)
        board.Add(t)
        tracks.append((pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1], w/2, netn, layer))

# ---------------- new RF + GND paths ----------------
PATHS = [
    # ANT: U3.30 -> FL1.1, approach from south, clear of FL1.2 (GND, north row)
    ('ANT', [(118.8,107.25),(118.8,104.6),(119.05,104.35),(119.25,104.35),(119.25,104.2)], 0.2, F),
    # ANT50: FL1.3 -> south -> east between C35 pads -> R15.1; C35.1 stub tap
    ('ANT50', [(119.95,104.15),(119.95,104.9),(121.3,104.9),(121.3,104.5),(121.465,104.32)], 0.15, F),
    ('ANT50', [(120.635,105.38),(120.635,104.9)], 0.15, F),
    # ANT_FEED: C34.1 -> east around C34.2 -> south into E1.1
    ('ANT_FEED', [(123.315,105.38),(123.9,104.79),(123.9,101.6)], 0.2, F),
    # GND: U3.45 -> EP-region, dodging NC pad 44
    ('GND', [(115.65,111.2),(115.1,111.2),(114.75,110.85),(114.75,110.45)], 0.1, F),
]
ok = True
for netn, pts, w, layer in PATHS:
    bad = check_path(netn, pts, w, layer)
    if bad:
        ok = False
        print(f'!! {netn} path invalid:'); [print('   ', s) for s in bad[:6]]
    else:
        add_path(netn, pts, w, layer)
        print(f'{netn}: path OK ({len(pts)-1} segs, w={w})')

# ---------------- R2.2 re-stitch, edge-aware ----------------
def via_ok(x, y):
    if not (BXMIN+0.55 < x < BXMAX-0.55 and BYMIN+0.55 < y < BYMAX-0.55):
        return False
    for (px, py, rx, ry, net, lay, th) in pads:
        ddx = max(abs(x-px) - rx, 0.0); ddy = max(abs(y-py) - ry, 0.0)
        d = math.hypot(ddx, ddy)
        if net != 'GND' and d < 0.3: return False
        if th and d < 0.3: return False
    for (x0, y0, x1, y1, hw, net, lay) in tracks:
        if net == 'GND': continue
        if d_seg(x, y, x0, y0, x1, y1) < 0.3 + hw: return False
    for (vx, vy, vr, net) in vias:
        d = math.hypot(x-vx, y-vy)
        if d < (0.45 if net == 'GND' else 0.3 + vr): return False
    return True

def seg_ok_gnd(ax, ay, bx, by, layer):
    return not check_path('GND', [(ax,ay),(bx,by)], 0.15, layer)

px, py = 108.5, 101.49   # R2.2 (B)
best = None; r = 0.45
while r < 2.5 and best is None:
    for ang in range(0, 360, 15):
        x = px + r*math.cos(math.radians(ang)); y = py + r*math.sin(math.radians(ang))
        if via_ok(x, y) and seg_ok_gnd(px, py, x, y, B):
            best = (x, y); break
    r += 0.1
if best:
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(nm(best[0]), nm(best[1])))
    v.SetWidth(nm(0.4)); v.SetDrill(nm(0.2))
    v.SetLayerPair(F, B); v.SetNetCode(board.GetNetcodeFromNetname('GND'))
    board.Add(v)
    add_path('GND', [(px,py), best], 0.15, B)
    print(f'R2.2 via at ({best[0]:.2f},{best[1]:.2f})')
else:
    print('!! R2.2: no via spot')

# ---------------- islands threshold + refill ----------------
for z in board.Zones():
    if z.GetNetname() == 'GND':
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_AREA)
        z.SetMinIslandArea(int(0.7 * 1e12))
pcbnew.ZONE_FILLER(board).Fill(board.Zones())
pcbnew.SaveBoard(PCB, board)
print('saved, ok =', ok)
