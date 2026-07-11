#!/usr/bin/env python3
"""Generate discrete-potentiostat.kicad_pcb (KiCad 7, pcbnew API).

24 x 22 mm 4-layer board. Placement + zones + antenna keepout + RF feed
pre-route; general routing is left for interactive work (ratsnest ready).
Origin: board top-left at (100,100) mm absolute.
"""
import os, re, sys
import pcbnew
from pcbnew import VECTOR2I, FromMM, ToMM

GEN = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get('PCB_OUT', os.path.abspath(os.path.join(GEN, '..')))
FPLIB = '/usr/share/kicad/footprints'
BX, BY = 100.0, 100.0      # board origin (top-left)
BW, BH = 24.0, 22.0        # board size

def MM(x, y):
    return VECTOR2I(FromMM(BX + x), FromMM(BY + y))

# ---------------------------------------------------------------- netlist ---
def tokenize(s):
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c in '()':
            out.append(c); i += 1
        elif c == '"':
            j = i + 1
            while s[j] != '"' or s[j-1] == '\\': j += 1
            out.append(('STR', s[i+1:j])); i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not s[j].isspace() and s[j] not in '()': j += 1
            out.append(('SYM', s[i:j])); i = j
    return out

def parse(tokens):
    it = iter(tokens)
    def walk():
        lst = []
        for t in it:
            if t == '(':
                lst.append(walk())
            elif t == ')':
                return lst
            else:
                lst.append(t[1])
        return lst
    assert next(it) == '('
    return walk()

tree = parse(tokenize(open(os.path.join(GEN, 'out.net')).read()))
comps = {}   # ref -> (fp_libid, value)
for sec in tree:
    if isinstance(sec, list) and sec and sec[0] == 'components':
        for c in sec[1:]:
            d = {}
            for item in c[1:]:
                if isinstance(item, list) and len(item) >= 2 and isinstance(item[1], str):
                    d[item[0]] = item[1]
            comps[d['ref']] = (d.get('footprint', ''), d.get('value', ''))
netof = {}   # (ref,pin) -> netname
netnames = set()
for sec in tree:
    if isinstance(sec, list) and sec and sec[0] == 'nets':
        for net in sec[1:]:
            name = None
            for item in net[1:]:
                if item[0] == 'name': name = item[1]
            name = name.lstrip('/'); netnames.add(name)
            for item in net[1:]:
                if item[0] == 'node':
                    dd = {k[0]: k[1] for k in item[1:] if isinstance(k, list)}
                    netof[(dd['ref'], dd['pin'])] = name.lstrip('/')

# ------------------------------------------------------------------ board ---
board = pcbnew.BOARD()
ds = board.GetDesignSettings()
ds.SetCopperLayerCount(6)
ds.m_TrackMinWidth = FromMM(0.09)
ds.m_ViasMinSize = FromMM(0.4)
ds.m_MinThroughDrill = FromMM(0.2)
ds.m_MinClearance = FromMM(0.09)
ds.m_HoleClearance = FromMM(0.2)
ds.m_HoleToHoleMin = FromMM(0.2)
ds.m_CopperEdgeClearance = FromMM(0.3)
try:
    nc = ds.m_NetSettings.m_DefaultNetClass
    nc.SetClearance(FromMM(0.09)); nc.SetTrackWidth(FromMM(0.1))
    nc.SetViaDiameter(FromMM(0.4)); nc.SetViaDrill(FromMM(0.2))
except Exception as _e:
    print('netclass', _e)
nets = {}
for name in sorted(netnames):
    ni = pcbnew.NETINFO_ITEM(board, name)
    board.Add(ni)
    nets[name] = ni

# ------------------------------------------------------- custom footprints ---
def base_fp(name):
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID('pstat', name))
    return fp

def smd_pad(fp, num, x, y, w, h):
    p = pcbnew.PAD(fp)
    p.SetNumber(str(num))
    p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    p.SetShape(pcbnew.PAD_SHAPE_RECT)
    p.SetSize(VECTOR2I(FromMM(w), FromMM(h)))
    p.SetPos0(VECTOR2I(FromMM(x), FromMM(y)))
    p.SetPosition(VECTOR2I(FromMM(x), FromMM(y)))
    p.SetLayerSet(pcbnew.PAD.SMDMask())
    fp.Add(p)
    return p

def make_custom(name):
    fp = base_fp(name)
    if name == 'Johanson_0402_4pin':          # FL1 2450FM07A0029 (VERIFY vs datasheet)
        smd_pad(fp, 1, -0.35, 0.15, 0.25, 0.2)
        smd_pad(fp, 2, -0.35, -0.15, 0.25, 0.2)   # note: pad map per datasheet before fab
        smd_pad(fp, 3, 0.35, 0.15, 0.25, 0.2)
        smd_pad(fp, 4, 0.35, -0.15, 0.25, 0.2)
    elif name == 'Johanson_2450AT07A0100':    # E1 (VERIFY vs datasheet)
        smd_pad(fp, 1, -0.4, 0, 0.3, 0.5)
        smd_pad(fp, 2, 0.4, 0, 0.3, 0.5)      # pad2 = mechanical/NC end
    elif name == 'TDK_WR202010':              # coil lead solder pads
        smd_pad(fp, 1, 0, -2.0, 1.8, 2.4)
        smd_pad(fp, 2, 0, 2.0, 1.8, 2.4)
    elif name == 'LIR2032_Tabs':               # tab-welded coin cell, 2 solder pads
        smd_pad(fp, 1, -9.8, 0, 2.8, 5.0)      # +tab
        smd_pad(fp, 2, 9.8, 0, 2.8, 5.0)       # -tab
    elif name.startswith('SolderPads_1x'):     # wire-solder pads, D1.0mm, 1.8mm pitch
        n = int(name.split('_1x')[1].split('_')[0])
        for k in range(n):
            p = smd_pad(fp, k+1, (k - (n-1)/2)*1.8, 0, 1.0, 1.0)
            p.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
    else:
        raise KeyError(name)
    return fp

# --------------------------------------------------------------- placement ---
# (ref, x, y, rot_deg, flip)
PLACE = [
    # --- Qi charger, top-left ---
    ('U1',  3.7,  6.8,   0, False),
    ('C1',  2.4,  1.6,   0, False), ('C2', 5.6, 1.2, 0, False),
    ('C7',  7.45, 1.7,  90, False), ('C8', 8.75, 1.7, 90, False),
    ('C3',  6.9,  3.9,  90, False), ('C4', 8.0, 3.9, 90, False), ('C5', 9.1, 3.9, 90, False),
    ('C6',  7.05, 5.9,  90, False), ('C9', 8.5, 6.2, 90, False),
    ('R1',  7.1,  7.9,  90, False), ('R2', 7.1, 9.8, 90, False), ('TH1', 8.2, 9.8, 90, False),
    ('L1',  1.2, 13.0,   0, False),
    # --- 5V rail parts, top strip ---
    ('C10', 9.55, 1.3,  90, False), ('C11',10.7, 1.3, 90, False),
    ('R3', 11.65, 1.25, 90, False), ('R4', 12.55, 1.25, 90, False),
    ('C27',10.25, 4.0,  90, False),
    # --- crystal ---
    ('Y1', 13.8,  3.65,  0, False),
    ('C32',11.3,  3.6,  90, False), ('C33',16.3, 3.6, 90, False),
    # --- LDO ---
    ('U2', 10.9,  8.0, 180, False),
    ('C12', 8.9, 10.4,   0, False), ('C13',11.1, 10.4, 0, False), ('C14',12.3, 11.9, 90, False),
    # --- nRF52832 ---
    ('U3', 16.9,  9.4,  90, False),
    ('C25',14.0, 14.9,  90, False), ('C26',21.3,  7.5, 90, False),
    ('C28',21.3,  9.0, 90, False), ('C29',21.3, 10.5, 90, False),
    ('C30',15.3, 14.9, 90, False), ('C31',16.5, 14.9, 90, False),
    ('L2', 19.6, 15.4,  90, False), ('L3', 18.0, 15.3, 90, False),
    # --- RF corner ---
    ('FL1',17.9,  3.6,   0, False),
    ('C35',19.0,  4.3,  90, False),
    ('R15',20.4,  3.8,   0, False),
    ('C34',22.3,  4.2,  90, False),
    ('E1', 22.4,  1.5,   0, False),
    # --- SWD, charger IC, LED (bottom-right) ---
    ('J2', 22.7, 17.6,  90, False),
    ('U6', 16.6, 20.3,   0, False),
    ('D1', 19.7, 20.9,   0, False), ('R14',21.6, 20.9, 0, False),
    # --- AFE, bottom-left ---
    ('U5',  4.4, 14.2,   0, False),
    ('C16', 6.95, 13.6, 90, False), ('C17', 7.9, 13.6, 90, False),
    ('C18', 9.0, 13.7,  90, False), ('C19',10.05, 13.6, 90, False),
    ('R7', 11.0, 13.6,  90, False), ('R8', 11.95, 13.6, 90, False), ('C20',12.9, 13.6, 90, False),
    ('U4',  4.8, 18.3,   0, False),
    ('R10', 1.3, 17.3,  90, False), ('C21', 1.2, 19.1, 90, False),
    ('R9',  8.7, 16.9,   0, False),
    ('R11', 9.3, 18.1,   0, False), ('C22', 9.3, 19.4, 0, False),
    ('R12',11.2, 16.6,  90, False), ('C23',12.1, 16.6, 90, False),
    ('R13',11.2, 18.6,  90, False), ('C24',12.1, 18.6, 90, False),
    ('TP1',13.6, 17.2,   0, False), ('TP2',13.6, 19.0, 0, False), ('TP3',13.6, 20.8, 0, False),
    ('J1',  6.0, 21.2,   0, False),
    ('R5', 10.4, 20.9,  90, False), ('R6', 11.3, 20.9, 90, False), ('C15',12.2, 20.9, 90, False),
    # --- back side: tab-welded cell ---
    ('BT1',12.0, 11.0,   0, True),
]

FP_OVERRIDE = {
    'BT1': 'pstat:LIR2032_Tabs',   # Keystone 3034 is 27mm wide -> does not fit 24mm board
}

def load_fp(libid):
    lib, name = libid.split(':')
    if lib == 'pstat':
        return make_custom(name)
    path = os.path.join(FPLIB, lib + '.pretty')
    fp = pcbnew.FootprintLoad(path, name)
    if fp is None:
        raise FileNotFoundError(libid)
    return fp

missing, placed = [], {}
for ref, x, y, rot, flip in PLACE:
    fpid, value = comps.get(ref, ('', ''))
    fpid = FP_OVERRIDE.get(ref, fpid)
    if not fpid:
        missing.append(ref); continue
    fp = load_fp(fpid)
    fp.SetReference(ref)
    fp.SetValue(value)
    board.Add(fp)
    fp.SetPosition(MM(x, y))
    if flip:
        fp.Flip(MM(x, y), False)
    fp.SetOrientationDegrees(rot)
    for pad in fp.Pads():
        key = (ref, pad.GetNumber())
        if key in netof:
            pad.SetNetCode(nets[netof[key]].GetNetCode())
    placed[ref] = fp
for ref in comps:
    if ref not in placed:
        missing.append(ref)
if missing:
    print('WARNING not placed:', sorted(set(missing)))

# ----------------------------------------------------------------- outline ---
def edge_line(x1, y1, x2, y2):
    s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(MM(x1, y1)); s.SetEnd(MM(x2, y2))
    s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(FromMM(0.1))
    board.Add(s)

def edge_arc(cx, cy, sx, sy, angle_deg):
    a = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_ARC)
    a.SetCenter(MM(cx, cy)); a.SetStart(MM(sx, sy))
    a.SetArcAngleAndEnd(pcbnew.EDA_ANGLE(angle_deg, pcbnew.DEGREES_T), False)
    a.SetLayer(pcbnew.Edge_Cuts); a.SetWidth(FromMM(0.1))
    board.Add(a)

R = 1.5
edge_line(R, 0, BW - R, 0)
edge_line(BW, R, BW, BH - R)
edge_line(BW - R, BH, R, BH)
edge_line(0, BH - R, 0, R)
edge_arc(BW - R, R, BW - R, 0, 90)
edge_arc(BW - R, BH - R, BW, BH - R, 90)
edge_arc(R, BH - R, R, BH, 90)
edge_arc(R, R, 0, R, 90)

# ------------------------------------------------------------------- zones ---
def poly_chain(pts):
    ch = pcbnew.SHAPE_LINE_CHAIN()
    for (x, y) in pts:
        ch.Append(FromMM(BX + x), FromMM(BY + y))
    ch.SetClosed(True)
    return ch

def gnd_zone(layer):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNetCode(nets['GND'].GetNetCode())
    z.Outline().AddOutline(poly_chain([(-0.5, -0.5), (BW + 0.5, -0.5), (BW + 0.5, BH + 0.5), (-0.5, BH + 0.5)]))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    z.SetLocalClearance(FromMM(0.25))
    z.SetMinThickness(FromMM(0.2))
    board.Add(z)

for layer in (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In4_Cu, pcbnew.B_Cu):
    gnd_zone(layer)

# antenna keepout: no copper pours / no vias on all layers; tracks allowed
kz = pcbnew.ZONE(board)
ls = pcbnew.LSET()
for layer in (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.In3_Cu, pcbnew.In4_Cu, pcbnew.B_Cu):
    ls.AddLayer(layer)
kz.SetLayerSet(ls)
kz.SetIsRuleArea(True)
kz.SetDoNotAllowCopperPour(True)
kz.SetDoNotAllowVias(True)
kz.SetDoNotAllowTracks(False)
kz.SetDoNotAllowPads(False)
kz.SetDoNotAllowFootprints(False)
kz.Outline().AddOutline(poly_chain([(19.0, -0.5), (BW + 0.5, -0.5), (BW + 0.5, 3.2), (19.0, 3.2)]))
kz.SetZoneName('ANT_KEEPOUT')
board.Add(kz)

# ---------------------------------------------------------- RF feed traces ---
def pad_pos(ref, num):
    return placed[ref].FindPadByNumber(num).GetPosition()

def track(p1, p2, width=0.35, layer=pcbnew.F_Cu, netname='ANT'):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(p1); t.SetEnd(p2)
    t.SetWidth(FromMM(width))
    t.SetLayer(layer)
    t.SetNetCode(nets[netname].GetNetCode())
    board.Add(t)

try:
    track(pad_pos('U3', '30'), pad_pos('FL1', '1'), netname='ANT')
    track(pad_pos('FL1', '3'), pad_pos('C35', '1'), netname='ANT50')
    track(pad_pos('C35', '1'), pad_pos('R15', '1'), netname='ANT50')
    track(pad_pos('R15', '2'), pad_pos('C34', '1'), netname='ANT_FEED')
    track(pad_pos('C34', '1'), pad_pos('E1', '1'), netname='ANT_FEED')
except Exception as e:
    print('RF preroute skipped:', e)

# ------------------------------------------------------------------- texts ---
def text(s, x, y, layer, size=0.8):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s)
    t.SetPosition(MM(x, y))
    t.SetLayer(layer)
    t.SetTextSize(VECTOR2I(FromMM(size), FromMM(size)))
    t.SetTextThickness(FromMM(size * 0.15))
    board.Add(t)

text('ANT KEEPOUT 5x3\nNO COPPER ALL LAYERS', 21.5, 1.6, pcbnew.Dwgs_User, 0.5)
text('discrete-potentiostat 24x22 RevA', 12, 10.2, pcbnew.F_SilkS, 0.7)
text('coil 20x20 under battery,\nleads to L1 pads', 4.5, 10.5, pcbnew.Cmts_User, 0.5)

# coil outline reference on drawings layer (stack position, shifted away from RF corner)
for (x1, y1, x2, y2) in [(0.5, 1.5, 20.5, 1.5), (20.5, 1.5, 20.5, 21.5), (20.5, 21.5, 0.5, 21.5), (0.5, 21.5, 0.5, 1.5)]:
    s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(MM(x1, y1)); s.SetEnd(MM(x2, y2))
    s.SetLayer(pcbnew.Cmts_User); s.SetWidth(FromMM(0.08))
    board.Add(s)


# ------------------------------------------------------------ legalization ---
FROZEN = {'U1','U2','U3','U4','U5','U6','Y1','FL1','E1','J1','J2','L1','BT1',
          'TP1','TP2','TP3'}
KEEPOUT = (19.0, -1.0, 24.5, 3.2)   # x0,y0,x1,y1 — movables must stay out

def fp_bbox(fp):
    xs, ys = [], []
    for pad in fp.Pads():
        bb = pad.GetBoundingBox()
        xs += [ToMM(bb.GetLeft()) - BX, ToMM(bb.GetRight()) - BX]
        ys += [ToMM(bb.GetTop()) - BY, ToMM(bb.GetBottom()) - BY]
    return min(xs), min(ys), max(xs), max(ys)

def legalize(rounds=2000, clearance=0.22, edge=0.45):
    import random
    rng = random.Random(7)
    refs = list(placed)
    for it in range(rounds):
        moved = False
        boxes = {r: fp_bbox(placed[r]) for r in refs}
        sides = {r: ('B' if placed[r].IsFlipped() else 'F') for r in refs}
        order = list(range(len(refs)))
        rng.shuffle(order)
        for i in order:
            for j in order:
                if i == j: continue
                r1, r2 = refs[i], refs[j]
                if sides[r1] != sides[r2]: continue
                if r2 in FROZEN and r1 in FROZEN: continue
                # ensure r2 is the movable one
                if r2 in FROZEN:
                    r1, r2 = r2, r1
                a, b = boxes[r1], boxes[r2]
                ox = min(a[2], b[2]) - max(a[0], b[0]) + clearance
                oy = min(a[3], b[3]) - max(a[1], b[1]) + clearance
                if ox <= 0 or oy <= 0: continue
                fp = placed[r2]
                pos = fp.GetPosition()
                x, y = ToMM(pos.x) - BX, ToMM(pos.y) - BY
                acx, acy = (a[0]+a[2])/2, (a[1]+a[3])/2
                bcx, bcy = (b[0]+b[2])/2, (b[1]+b[3])/2
                if ox < oy:
                    x += (ox + 0.03) * (1 if bcx >= acx else -1)
                else:
                    y += (oy + 0.03) * (1 if bcy >= acy else -1)
                # clamp to board, respecting part half-size
                hw = (b[2]-b[0])/2 + edge
                hh = (b[3]-b[1])/2 + edge
                x = min(max(x, hw), BW - hw)
                y = min(max(y, hh), BH - hh)
                fp.SetPosition(MM(x, y))
                nb = fp_bbox(fp)
                # keep movables out of the antenna keepout
                kx0, ky0, kx1, ky1 = KEEPOUT
                if r2 != 'E1' and not (nb[2] < kx0 or nb[0] > kx1 or nb[3] < ky0 or nb[1] > ky1):
                    fp.SetPosition(MM(x - (nb[2] - kx0) - 0.3 if x > kx0 else x, max(y, ky1 + (nb[3]-nb[1])/2 + 0.3)))
                boxes[r2] = fp_bbox(fp)
                moved = True
        if not moved:
            print(f'legalized in {it} rounds')
            break
    else:
        print('legalizer hit round limit')

legalize()

for ref, num in [('U3','30'),('U3','34'),('U3','35'),('U3','13')]:
    p = placed[ref].FindPadByNumber(num).GetPosition()
    print(f'pad {ref}.{num} at ({ToMM(p.x)-BX:.2f},{ToMM(p.y)-BY:.2f})')
from pcbnew import ToMM as _T
out = os.path.join(OUT, 'discrete-potentiostat.kicad_pcb')
pcbnew.SaveBoard(out, board)
print('saved', out, '| footprints:', len(placed), '| nets:', len(nets))
