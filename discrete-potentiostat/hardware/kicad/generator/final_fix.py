#!/usr/bin/env python3
"""Bridge DRC-unconnected pads to the nearest same-net copper point."""
import pcbnew, re, math
from pcbnew import VECTOR2I, FromMM, ToMM
import os
PCB = os.environ.get('PCB_FILE', os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'discrete-potentiostat.kicad_pcb')))
board = pcbnew.LoadBoard(PCB)
F, B = pcbnew.F_Cu, pcbnew.B_Cu

def drc_unconn():
    pcbnew.WriteDRCReport(board, 'drc_ff.txt', pcbnew.EDA_UNITS_MILLIMETRES, True)
    txt = open('drc_ff.txt').read()
    out = []
    for m in re.finditer(r'\[unconnected_items\][^\n]*\n((?:    [^\n]*\n){1,3})', txt):
        for pm in re.finditer(r'Pad (\w+) \[([^\]]*)\] of (\w+)', m.group(1)):
            num, net, ref = pm.groups()
            if net != 'GND' and (ref, num, net) not in out:
                out.append((ref, num, net))
    ncl = len(re.findall(r'^\[clearance\]', txt, re.M))
    return out, ncl

def pad_of(ref, num):
    fp = board.FindFootprintByReference(ref)
    return fp.FindPadByNumber(num)

def same_net_points(netname, exclude_pad):
    pts = []
    code = None
    for t in board.GetTracks():
        if t.GetNetname().lstrip('/') != netname: continue
        if t.GetClass() == 'PCB_TRACK':
            for p in (t.GetStart(), t.GetEnd()):
                pts.append((p, t.GetLayer()))
            mid = VECTOR2I((t.GetStart().x+t.GetEnd().x)//2, (t.GetStart().y+t.GetEnd().y)//2)
            pts.append((mid, t.GetLayer()))
        else:
            pts.append((t.GetPosition(), F)); pts.append((t.GetPosition(), B))
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad is exclude_pad: continue
            if pad.GetNetname().lstrip('/') != netname: continue
            layer = F if pad.IsOnLayer(F) else B
            pts.append((pad.GetPosition(), layer))
    return pts

unconn, base_cl = drc_unconn()
print('start:', len(unconn), 'pads, clearance', base_cl)
made = 0
for ref, num, net in unconn:
    pad = pad_of(ref, num)
    pp = pad.GetPosition()
    play = F if pad.IsOnLayer(F) else B
    best = None
    for (q, lay) in same_net_points(net, pad):
        if lay != play: continue
        d = math.hypot(ToMM(q.x-pp.x), ToMM(q.y-pp.y))
        if d < 2.0 and (best is None or d < best[0]):
            best = (d, q)
    if best is None:
        print(f'skip {ref}.{num} [{net}] — no same-layer copper within 2mm')
        continue
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pp); t.SetEnd(best[1])
    t.SetWidth(FromMM(0.1)); t.SetLayer(play)
    ni = board.FindNet(net) or board.FindNet('/'+net)
    if ni: t.SetNetCode(ni.GetNetCode())
    board.Add(t)
    made += 1
    print(f'bridge {ref}.{num} [{net}] {best[0]:.2f}mm on {"F" if play==F else "B"}')
pcbnew.SaveBoard(PCB, board)
after, after_cl = drc_unconn()
print('bridges:', made, '| unconnected now:', len(after), after, '| clearance:', base_cl, '->', after_cl)
