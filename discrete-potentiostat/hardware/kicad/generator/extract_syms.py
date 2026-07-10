#!/usr/bin/env python3
"""Extract the base symbol blocks gen_sch.py needs from an installed KiCad
official symbol library (/usr/share/kicad/symbols). Run once before gen_sch.py."""
import os, sys
LIBDIR = sys.argv[1] if len(sys.argv) > 1 else '/usr/share/kicad/symbols'
HERE = os.path.dirname(os.path.abspath(__file__))
def get_block(txt, name):
    i = txt.find('(symbol "%s"' % name)
    if i < 0: return None
    depth = 0; j = i
    while j < len(txt):
        if txt[j] == '(': depth += 1
        elif txt[j] == ')':
            depth -= 1
            if depth == 0: return txt[i:j+1]
        j += 1
WANTED = [
    ('Device.kicad_sym', ['R','C','L','LED','Battery_Cell','Thermistor_NTC']),
    ('power.kicad_sym', ['GND','+3V3','+BATT','PWR_FLAG']),
    ('Connector.kicad_sym', ['TestPoint']),
    ('Connector_Generic.kicad_sym', ['Conn_01x03','Conn_01x05']),
    ('Battery_Management.kicad_sym', ['BQ51050BRHL']),
    ('Regulator_Linear.kicad_sym', ['AP131-15']),      # geometry base for MIC5205-3.3YM5
    ('Amplifier_Operational.kicad_sym', ['LM2904']),   # geometry base for OPA2391xDGK
]
for lib, names in WANTED:
    txt = open(os.path.join(LIBDIR, lib)).read()
    for name in names:
        blk = get_block(txt, name)
        assert blk, (lib, name)
        open(os.path.join(HERE, name + '.sym'), 'w').write(blk)
        print('extracted', name)
