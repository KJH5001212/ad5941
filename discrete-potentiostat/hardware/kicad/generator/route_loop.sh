#!/bin/bash
set -e
cd "$(dirname "$0")"
for i in 1 2 3 4; do
  python3 - <<'PY'
import pcbnew, re, json
import os
b = pcbnew.LoadBoard(os.path.abspath(os.path.join(os.path.dirname(os.path.abspath('route_loop.sh')), '..', 'discrete-potentiostat.kicad_pcb')))
pcbnew.WriteDRCReport(b, 'drc_loop.txt', pcbnew.EDA_UNITS_MILLIMETRES, True)
txt = open('drc_loop.txt').read()
todo = {}
for m in re.finditer(r'\[unconnected_items\][^\n]*\n((?:    [^\n]*\n){1,3})', txt):
    for pm in re.finditer(r'Pad (\w+) \[([^\]]*)\] of (\w+)', m.group(1)):
        num, net, ref = pm.groups()
        if net == 'GND': continue
        todo.setdefault(net, [])
        if [ref, num] not in todo[net]: todo[net].append([ref, num])
json.dump(todo, open('todo_override.json', 'w'))
n = sum(len(v) for v in todo.values())
print('loop:', n, 'unconnected signal pads:', todo)
open('loop_count.txt','w').write(str(n))
PY
  n=$(cat loop_count.txt)
  if [ "$n" = "0" ]; then echo "ALL SIGNALS ROUTED"; break; fi
  RESUME=1 timeout 560 python3 route_pcb.py 2>&1 | grep -E "failed connections|UNCONNECTED|guard" || true
done
