#!/usr/bin/env python3
"""Auto-route the discrete-potentiostat board.

Strategy on the 4-layer stack (F.Cu / In1 GND / In2 GND / B.Cu):
  1. GND: stitch a via next to every GND pad -> inner planes; pours refilled.
  2. All other nets: grid A* router on F.Cu/B.Cu with via moves.
Verification: KiCad connectivity (unconnected count must be 0).
"""
import os, heapq, math, time
T0 = time.time()
import pcbnew
from pcbnew import VECTOR2I, FromMM, ToMM

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.environ.get('PCB_FILE', os.path.join(HERE, '..', 'discrete-potentiostat.kicad_pcb'))
BX, BY, BW, BH = 100.0, 100.0, 24.0, 22.0
STEP = 0.05                     # routing grid (mm)
NX, NY = int(BW/STEP)+1, int(BH/STEP)+1
EDGE = 0.35                     # copper-to-edge margin
VIA_D, VIA_DRILL = 0.4, 0.2
KEEP = (19.0, -1.0, 24.5, 3.2)  # antenna keepout
POWER = {'3V3', '+BATT', 'V5OUT', 'AFE_PWR', 'RECT', 'COIL', 'AC1', 'AC2'}
W_SIG, W_PWR = 0.10, 0.20
CLR = 0.10

board = pcbnew.LoadBoard(PCB)
F, B = pcbnew.F_Cu, pcbnew.B_Cu
LAYERS = [F, B]

def mm_pt(p):  # absolute -> board-relative mm
    return ToMM(p.x) - BX, ToMM(p.y) - BY

def to_cell(x, y):
    return int(round(x/STEP)), int(round(y/STEP))

def cell_mm(i, j):
    return i*STEP, j*STEP

# ------------------------------------------------------------- collect data --
pads_by_net = {}
all_pads = []
for fp in board.GetFootprints():
    for pad in fp.Pads():
        net = pad.GetNetname().lstrip('/')
        x, y = mm_pt(pad.GetPosition())
        bb = pad.GetBoundingBox()
        rect = (ToMM(bb.GetLeft())-BX, ToMM(bb.GetTop())-BY,
                ToMM(bb.GetRight())-BX, ToMM(bb.GetBottom())-BY)
        on_f = pad.IsOnLayer(F)
        on_b = pad.IsOnLayer(B)
        tht = pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH
        rec = dict(net=net, x=x, y=y, rect=rect, f=on_f or tht, b=on_b or tht,
                   ref=fp.GetReference(), num=pad.GetNumber())
        all_pads.append(rec)
        pads_by_net.setdefault(net, []).append(rec)

RESUME = os.environ.get('RESUME') == '1'
exist_tracks = []       # untouchable pre-routes (RF) — only in fresh mode
resume_tracks = []      # (start,end,w,layer,net,obj) rebuilt as ripable routed segs
resume_vias = []
RF_NETS = ('ANT', 'ANT50', 'ANT_FEED')
for t in board.GetTracks():
    net = t.GetNetname().lstrip('/')
    if t.GetClass() == 'PCB_TRACK':
        rec = (mm_pt(t.GetStart()), mm_pt(t.GetEnd()), ToMM(t.GetWidth()), t.GetLayer(), net)
        if not RESUME or net in RF_NETS or net == 'GND':
            exist_tracks.append(rec)
        else:
            resume_tracks.append(rec + (t,))
    elif t.GetClass() == 'PCB_VIA' and RESUME:
        if net in RF_NETS or net == 'GND':
            x, y = mm_pt(t.GetPosition())
            exist_tracks.append(((x, y), (x, y), ToMM(t.GetWidth()), F, net))
        else:
            resume_vias.append((mm_pt(t.GetPosition()), net, t))

# ------------------------------------------------------------ obstacle grid --
import array
def new_grid():
    return [array.array('b', [0])*0 or array.array('b', [0]*(NX*NY)) for _ in LAYERS]

def stamp_rect(grid, li, x0, y0, x1, y1, infl):
    # block cells whose CENTER falls inside the inflated rect (exact bounds)
    i0 = max(0, int(math.ceil((x0-infl)/STEP - 1e-9)))
    i1 = min(NX-1, int(math.floor((x1+infl)/STEP + 1e-9)))
    j0 = max(0, int(math.ceil((y0-infl)/STEP - 1e-9)))
    j1 = min(NY-1, int(math.floor((y1+infl)/STEP + 1e-9)))
    g = grid[li]
    for j in range(j0, j1+1):
        base = j*NX
        for i in range(i0, i1+1):
            g[base+i] = 1

def stamp_seg(grid, li, x0, y0, x1, y1, r):
    n = max(1, int(math.hypot(x1-x0, y1-y0)/ (STEP*0.7)))
    for k in range(n+1):
        x = x0 + (x1-x0)*k/n; y = y0 + (y1-y0)*k/n
        stamp_rect(grid, li, x, y, x, y, r)

_BASE_CACHE = {}
def base_grid(width):
    """obstacles common to every net of this width class (cached, copied)"""
    if width in _BASE_CACHE:
        return [g[:] for g in _BASE_CACHE[width]]
    infl = CLR + width/2
    grid = new_grid()
    # board edge margin
    for li in range(2):
        g = grid[li]
        m = int((EDGE+width/2)/STEP)
        for j in range(NY):
            for i in range(NX):
                if i < m or j < m or i > NX-1-m or j > NY-1-m:
                    g[j*NX+i] = 1
    # antenna keepout (both layers, all auto nets)
    for li in range(2):
        stamp_rect(grid, li, KEEP[0], KEEP[1], KEEP[2], KEEP[3], 0)
    # existing tracks (RF pre-route)
    for (s, e, w, layer, net) in exist_tracks:
        for li, L in enumerate(LAYERS):
            if layer == L:
                stamp_seg(grid, li, s[0], s[1], e[0], e[1], infl + w/2)
    _BASE_CACHE[width] = [g[:] for g in grid]
    return grid

def stamp_pads(grid, width, skip_net):
    infl = CLR + width/2
    for p in all_pads:
        if p['net'] == skip_net: continue
        x0, y0, x1, y1 = p['rect']
        if p['f']: stamp_rect(grid, 0, x0, y0, x1, y1, infl)
        if p['b']: stamp_rect(grid, 1, x0, y0, x1, y1, infl)

routed_segs = []   # (x0,y0,x1,y1,w,li,net)
routed_vias = []   # (x,y,net)

def stamp_routed(grid, width, skip_net):
    infl = CLR + width/2
    for (x0, y0, x1, y1, w, li, net) in routed_segs:
        if net == skip_net: continue
        stamp_seg(grid, li, x0, y0, x1, y1, infl + w/2)
    for (x, y, net) in routed_vias:
        if net == skip_net: continue
        for li in range(2):
            stamp_rect(grid, li, x, y, x, y, infl + VIA_D/2)

# --------------------------------------------------------------- A* router ---
DIRS = [(1,0,1.0),(-1,0,1.0),(0,1,1.0),(0,-1,1.0),
        (1,1,1.42),(1,-1,1.42),(-1,1,1.42),(-1,-1,1.42)]

def astar(grid, starts, targets, via_ok, f_penalty=1.0):
    """starts/targets: sets of (i,j,li). Returns path list or None."""
    tgt = set(targets)
    if not starts or not tgt: return None
    txs = [t[0] for t in tgt]; tys = [t[1] for t in tgt]
    def h(i, j):
        return 0.9*min(abs(i-tx)+abs(j-ty) for tx, ty in zip(txs, tys)) if len(txs) < 40 else 0
    openq = []
    dist = {}
    prev = {}
    for s in starts:
        dist[s] = 0.0
        heapq.heappush(openq, (h(s[0], s[1]), 0.0, s))
    seen = set()
    while openq:
        _, d, cur = heapq.heappop(openq)
        if cur in seen: continue
        seen.add(cur)
        if cur in tgt:
            path = [cur]
            while cur in prev:
                cur = prev[cur]
                path.append(cur)
            return path[::-1]
        i, j, li = cur
        g = grid[li]
        for di, dj, c in DIRS:
            ni, nj = i+di, j+dj
            if not (0 <= ni < NX and 0 <= nj < NY): continue
            nxt = (ni, nj, li)
            if nxt in seen: continue
            if g[nj*NX+ni] and nxt not in tgt: continue
            nd = d + (c * f_penalty if li == 0 else c)
            if nd < dist.get(nxt, 1e18):
                dist[nxt] = nd; prev[nxt] = cur
                heapq.heappush(openq, (nd + h(ni, nj), nd, nxt))
        if via_ok and via_free(grid, i, j):
            nxt = (i, j, 1-li)
            if nxt not in seen:
                nd = d + 10.0            # via cost ~0.5mm detour (0.05 grid)
                if nd < dist.get(nxt, 1e18):
                    dist[nxt] = nd; prev[nxt] = cur
                    heapq.heappush(openq, (nd + h(i, j), nd, nxt))
    return None

def via_free(grid, i, j):
    r = int((VIA_D/2 + CLR)/STEP)
    for li in range(2):
        g = grid[li]
        for dj in range(-r, r+1):
            jj = j+dj
            if jj < 0 or jj >= NY: return False
            base = jj*NX
            for di in range(-r, r+1):
                ii = i+di
                if ii < 0 or ii >= NX: return False
                if g[base+ii]: return False
    return True

def pad_cells(p, grid, width):
    """cells inside pad usable as start/target on its layer(s)"""
    out = []
    x0, y0, x1, y1 = p['rect']
    i0, i1 = int(math.ceil(x0/STEP)), int(x1/STEP)
    j0, j1 = int(math.ceil(y0/STEP)), int(y1/STEP)
    ci, cj = to_cell(p['x'], p['y'])
    cand = {(ci, cj)}
    for j in range(max(0,j0), min(NY, j1+1)):
        for i in range(max(0,i0), min(NX, i1+1)):
            cand.add((i, j))
    for (i, j) in cand:
        if not (0 <= i < NX and 0 <= j < NY): continue
        if p['f']: out.append((i, j, 0))
        if p['b']: out.append((i, j, 1))
    return out

def seg_simplify(path):
    """merge collinear grid steps"""
    segs = []
    k = 0
    while k < len(path)-1:
        a = path[k]
        m = k+1
        if path[m][2] != a[2]:
            segs.append(('via', a, path[m])); k = m; continue
        di, dj = path[m][0]-a[0], path[m][1]-a[1]
        while (m+1 < len(path) and path[m+1][2] == a[2]
               and (path[m+1][0]-path[m][0], path[m+1][1]-path[m][1]) == (di, dj)):
            m += 1
        segs.append(('trk', a, path[m])); k = m
    return segs

def soft_grid(width, skip_net):
    """owner-name grid for routed copper of other nets"""
    infl = CLR + width/2
    own = [[None]*(NX*NY) for _ in LAYERS]
    def mark(li, x0, y0, x1, y1, r, name):
        i0 = max(0, int(math.ceil((min(x0,x1)-r)/STEP - 1e-9)))
        i1 = min(NX-1, int(math.floor((max(x0,x1)+r)/STEP + 1e-9)))
        j0 = max(0, int(math.ceil((min(y0,y1)-r)/STEP - 1e-9)))
        j1 = min(NY-1, int(math.floor((max(y0,y1)+r)/STEP + 1e-9)))
        o = own[li]
        for j in range(j0, j1+1):
            for i in range(i0, i1+1):
                o[j*NX+i] = name
    for (x0, y0, x1, y1, w, li, net) in routed_segs:
        if net == skip_net: continue
        n = max(1, int(math.hypot(x1-x0, y1-y0)/(STEP*0.7)))
        for k in range(n+1):
            x = x0+(x1-x0)*k/n; y = y0+(y1-y0)*k/n
            mark(li, x, y, x, y, infl + w/2, net)
    for (x, y, net) in routed_vias:
        if net == skip_net: continue
        for li in range(2):
            mark(li, x, y, x, y, infl + VIA_D/2, net)
    return own

def astar_soft(grid, own, starts, targets, via_ok):
    tgt = set(targets)
    if not starts or not tgt: return None, None
    openq = []; dist = {}; prev = {}
    for s in starts:
        dist[s] = 0.0
        heapq.heappush(openq, (0.0, s))
    seen = set()
    while openq:
        d, cur = heapq.heappop(openq)
        if cur in seen: continue
        seen.add(cur)
        if cur in tgt:
            path = [cur]; c = cur
            while c in prev:
                c = prev[c]; path.append(c)
            path = path[::-1]
            crossed = set()
            for (i, j, li) in path:
                o = own[li][j*NX+i]
                if o: crossed.add(o)
            return path, crossed
        i, j, li = cur
        g = grid[li]; o = own[li]
        for di, dj, c in DIRS:
            ni, nj = i+di, j+dj
            if not (0 <= ni < NX and 0 <= nj < NY): continue
            nxt = (ni, nj, li)
            if nxt in seen: continue
            if g[nj*NX+ni] and nxt not in tgt: continue
            pen = 300.0 if o[nj*NX+ni] else 0.0
            nd = d + c + pen
            if nd < dist.get(nxt, 1e18):
                dist[nxt] = nd; prev[nxt] = cur
                heapq.heappush(openq, (nd, nxt))
        if via_ok and via_free(grid, i, j):
            nxt = (i, j, 1-li)
            if nxt not in seen:
                nd = d + 16.0
                if nd < dist.get(nxt, 1e18):
                    dist[nxt] = nd; prev[nxt] = cur
                    heapq.heappush(openq, (nd, nxt))
    return None, None

def rip_net(name):
    for it in items_by_net.get(name, []):
        board.Remove(it)
    items_by_net[name] = []
    global routed_segs, routed_vias
    routed_segs = [s for s in routed_segs if s[6] != name]
    routed_vias = [v for v in routed_vias if v[2] != name]
    net_tree[name] = None
    net_todo[name] = pads_by_net[name][1:]

# ------------------------------------------------------------- route driver --
items_by_net = {}

def add_track(x0, y0, x1, y1, w, layer, netname):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(VECTOR2I(FromMM(BX+x0), FromMM(BY+y0)))
    t.SetEnd(VECTOR2I(FromMM(BX+x1), FromMM(BY+y1)))
    t.SetWidth(FromMM(w))
    t.SetLayer(layer)
    t.SetNetCode(board.FindNet(netname).GetNetCode() if board.FindNet(netname) else 0)
    board.Add(t)
    items_by_net.setdefault(netname.lstrip('/'), []).append(t)

def add_via(x, y, netname):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(VECTOR2I(FromMM(BX+x), FromMM(BY+y)))
    v.SetDrill(FromMM(VIA_DRILL))
    v.SetWidth(FromMM(VIA_D))
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    v.SetLayerPair(F, B)
    net = board.FindNet(netname)
    if net: v.SetNetCode(net.GetNetCode())
    board.Add(v)
    items_by_net.setdefault(netname.lstrip('/'), []).append(v)

def find_netname(n):
    ni = board.FindNet(n)
    if ni: return n
    ni = board.FindNet('/' + n)
    return ('/' + n) if ni else n

# ---- resume mode: seed router state from existing copper -------------------
if RESUME:
    for (s, e, w, layer, net, obj) in resume_tracks:
        li = 0 if layer == F else 1
        routed_segs.append((s[0], s[1], e[0], e[1], w, li, net))
        items_by_net.setdefault(net, []).append(obj)
    for ((x, y), net, obj) in resume_vias:
        routed_vias.append((x, y, net))
        items_by_net.setdefault(net, []).append(obj)

def connected_components(name):
    """geometric union-find over pads + routed segs/vias of a net"""
    pads = pads_by_net[name]
    nodes = []   # (kind, idx, key points [(x,y,li)...])
    for k, p in enumerate(pads):
        pts = [(p['x'], p['y'], 0 if p['f'] else 1)]
        if p['f'] and p['b']:
            pts.append((p['x'], p['y'], 1))
        nodes.append(('pad', k, pts, p))
    for s in [s for s in routed_segs if s[6] == name]:
        nodes.append(('seg', None, [(s[0], s[1], s[5]), (s[2], s[3], s[5])], s))
    for v in [v for v in routed_vias if v[2] == name]:
        nodes.append(('via', None, [(v[0], v[1], 0), (v[0], v[1], 1)], v))
    parent = list(range(len(nodes)))
    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb
    def near(p1, p2):
        return p1[2] == p2[2] and abs(p1[0]-p2[0]) < 0.65 and abs(p1[1]-p2[1]) < 0.65
    def pad_touch(p, pt):
        x0, y0, x1, y1 = p['rect']
        li = 0 if p['f'] else 1
        lis = {0, 1} if (p['f'] and p['b']) else {li}
        return pt[2] in lis and x0-0.1 <= pt[0] <= x1+0.1 and y0-0.1 <= pt[1] <= y1+0.1
    for a in range(len(nodes)):
        for b in range(a+1, len(nodes)):
            ka, _, pa, ra = nodes[a]
            kb, _, pb, rb = nodes[b]
            hit = False
            if ka == 'pad' and kb == 'pad':
                continue
            if ka == 'pad' or kb == 'pad':
                pad = ra if ka == 'pad' else rb
                pts = pb if ka == 'pad' else pa
                hit = any(pad_touch(pad, q) for q in pts)
            else:
                hit = any(near(q1, q2) for q1 in pa for q2 in pb)
            if hit: union(a, b)
    comp_of_pad = {}
    for n_i, (k, idx, pts, r) in enumerate(nodes):
        if k == 'pad':
            comp_of_pad[idx] = find(n_i)
    root = comp_of_pad.get(0)
    todo_pads = [pads[k] for k, c in comp_of_pad.items() if c != root and k != 0]
    return todo_pads

# 1) route all signal/power nets first --------------------------------------
def net_order(item):
    name, pads = item
    xs = [p['x'] for p in pads]; ys = [p['y'] for p in pads]
    return (-len(pads), (max(xs)-min(xs)) + (max(ys)-min(ys)))

pad_stub = {}      # (ref,num) -> set of extra start cells (stub + via, both layers)

def pre_fanout(refs=('U3', 'U1')):
    """dog-bone escape: stub + via to B for used pads of fine-pitch ICs"""
    grid = base_grid(W_SIG)
    stamp_pads(grid, W_SIG, skip_net='__none__')
    stamp_routed(grid, W_SIG, skip_net='__none__')
    centers = {}
    for fp in board.GetFootprints():
        centers[fp.GetReference()] = mm_pt(fp.GetPosition())
    done = 0
    idx = 0
    for p in all_pads:
        if p['ref'] not in refs: continue
        if p['net'] in ('', 'GND', 'ANT', 'ANT50', 'ANT_FEED'): continue
        if not p['f']: continue
        if len(pads_by_net.get(p['net'], [])) < 2: continue
        idx += 1
        cx, cy = centers[p['ref']]
        dx, dy = p['x']-cx, p['y']-cy
        if abs(dx) > abs(dy): dx, dy = (1 if dx > 0 else -1), 0
        else: dx, dy = 0, (1 if dy > 0 else -1)
        dists = (0.55, 0.85, 1.15, 1.45) if idx % 2 else (0.7, 1.0, 1.3, 1.6)
        for dist in dists:
            x, y = p['x']+dx*dist, p['y']+dy*dist
            x, y = round(x/STEP)*STEP, round(y/STEP)*STEP
            i, j = to_cell(x, y)
            if not (0 <= i < NX and 0 <= j < NY): continue
            if KEEP[0] < x < KEEP[2] and KEEP[1] < y < KEEP[3]: continue
            if not via_free(grid, i, j): continue
            nn = find_netname(p['net'])
            add_track(p['x'], p['y'], x, y, W_SIG, F, nn)
            add_via(x, y, nn)
            routed_segs.append((p['x'], p['y'], x, y, W_SIG, 0, p['net']))
            routed_vias.append((x, y, p['net']))
            stamp_seg(grid, 0, p['x'], p['y'], x, y, W_SIG + CLR)
            stamp_rect(grid, 0, x, y, x, y, VIA_D/2 + CLR + W_SIG/2)
            stamp_rect(grid, 1, x, y, x, y, VIA_D/2 + CLR + W_SIG/2)
            cells = set()
            n = max(1, int(dist/STEP))
            for k in range(n+1):
                ii, jj = to_cell(p['x']+dx*dist*k/n, p['y']+dy*dist*k/n)
                cells.add((ii, jj, 0))
            cells.add((i, j, 0)); cells.add((i, j, 1))
            pad_stub[(p['ref'], p['num'])] = cells
            done += 1
            break
    print('fanout stubs placed:', done)

# pre_fanout()  # disabled: greedy stubs congest the escape ring

net_tree = {}      # name -> set of cells claimed by routed copper/pads connected
net_todo = {}      # name -> list of pads still unconnected

todo = [(n, ps) for n, ps in pads_by_net.items()
        if n not in ('GND', '') and len(ps) > 1
        and n not in ('ANT', 'ANT50', 'ANT_FEED')]
for name, pads in todo:
    net_todo[name] = connected_components(name) if RESUME else pads[1:]
    net_tree[name] = None      # lazy init with pads[0]

def route_net(name, pads0):
    w = W_PWR if name in POWER else W_SIG
    grid = base_grid(w)
    stamp_pads(grid, w, skip_net=name)
    stamp_routed(grid, w, skip_net=name)
    if net_tree[name] is None:
        t = set(pad_cells(pads0, grid, w)) | pad_stub.get((pads0['ref'], pads0['num']), set())
        for s in [s for s in routed_segs if s[6] == name]:
            n = max(1, int(math.hypot(s[2]-s[0], s[3]-s[1])/STEP))
            for k in range(n+1):
                ii, jj = to_cell(s[0]+(s[2]-s[0])*k/n, s[1]+(s[3]-s[1])*k/n)
                t.add((ii, jj, s[5]))
        for v in [v for v in routed_vias if v[2] == name]:
            ii, jj = to_cell(v[0], v[1])
            t.add((ii, jj, 0)); t.add((ii, jj, 1))
        net_tree[name] = t
    tree = net_tree[name]
    still = []
    xs = [q['x'] for q in pads_by_net[name]]; ys = [q['y'] for q in pads_by_net[name]]
    long_net = (max(xs)-min(xs)) + (max(ys)-min(ys)) > 4.0
    for p in sorted(net_todo[name], key=lambda q: math.hypot(q['x']-pads0['x'], q['y']-pads0['y'])):
        starts = set(pad_cells(p, grid, w)) | pad_stub.get((p['ref'], p['num']), set())
        path = astar(grid, starts, tree, via_ok=True, f_penalty=2.0 if long_net else 1.0)
        if path is None:
            still.append(p)
            continue
        for kind, a, b in seg_simplify(path):
            if kind == 'via':
                x, y = cell_mm(a[0], a[1])
                add_via(x, y, find_netname(name))
                routed_vias.append((x, y, name))
                tree.add((a[0], a[1], 0)); tree.add((a[0], a[1], 1))
                stamp_rect(grid, 0, x, y, x, y, VIA_D/2 + CLR)
                stamp_rect(grid, 1, x, y, x, y, VIA_D/2 + CLR)
            else:
                x0, y0 = cell_mm(a[0], a[1]); x1, y1 = cell_mm(b[0], b[1])
                add_track(x0, y0, x1, y1, w, LAYERS[a[2]], find_netname(name))
                routed_segs.append((x0, y0, x1, y1, w, a[2], name))
                n = max(abs(b[0]-a[0]), abs(b[1]-a[1]))
                for k in range(n+1):
                    ii = a[0] + (b[0]-a[0])*k//n if n else a[0]
                    jj = a[1] + (b[1]-a[1])*k//n if n else a[1]
                    tree.add((ii, jj, a[2]))
        tree |= starts
    net_todo[name] = still
    return still

rip_budget = 60
queue = [n for n, _ in sorted(todo, key=net_order)]
qi = 0
while qi < len(queue):
    if time.time() - T0 > 420:
        print('rip-up time guard hit'); break
    name = queue[qi]; qi += 1
    if not net_todo[name]: continue
    left = route_net(name, pads_by_net[name][0])
    if not left: continue
    # rip-up: find what blocks the first failed endpoint
    w = W_PWR if name in POWER else W_SIG
    grid = base_grid(w)
    stamp_pads(grid, w, skip_net=name)
    own = soft_grid(w, skip_net=name)
    p = left[0]
    starts = set(pad_cells(p, grid, w)) | pad_stub.get((p['ref'], p['num']), set())
    tree = net_tree[name] or set(pad_cells(pads_by_net[name][0], grid, w))
    path, crossed = astar_soft(grid, own, starts, tree, via_ok=True)
    if path is None or not crossed or rip_budget <= 0:
        continue
    victims = [v for v in crossed if v != name and v not in POWER and len(pads_by_net.get(v, [])) <= 3][:2]
    if not victims: continue
    rip_budget -= len(victims)
    for v in victims:
        rip_net(v)
    rip_net(name)
    queue.append(name)
    for v in victims:
        queue.append(v)

# final mop-up passes without rip
for pass_no in range(3):
    for name, pads in sorted([(n, ps) for n, ps in todo if net_todo[n]], key=net_order):
        route_net(name, pads[0])

fails = [(n, p['ref'], p['num']) for n in net_todo for p in net_todo[n]]
print('failed connections:', len(fails), fails[:12])

# 2) GND: shared stitching vias (after signals, into leftover space) ---------
if RESUME:
    print('resume: skipping GND stitch (already present)')

gnd_grid = base_grid(W_SIG)
stamp_pads(gnd_grid, W_SIG, skip_net='GND')
stamp_routed(gnd_grid, W_SIG, skip_net='__none__')
if RESUME:
    pads_by_net['GND'] = []   # no re-stitch
gnd_vias = []          # (x, y)
gnd_done = 0
for p in sorted(pads_by_net.get('GND', []), key=lambda q: (q['x'], q['y'])):
    if any(math.hypot(p['x']-vx, p['y']-vy) < 1.6 for vx, vy in gnd_vias):
        gnd_done += 1
        continue
    ci, cj = to_cell(p['x'], p['y'])
    placed_via = False
    for r in range(2, 20):
        ring = [(di, dj) for dj in range(-r, r+1)
                for di in ((-r, r) if abs(dj) != r else range(-r, r+1))]
        ring.sort(key=lambda d: d[0]*d[0]+d[1]*d[1])
        for di, dj in ring:
            i, j = ci+di, cj+dj
            if not (0 <= i < NX and 0 <= j < NY): continue
            x, y = cell_mm(i, j)
            if KEEP[0] < x < KEEP[2] and KEEP[1] < y < KEEP[3]: continue
            if not via_free(gnd_grid, i, j): continue
            li = 0 if p['f'] else 1
            add_track(p['x'], p['y'], x, y, W_SIG, LAYERS[li], find_netname('GND'))
            add_via(x, y, find_netname('GND'))
            stamp_rect(gnd_grid, 0, x, y, x, y, VIA_D/2 + CLR + W_SIG)
            stamp_rect(gnd_grid, 1, x, y, x, y, VIA_D/2 + CLR + W_SIG)
            stamp_seg(gnd_grid, li, p['x'], p['y'], x, y, W_SIG/2 + CLR + W_SIG/2)
            gnd_vias.append((x, y))
            gnd_done += 1
            placed_via = True
            break
        if placed_via: break
print('GND pads stitched or shared:', gnd_done, '/', len(pads_by_net.get('GND', [])))

# 3) refill zones, save, report connectivity --------------------------------
filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())
pcbnew.SaveBoard(PCB, board)
board2 = pcbnew.LoadBoard(PCB)
conn = board2.GetConnectivity()
unconn = conn.GetUnconnectedCount(True)
print('UNCONNECTED after routing:', unconn)
