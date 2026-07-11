#!/usr/bin/env python3
"""Connect GND orphans: pads without plane access get track+via; isolated
zone islands get a stitching via (In1/In4 are solid GND planes)."""
import os, math, sys
import pcbnew

PCB = os.environ.get('PCB_FILE', os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'discrete-potentiostat.kicad_pcb')))
board = pcbnew.LoadBoard(PCB)
mm = lambda v: v / 1e6
nm = lambda v: int(round(v * 1e6))
F, B = pcbnew.F_Cu, pcbnew.B_Cu
VIA_R, CLR, TRK_W = 0.2, 0.10, 0.15

# ---- collision model ------------------------------------------------------
pads = []      # (x, y, rx, ry, net, layers)
tracks = []    # (x0,y0,x1,y1, halfw, net, layer)
vias = []      # (x, y, r, net)
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

def via_ok(x, y):
    """through-via at (x,y): clearance vs all foreign copper on all layers,
    hole clearance vs everything."""
    for (px, py, rx, ry, net, lay, th) in pads:
        ddx = max(abs(x-px) - rx, 0.0); ddy = max(abs(y-py) - ry, 0.0)
        d = math.hypot(ddx, ddy)
        if net != 'GND':
            if d < VIA_R + CLR: return False
        else:
            if th and d < 0.05: return False
        if th and d < 0.3: return False   # hole-to-hole safety
    for (x0, y0, x1, y1, hw, net, lay) in tracks:
        if net == 'GND': continue
        if d_seg(x, y, x0, y0, x1, y1) < VIA_R + CLR + hw: return False
    for (vx, vy, vr, net) in vias:
        d = math.hypot(x-vx, y-vy)
        if net == 'GND':
            if d < 0.45: return False    # hole-hole
        else:
            if d < VIA_R + CLR + vr: return False
    return True

def seg_ok(ax, ay, bx, by, layer, ignore_pads=()):
    n = max(2, int(math.hypot(bx-ax, by-ay) / 0.05))
    for k in range(n+1):
        x = ax + (bx-ax)*k/n; y = ay + (by-ay)*k/n
        for i, (px, py, rx, ry, net, lay, th) in enumerate(pads):
            if net == 'GND' or i in ignore_pads: continue
            if layer not in lay and not th: continue
            ddx = max(abs(x-px) - rx, 0.0); ddy = max(abs(y-py) - ry, 0.0)
            if math.hypot(ddx, ddy) < TRK_W/2 + CLR: return False
        for (x0, y0, x1, y1, hw, net, lay) in tracks:
            if net == 'GND' or lay != layer: continue
            if d_seg(x, y, x0, y0, x1, y1) < TRK_W/2 + CLR + hw: return False
        for (vx, vy, vr, net) in vias:
            if net == 'GND': continue
            if math.hypot(x-vx, y-vy) < TRK_W/2 + CLR + vr: return False
    return True

GNDNET = board.GetNetcodeFromNetname('GND')

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
    t.SetWidth(nm(TRK_W)); t.SetLayer(layer); t.SetNetCode(GNDNET)
    board.Add(t)
    tracks.append((ax, ay, bx, by, TRK_W/2, 'GND', layer))

def pad_index(ref, num):
    i = 0
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if fp.GetReference() == ref and p.GetNumber() == num:
                return i, p
            i += 1
    return None, None

def stitch_pad(ref, num, layer):
    """place a via near pad, connected by short track on `layer`."""
    idx, p = pad_index(ref, num)
    pos = p.GetPosition(); px, py = mm(pos.x), mm(pos.y)
    best = None
    r = 0.45
    while r < 2.0 and best is None:
        for ang in range(0, 360, 15):
            x = px + r*math.cos(math.radians(ang))
            y = py + r*math.sin(math.radians(ang))
            if via_ok(x, y) and seg_ok(px, py, x, y, layer, ignore_pads=(idx,)):
                best = (x, y); break
        r += 0.1
    if best is None:
        print(f'  !! no via spot for {ref}.{num}')
        return False
    add_via(*best)
    add_trk(px, py, best[0], best[1], layer)
    print(f'  via for {ref}.{num} at ({best[0]:.2f},{best[1]:.2f}) r={math.hypot(best[0]-px, best[1]-py):.2f}')
    return True

# ---- 1. orphan pads -------------------------------------------------------
for ref, num, layer in [('U1','9',F), ('C32','2',F), ('R2','2',B), ('C24','2',F)]:
    stitch_pad(ref, num, layer)

# ---- 2. refill, then stitch isolated islands ------------------------------
filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())

def island_stitch():
    added = 0
    for zone in board.Zones():
        if zone.GetNetname() != 'GND': continue
        for layer in (F, B):
            if not zone.IsOnLayer(layer): continue
            polys = zone.GetFilledPolysList(layer)
            for oi in range(polys.OutlineCount()):
                outline = polys.Outline(oi)
                # does any GND via/PTH pad fall inside this island?
                touched = False
                for (vx, vy, vr, net) in vias:
                    if net == 'GND' and outline.PointInside(pcbnew.VECTOR2I(nm(vx), nm(vy)), 0, False):
                        touched = True; break
                if not touched:
                    for (px2, py2, rx, ry, net, lay, th) in pads:
                        if net == 'GND' and th and outline.PointInside(pcbnew.VECTOR2I(nm(px2), nm(py2)), 0, False):
                            touched = True; break
                if touched: continue
                # find interior point: try centroid of bbox then sample
                bb = outline.BBox()
                cx, cy = mm(bb.Centre().x), mm(bb.Centre().y)
                spot = None
                for (dx, dy) in [(0,0)] + [(0.2*i*math.cos(math.radians(a)), 0.2*i*math.sin(math.radians(a)))
                                            for i in range(1, 15) for a in range(0, 360, 30)]:
                    x, y = cx+dx, cy+dy
                    if outline.PointInside(pcbnew.VECTOR2I(nm(x), nm(y)), 0, False) and via_ok(x, y):
                        spot = (x, y); break
                if spot:
                    add_via(*spot)
                    added += 1
                    print(f'  island via at ({spot[0]:.2f},{spot[1]:.2f}) on {"F" if layer==F else "B"}')
                else:
                    print(f'  !! island at ({cx:.2f},{cy:.2f}) {"F" if layer==F else "B"}: no via spot (area {mm(mm(outline.Area())):.2f}mm2)')
    return added

n = island_stitch()
if n:
    filler.Fill(board.Zones())
pcbnew.SaveBoard(PCB, board)
print('saved;', n, 'island vias')
