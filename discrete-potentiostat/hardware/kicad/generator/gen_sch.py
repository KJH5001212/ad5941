#!/usr/bin/env python3
"""Generate discrete-potentiostat.kicad_sch (KiCad 7) + expected netlist.

Style: every connected pin gets a short stub wire + local net label (or a power
symbol whose anchor sits on the stub end). Rot-0 placement only -> transform is
(X + px, Y - py). Verified against kicad-cli netlist export.
"""
import os, re, uuid, json

GEN = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(GEN, '..'))

def U(): return str(uuid.uuid4())

# ---------------------------------------------------------------- symbols ---
def load(name):
    return open(os.path.join(GEN, name + '.sym')).read()

def rename_block(blk, old, new):
    blk = blk.replace('(symbol "%s"' % old, '(symbol "%s"' % new, 1)
    blk = blk.replace('(symbol "%s_' % old, '(symbol "%s_' % new)
    return blk

def set_property(blk, prop, value):
    pat = r'(\(property "%s" ")[^"]*(")' % prop
    if re.search(pat, blk):
        return re.sub(pat, r'\g<1>%s\g<2>' % value, blk, count=1)
    return blk

def prefix_libname(blk, name, libid):
    # outer name -> full lib id, inner unit names keep bare name
    return blk.replace('(symbol "%s"' % name, '(symbol "%s"' % libid, 1)

CUSTOM_REF35 = '''(symbol "pstat:REF35102QDBVR" (in_bom yes) (on_board yes)
      (property "Reference" "U" (at -7.62 8.89 0) (effects (font (size 1.27 1.27))))
      (property "Value" "REF35102QDBVR" (at 1.27 8.89 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "Package_TO_SOT_SMD:SOT-23-6" (at 0 -13.97 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "https://www.ti.com/lit/ds/symlink/ref35.pdf" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (property "Description" "1.024V ultra-low-power precision voltage reference, SOT-23-6" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "REF35102QDBVR_0_1"
        (rectangle (start -7.62 7.62) (end 7.62 -7.62)
          (stroke (width 0.254) (type default)) (fill (type background)))
      )
      (symbol "REF35102QDBVR_1_1"
        (pin power_in line (at -10.16 5.08 0) (length 2.54)
          (name "VIN" (effects (font (size 1.27 1.27))))
          (number "4" (effects (font (size 1.27 1.27)))))
        (pin input line (at -10.16 0 0) (length 2.54)
          (name "EN" (effects (font (size 1.27 1.27))))
          (number "3" (effects (font (size 1.27 1.27)))))
        (pin power_out line (at 10.16 5.08 180) (length 2.54)
          (name "VREF" (effects (font (size 1.27 1.27))))
          (number "6" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 10.16 0 180) (length 2.54)
          (name "NR" (effects (font (size 1.27 1.27))))
          (number "5" (effects (font (size 1.27 1.27)))))
        (pin power_in line (at -2.54 -10.16 90) (length 2.54)
          (name "GND" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
        (pin power_in line (at 2.54 -10.16 90) (length 2.54)
          (name "GND" (effects (font (size 1.27 1.27))))
          (number "2" (effects (font (size 1.27 1.27)))))
      )
    )'''

# MDBT42Q: pads 1-21 left (top->bottom), 22-41 right (bottom->top).
# Pads 1-21 and 37-41 web-verified; 22-36 sequential reconstruction (see notes).
MDBT_PINS = [
    (1,'GND','power_in'), (2,'P0.25','bidirectional'), (3,'P0.26','bidirectional'),
    (4,'P0.27','bidirectional'), (5,'P0.28/AIN4','bidirectional'), (6,'P0.29/AIN5','bidirectional'),
    (7,'P0.30/AIN6','bidirectional'), (8,'P0.31/AIN7','bidirectional'), (9,'DEC4','passive'),
    (10,'DCC','passive'), (11,'VDD','power_in'), (12,'GND','power_in'),
    (13,'P0.00/XL1','bidirectional'), (14,'P0.01/XL2','bidirectional'), (15,'P0.02/AIN0','bidirectional'),
    (16,'P0.03/AIN1','bidirectional'), (17,'P0.04/AIN2','bidirectional'), (18,'P0.05/AIN3','bidirectional'),
    (19,'P0.06','bidirectional'), (20,'P0.07','bidirectional'), (21,'P0.08','bidirectional'),
    (22,'P0.09/NFC1','bidirectional'), (23,'P0.10/NFC2','bidirectional'), (24,'P0.11','bidirectional'),
    (25,'P0.12','bidirectional'), (26,'P0.13','bidirectional'), (27,'P0.14','bidirectional'),
    (28,'P0.15','bidirectional'), (29,'P0.16','bidirectional'), (30,'GND','power_in'),
    (31,'P0.17','bidirectional'), (32,'P0.18','bidirectional'), (33,'P0.19','bidirectional'),
    (34,'P0.20','bidirectional'), (35,'P0.21/RESET','bidirectional'), (36,'P0.23','bidirectional'),
    (37,'SWDCLK','input'), (38,'SWDIO','bidirectional'), (39,'P0.22','bidirectional'),
    (40,'GND','power_in'), (41,'P0.24','bidirectional'),
]

def make_mdbt():
    top = 27.94
    lines = ['(symbol "pstat:MDBT42Q" (in_bom yes) (on_board yes)',
      '      (property "Reference" "U" (at -17.78 29.21 0) (effects (font (size 1.27 1.27))))',
      '      (property "Value" "MDBT42Q-512KV2" (at 0 29.21 0) (effects (font (size 1.27 1.27))))',
      '      (property "Footprint" "pstat:Raytac_MDBT42Q" (at 0 -33.02 0) (effects (font (size 1.27 1.27)) hide))',
      '      (property "Datasheet" "https://www.raytac.com/product/ins.php?index_id=31" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))',
      '      (property "Description" "Raytac MDBT42Q nRF52832 BLE module, 41-pad LGA" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))',
      '      (symbol "MDBT42Q_0_1"',
      '        (rectangle (start -15.24 %.2f) (end 15.24 %.2f)' % (top, -top),
      '          (stroke (width 0.254) (type default)) (fill (type background)))',
      '      )',
      '      (symbol "MDBT42Q_1_1"']
    for num, name, etype in MDBT_PINS:
        if num <= 21:
            x, ang = -20.32, 0
            y = top - 2.54 - (num - 1) * 2.54
        else:
            x, ang = 20.32, 180
            y = -top + 2.54 + (num - 22) * 2.54
        lines.append('        (pin %s line (at %.2f %.2f %d) (length 5.08)' % (etype, x, y, ang))
        lines.append('          (name "%s" (effects (font (size 1.27 1.27))))' % name)
        lines.append('          (number "%d" (effects (font (size 1.27 1.27)))))' % num)
    lines.append('      )')
    lines.append('    )')
    return '\n'.join(lines)

def build_symbol_library():
    syms = {}
    for base in ['R','C','L','LED','Battery_Cell','Thermistor_NTC']:
        syms['Device:'+base] = prefix_libname(load(base), base, 'Device:'+base)
    for base in ['GND','+3V3','+BATT','PWR_FLAG']:
        syms['power:'+base] = prefix_libname(load(base), base, 'power:'+base)
    syms['Connector:TestPoint'] = prefix_libname(load('TestPoint'), 'TestPoint', 'Connector:TestPoint')
    for base in ['Conn_01x03','Conn_01x05']:
        syms['Connector_Generic:'+base] = prefix_libname(load(base), base, 'Connector_Generic:'+base)
    bq = rename_block(load('BQ51050BRHL'), 'BQ51050BRHL', 'BQ51013BRHL')
    bq = bq.replace('(name "BAT"', '(name "OUT"').replace('(name "TERM"', '(name "EN1"')
    bq = set_property(bq, 'Value', 'BQ51013BRHL')
    bq = set_property(bq, 'Datasheet', 'https://www.ti.com/lit/ds/symlink/bq51013b.pdf')
    bq = set_property(bq, 'Description', 'Qi WPC v1.2 wireless power receiver, regulated 5V output, VQFN-20 (pin map = BQ51050BRHL family; pin4 OUT, pin10 EN1)')
    syms['pstat:BQ51013BRHL'] = prefix_libname(bq, 'BQ51013BRHL', 'pstat:BQ51013BRHL')
    mcp = load('MCP73832-2-OT')
    mcp = set_property(mcp, 'Value', 'MCP73832T-2ACI/OT')
    syms['Battery_Management:MCP73832-2-OT'] = prefix_libname(mcp, 'MCP73832-2-OT', 'Battery_Management:MCP73832-2-OT')
    mic = rename_block(load('AP131-15'), 'AP131-15', 'MIC5205-3.3YM5')
    mic = set_property(mic, 'Value', 'MIC5205-3.3YM5')
    mic = set_property(mic, 'Footprint', 'Package_TO_SOT_SMD:SOT-23-5')
    mic = set_property(mic, 'Datasheet', 'https://ww1.microchip.com/downloads/en/DeviceDoc/MIC5205-Data-Sheet-DS20006424A.pdf')
    mic = set_property(mic, 'Description', '150mA LDO 3.3V, low-noise, SOT-23-5 (pin-compatible AP131 base symbol)')
    syms['pstat:MIC5205-3.3YM5'] = prefix_libname(mic, 'MIC5205-3.3YM5', 'pstat:MIC5205-3.3YM5')
    opa = rename_block(load('LM2904'), 'LM2904', 'OPA2391xDGK')
    opa = set_property(opa, 'Value', 'OPA2391')
    opa = set_property(opa, 'Footprint', 'Package_SO:VSSOP-8_3.0x3.0mm_P0.65mm')
    opa = set_property(opa, 'Datasheet', 'https://www.ti.com/lit/ds/symlink/opa2391.pdf')
    opa = set_property(opa, 'Description', 'Dual precision op amp, fA input bias, RRIO, VSSOP-8')
    syms['pstat:OPA2391xDGK'] = prefix_libname(opa, 'OPA2391xDGK', 'pstat:OPA2391xDGK')
    syms['pstat:REF35102QDBVR'] = CUSTOM_REF35
    syms['pstat:MDBT42Q'] = make_mdbt()
    return syms

# ------------------------------------------------------------- pin parser ---
def parse_pins(blk):
    """return {(unit, number): (x, y, angle)} — unit from enclosing symbol name suffix _N_M"""
    pins = {}
    for m in re.finditer(r'\(symbol "([^"]+_(\d+)_\d+)"', blk):
        sub_start = m.start()
        depth = 0; j = sub_start
        while j < len(blk):
            if blk[j] == '(': depth += 1
            elif blk[j] == ')':
                depth -= 1
                if depth == 0: break
            j += 1
        sub = blk[sub_start:j+1]
        unit = int(m.group(2))
        for pm in re.finditer(r'\(pin\s+\S+\s+\S+\s+\(at\s+([-\d.]+)\s+([-\d.]+)\s+(\d+)\)\s*\(length\s+([\d.]+)\)(?:\s+hide)?\s*\(name\s+"[^"]*"[^\n]*\n[^\n]*\(number\s+"([^"]+)"', sub):
            x, y, ang, ln, num = float(pm.group(1)), float(pm.group(2)), int(pm.group(3)), float(pm.group(4)), pm.group(5)
            pins[(unit, num)] = (x, y, ang)
    return pins

# -------------------------------------------------------------- instances ---
INSTS = []
def add(ref, libid, X, Y, value, fp, pins, unit=1, dnp=False):
    INSTS.append(dict(ref=ref, libid=libid, X=X, Y=Y, value=value, fp=fp,
                      pins=pins, unit=unit, dnp=dnp))

GND, V3, VB = '=GND', '=+3V3', '=+BATT'

def layout():
    # ---------------- Qi receiver / charger ----------------
    add('U1','pstat:BQ51013BRHL', 90, 90, 'BQ51013BRHLR','Package_DFN_QFN:Texas_VQFN-RHL-20', {
        '1':GND, '2':'AC1', '3':'BOOT1', '4':'V5OUT', '5':'CLAMP1', '6':'COMM1',
        '7':None, '8':None, '9':GND, '10':GND, '11':GND, '12':'ILIM',
        '13':'TS', '14':'FOD', '15':'COMM2', '16':'CLAMP2', '17':'BOOT2',
        '18':'RECT', '19':'AC2', '20':GND, '21':GND})
    add('L1','Device:L', 28, 68, '760308101214 26uH','pstat:Wurth_760308101214', {'1':'COIL','2':'AC2'})
    add('C1','Device:C', 42, 68, '100n C0G 50V','Capacitor_SMD:C_1210_3225Metric', {'1':'COIL','2':'AC1'})
    add('C2','Device:C', 54, 68, '1n C0G 50V','Capacitor_SMD:C_0603_1608Metric', {'1':'AC1','2':'AC2'})
    add('C3','Device:C', 28, 128, '10n','Capacitor_SMD:C_0402_1005Metric', {'1':'BOOT1','2':'AC1'})
    add('C4','Device:C', 40, 128, '10n','Capacitor_SMD:C_0402_1005Metric', {'1':'BOOT2','2':'AC2'})
    add('C5','Device:C', 52, 128, '22n 25V','Capacitor_SMD:C_0402_1005Metric', {'1':'COMM1','2':'AC1'})
    add('C6','Device:C', 64, 128, '22n 25V','Capacitor_SMD:C_0402_1005Metric', {'1':'COMM2','2':'AC2'})
    add('C7','Device:C', 76, 128, '470n 25V','Capacitor_SMD:C_0603_1608Metric', {'1':'CLAMP1','2':'AC1'})
    add('C8','Device:C', 88, 128, '470n 25V','Capacitor_SMD:C_0603_1608Metric', {'1':'CLAMP2','2':'AC2'})
    add('C9','Device:C', 126, 128, '10u 25V','Capacitor_SMD:C_0805_2012Metric', {'1':'RECT','2':GND})
    add('R1','Device:R', 140, 128, '2.32k 1%','Resistor_SMD:R_0402_1005Metric', {'1':'ILIM','2':'FOD'})
    add('R2','Device:R', 152, 128, '200R 1%','Resistor_SMD:R_0402_1005Metric', {'1':'FOD','2':GND})
    add('R3','Device:R', 268, 107, '49.9k 1%','Resistor_SMD:R_0402_1005Metric', {'1':'PROG','2':GND})
    add('TH1','Device:Thermistor_NTC', 176, 128, 'NTC 10k','Resistor_SMD:R_0402_1005Metric', {'1':'TS','2':GND})
    add('C10','Device:C', 135, 52, '4.7u 10V','Capacitor_SMD:C_0603_1608Metric', {'1':'V5OUT','2':GND})
    add('C11','Device:C', 147, 52, '100n','Capacitor_SMD:C_0402_1005Metric', {'1':'V5OUT','2':GND})
    add('R4','Device:R', 159, 52, '100k','Resistor_SMD:R_0402_1005Metric', {'1':V3,'2':'CHG_N'})
    add('U6','Battery_Management:MCP73832-2-OT', 245, 102, 'MCP73832T-2ACI/OT','Package_TO_SOT_SMD:SOT-23-5', {
        '4':'V5OUT', '2':GND, '3':VB, '5':'PROG', '1':'CHG_N'})
    add('C27','Device:C', 280, 102, '4.7u 10V','Capacitor_SMD:C_0603_1608Metric', {'1':VB,'2':GND})
    # ---------------- battery + LDO + monitor ----------------
    add('BT1','Device:Battery_Cell', 230, 60, 'LIR2032','Battery:BT_Keystone_3034_1x2032', {'1':VB,'2':GND})
    add('U2','pstat:MIC5205-3.3YM5', 270, 55, 'MIC5205-3.3YM5','Package_TO_SOT_SMD:SOT-23-5', {
        '1':VB, '2':GND, '3':VB, '4':'BYP', '5':V3})
    add('C12','Device:C', 250, 75, '1u 10V','Capacitor_SMD:C_0402_1005Metric', {'1':VB,'2':GND})
    add('C13','Device:C', 285, 75, '2.2u 10V','Capacitor_SMD:C_0603_1608Metric', {'1':V3,'2':GND})
    add('C14','Device:C', 295, 75, '470p','Capacitor_SMD:C_0402_1005Metric', {'1':'BYP','2':GND})
    add('R5','Device:R', 320, 50, '1M 1%','Resistor_SMD:R_0402_1005Metric', {'1':VB,'2':'VBAT_DIV'})
    add('R6','Device:R', 320, 70, '1M 1%','Resistor_SMD:R_0402_1005Metric', {'1':'VBAT_DIV','2':GND})
    add('C15','Device:C', 332, 70, '100n','Capacitor_SMD:C_0402_1005Metric', {'1':'VBAT_DIV','2':GND})
    # ---------------- AFE ----------------
    add('U5','pstat:REF35102QDBVR', 60, 190, 'REF35102QDBVR','Package_TO_SOT_SMD:SOT-23-6', {
        '4':'AFE_PWR', '3':'AFE_PWR', '6':'VREF', '5':'NR', '1':GND, '2':GND})
    add('C16','Device:C', 30, 210, '100n','Capacitor_SMD:C_0402_1005Metric', {'1':'AFE_PWR','2':GND})
    add('C17','Device:C', 40, 210, '1u 10V','Capacitor_SMD:C_0402_1005Metric', {'1':'AFE_PWR','2':GND})
    add('C18','Device:C', 82, 210, '1u low-leak','Capacitor_SMD:C_0603_1608Metric', {'1':'NR','2':GND})
    add('C19','Device:C', 92, 210, '100n','Capacitor_SMD:C_0402_1005Metric', {'1':'VREF','2':GND})
    add('R7','Device:R', 110, 185, '1M 1%','Resistor_SMD:R_0402_1005Metric', {'1':'VREF','2':'VBIAS'})
    add('R8','Device:R', 110, 205, '1M 1%','Resistor_SMD:R_0402_1005Metric', {'1':'VBIAS','2':GND})
    add('C20','Device:C', 122, 205, '100n','Capacitor_SMD:C_0402_1005Metric', {'1':'VBIAS','2':GND})
    add('U4A','pstat:OPA2391xDGK', 150, 235, 'OPA2391','Package_SO:VSSOP-8_3.0x3.0mm_P0.65mm', {
        '1':'CE_AMP', '2':'RE', '3':'VBIAS'}, unit=1)
    add('U4B','pstat:OPA2391xDGK', 150, 265, 'OPA2391','Package_SO:VSSOP-8_3.0x3.0mm_P0.65mm', {
        '5':'VREF', '6':'WE', '7':'TIA_OUT'}, unit=2)
    add('U4C','pstat:OPA2391xDGK', 108, 250, 'OPA2391','Package_SO:VSSOP-8_3.0x3.0mm_P0.65mm', {
        '8':'AFE_PWR', '4':GND}, unit=3)
    add('R9','Device:R', 175, 220, '100R','Resistor_SMD:R_0402_1005Metric', {'1':'CE_AMP','2':'CE'})
    add('R10','Device:R', 130, 218, '1k DNP','Resistor_SMD:R_0402_1005Metric', {'1':'CE_AMP','2':'COMP'}, dnp=True)
    add('C21','Device:C', 118, 218, '1n DNP','Capacitor_SMD:C_0402_1005Metric', {'1':'COMP','2':'RE'}, dnp=True)
    add('R11','Device:R', 175, 250, '1M 0.1%','Resistor_SMD:R_0603_1608Metric', {'1':'WE','2':'TIA_OUT'})
    add('C22','Device:C', 175, 262, '470p C0G','Capacitor_SMD:C_0603_1608Metric', {'1':'WE','2':'TIA_OUT'})
    add('R12','Device:R', 200, 240, '1k','Resistor_SMD:R_0402_1005Metric', {'1':'TIA_OUT','2':'ADC_P'})
    add('C23','Device:C', 212, 248, '10n C0G','Capacitor_SMD:C_0402_1005Metric', {'1':'ADC_P','2':GND})
    add('R13','Device:R', 200, 268, '1k','Resistor_SMD:R_0402_1005Metric', {'1':'VREF','2':'ADC_N'})
    add('C24','Device:C', 212, 276, '10n C0G','Capacitor_SMD:C_0402_1005Metric', {'1':'ADC_N','2':GND})
    add('J1','Connector_Generic:Conn_01x03', 30, 250, 'ELECTRODES','Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical', {
        '1':'CE','2':'RE','3':'WE'})
    add('TP1','Connector:TestPoint', 60, 235, 'TP_VREF','TestPoint:TestPoint_Pad_D1.5mm', {'1':'VREF'})
    add('TP2','Connector:TestPoint', 70, 235, 'TP_TIA','TestPoint:TestPoint_Pad_D1.5mm', {'1':'TIA_OUT'})
    add('TP3','Connector:TestPoint', 80, 235, 'TP_VBIAS','TestPoint:TestPoint_Pad_D1.5mm', {'1':'VBIAS'})
    # ---------------- nRF module ----------------
    mdbt_pins = {'1':GND, '11':V3, '12':GND, '30':GND, '40':GND,
                 '15':'ADC_N', '16':'ADC_P', '17':'VBAT_DIV',
                 '19':'AFE_PWR', '20':'LED_R', '21':'CHG_N',
                 '35':'NRST', '37':'SWDCLK', '38':'SWDIO'}
    for n,_,_ in MDBT_PINS:
        mdbt_pins.setdefault(str(n), None)
    add('U3','pstat:MDBT42Q', 280, 200, 'MDBT42Q-512KV2','pstat:Raytac_MDBT42Q', mdbt_pins)
    add('C25','Device:C', 240, 155, '10u 10V','Capacitor_SMD:C_0603_1608Metric', {'1':V3,'2':GND})
    add('C26','Device:C', 250, 155, '100n','Capacitor_SMD:C_0402_1005Metric', {'1':V3,'2':GND})
    add('R14','Device:R', 330, 165, '1k','Resistor_SMD:R_0402_1005Metric', {'1':'LED_R','2':'LED_A'})
    add('D1','Device:LED', 330, 180, 'GREEN','LED_SMD:LED_0603_1608Metric', {'1':GND,'2':'LED_A'})
    add('J2','Connector_Generic:Conn_01x05', 340, 220, 'SWD','Connector_PinHeader_1.27mm:PinHeader_1x05_P1.27mm_Vertical', {
        '1':V3,'2':'SWDIO','3':'SWDCLK','4':'NRST','5':GND})
    # ---------------- power flags ----------------
    add('#FLG01','power:PWR_FLAG', 385, 40, 'PWR_FLAG','', {'1':GND})
    add('#FLG02','power:PWR_FLAG', 395, 40, 'PWR_FLAG','', {'1':'AFE_PWR_FLAG'})
    add('#FLG03','power:PWR_FLAG', 405, 40, 'PWR_FLAG','', {'1':VB})

layout()

# Net alias: BQ BAT output and battery positive are the same copper
ALIAS = {'AFE_PWR_FLAG': 'AFE_PWR'}

# ------------------------------------------------------------- generation ---
SYMS = build_symbol_library()
PINDB = {lid: parse_pins(blk) for lid, blk in SYMS.items()}

def pin_pos(inst, num):
    lid = inst['libid']; unit = inst['unit']
    db = PINDB[lid]
    key = (unit, num)
    if key not in db:
        key = (0, num)
    if key not in db:
        cands = [k for k in db if k[1] == num]
        if not cands: raise KeyError((lid, unit, num))
        key = cands[0]
    px, py, ang = db[key]
    return inst['X'] + px, inst['Y'] - py, ang

def stub_dir(ang):
    # pin angle points toward body; stub goes opposite, in schematic coords (y down)
    return {0: (-1, 0), 180: (1, 0), 90: (0, 1), 270: (0, -1)}[ang]

body = []
expected = {}   # net -> set of (ref, pin)

def wire(x1, y1, x2, y2):
    body.append(f'  (wire (pts (xy {x1:.2f} {y1:.2f}) (xy {x2:.2f} {y2:.2f})) (stroke (width 0) (type default)) (uuid {U()}))')

def label(net, x, y, ang, justify):
    body.append(f'  (label "{net}" (at {x:.2f} {y:.2f} {ang}) (effects (font (size 1.27 1.27)) (justify {justify} bottom)) (uuid {U()}))')

def no_connect(x, y):
    body.append(f'  (no_connect (at {x:.2f} {y:.2f}) (uuid {U()}))')

POWER_LIBID = {'GND':'power:GND', '+3V3':'power:+3V3', '+BATT':'power:+BATT'}
power_instances = []
pwr_count = {'GND':0, '+3V3':0, '+BATT':0}

ROOT_UUID = U()

def plain_ref(inst):
    return inst['ref'].rstrip('ABC') if inst['libid'] == 'pstat:OPA2391xDGK' else inst['ref']

def emit_instance(inst):
    lid = inst['libid']
    ref = plain_ref(inst)
    fields = [('Reference', ref), ('Value', inst['value'])]
    if inst['fp']:
        fields.append(('Footprint', inst['fp']))
    pins_all = sorted({k[1] for k in PINDB[lid]}, key=lambda s: int(s) if s.isdigit() else 0)
    plist = '\n'.join(f'    (pin "{n}" (uuid {U()}))' for n in pins_all)
    props = '\n'.join(
        f'    (property "{k}" "{v}" (at {inst["X"]:.2f} {inst["Y"]-10+2.2*i:.2f} 0) (effects (font (size 1.27 1.27)){" hide" if k=="Footprint" else ""}))'
        for i, (k, v) in enumerate(fields))
    dnp = ' (dnp yes)' if inst.get('dnp') else ''
    return f'''  (symbol (lib_id "{lid}") (at {inst['X']:.2f} {inst['Y']:.2f} 0) (unit {inst['unit']})
    (in_bom yes) (on_board yes){dnp} (uuid {U()})
{props}
{plist}
    (instances (project "discrete-potentiostat"
      (path "/{ROOT_UUID}" (reference "{ref}") (unit {inst['unit']}))))
  )'''

sym_bodies = []
for inst in INSTS:
    sym_bodies.append(emit_instance(inst))
    for num, net in sorted(inst['pins'].items(), key=lambda kv: int(kv[0])):
        x, y, ang = pin_pos(inst, num)
        if net is None:
            no_connect(x, y)
            continue
        dx, dy = stub_dir(ang)
        real = net[1:] if net.startswith('=') else ALIAS.get(net, net)
        ref = plain_ref(inst)
        if not ref.startswith('#'):
            expected.setdefault(real, set()).add((ref, num))
        if net.startswith('='):
            name = net[1:]
            ex, ey = x + dx*2.54, y + dy*2.54
            wire(x, y, ex, ey)
            pwr_count[name] += 1
            power_instances.append((POWER_LIBID[name], ex, ey, name, f'#PWR{name.replace("+","P").replace("3V3","3V3_")}{pwr_count[name]:02d}'))
        else:
            ex, ey = x + dx*3.81, y + dy*3.81
            wire(x, y, ex, ey)
            if dy != 0:
                ex2 = ex + 3.81
                wire(ex, ey, ex2, ey)
                label(real, ex2, ey, 0, 'left')
            elif dx > 0:
                label(real, ex, ey, 0, 'left')
            else:
                label(real, ex, ey, 180, 'right')

for lid, x, y, name, ref in power_instances:
    sym_bodies.append(f'''  (symbol (lib_id "{lid}") (at {x:.2f} {y:.2f} 0) (unit 1)
    (in_bom yes) (on_board yes) (uuid {U()})
    (property "Reference" "{ref}" (at {x:.2f} {y+5:.2f} 0) (effects (font (size 1.27 1.27)) hide))
    (property "Value" "{name}" (at {x:.2f} {y+3.5:.2f} 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "" (at {x:.2f} {y:.2f} 0) (effects (font (size 1.27 1.27)) hide))
    (pin "1" (uuid {U()}))
    (instances (project "discrete-potentiostat"
      (path "/{ROOT_UUID}" (reference "{ref}") (unit 1))))
  )''')

NOTES = r'''DISCRETE POTENTIOSTAT - wireless-charged 3-electrode chronoamperometry
E_cell = WE - RE = 1.024V - 0.512V = +0.512V fixed.  I_WE = (V(ADC_P)-V(ADC_N))/RF

DESIGN NOTES / VERIFY BEFORE LAYOUT
1. CHARGING: two-stage per TI guidance for small cells. BQ51013B = Qi receiver with
   regulated 5V (V5OUT), ILIM: RILIM = 250/IMAX = R1+R2 = 2.52k -> IMAX 100mA (HW limit 120mA).
   MCP73832-2 (4.2V) charges the LIR2032: ICHG = 1000/RPROG = 1000/49.9k = 20mA (0.5C),
   termination ~7.5% = 1.5mA, open-drain STAT -> CHG_N (100k pullup to 3V3).
   This replaces the earlier BQ51050B direct-charge (TI: not recommended <200mA).
2. RESONANT TANK: L1 26uH (Wurth 760308101214, dia 19mm = battery-bounded device size).
   C1 = 1/(L*(2pi*100kHz)^2) = 97n -> 100n C0G. C2 -> fd = 1MHz -> 1n C0G.
   Retune both whenever the coil changes. Small coil = tighter TX alignment; fine at our 0.13W.
3. MDBT42Q pads 22-36 are a sequential reconstruction (P0.09/NFC1 ... P0.23); pads 1-21 and
   37-41 verified against the datasheet. Every pad actually used in this design is a verified
   one. Cross-check the full pad table against the Raytac datasheet before PCB layout.
4. TS/CTRL: 10k NTC against the LIR2032 holder cuts wireless power if the cell heats
   (plain 10k = sense defeated, still a valid safe strap). EN1=EN2=GND: wireless enabled.
5. AFE_PWR is driven by nRF GPIO P0.06 set to high-drive. Load = REF35+OPA2391 ~ 60uA.
   Power the AFE off between runs to avoid electrode polarization.
6. WE net: guard ring driven from VREF, no soldermask opening at TIA input, clean flux.
7. RF/CF options: 1M/470p (default, +2.1uA/-0.9uA FS), 5.1M/100p (427/181nA), 10M/47p (217/92nA).
   rf_ohm + oversample are runtime settings in firmware; recalibrate after a swap.
8. R10/C21 control-amp compensation are DNP; fit only if the CE-RE loop oscillates.
9. LIR2032 has no protection circuit: firmware must System-OFF below 3.0V (VBAT_DIV, AIN2).
10. SAADC: differential P=AIN1(ADC_P) N=AIN0(ADC_N), gain 1/2..4, oversample 64x default.'''

def text_block(s, x, y, size=1.6, bold=False):
    esc = s.replace('"', "'").replace('\\', '\\\\').replace('\n', '\\n')
    b = ' bold' if bold else ''
    return f'  (text "{esc}" (at {x} {y} 0) (effects (font (size {size} {size}){b}) (justify left bottom)) (uuid {U()}))'

texts = [text_block(NOTES, 362, 200, 1.7)]
for s, x, y in [
    ('QI RECEIVER 5V (BQ51013B) + 20mA CHARGER (MCP73832)', 25, 35),
    ('BATTERY + 3.3V LDO + VBAT MONITOR', 225, 35),
    ('ANALOG FRONT-END: 2-OP-AMP POTENTIOSTAT (+0.512V)', 25, 165),
    ('nRF52832 MODULE (MDBT42Q) + SWD + LED', 225, 145),
]:
    texts.append(text_block(s, x, y, 2.5, bold=True))

lib_symbols = '\n'.join('    ' + SYMS[k] for k in sorted(SYMS))

sch = f'''(kicad_sch (version 20230121) (generator eeschema)
  (uuid {ROOT_UUID})
  (paper "A2")
  (title_block
    (title "Discrete Potentiostat - wireless 3-electrode chronoamperometry")
    (date "2026-07-10")
    (rev "A")
    (company "discrete-potentiostat")
    (comment 1 "OPA2391 + REF35102 + nRF52832(MDBT42Q) + MIC5205 + BQ51013B + MCP73832 + LIR2032")
    (comment 2 "E = +0.512V fixed; I range set by RF (default 1M)")
  )
  (lib_symbols
{lib_symbols}
  )
{chr(10).join(body)}
{chr(10).join(texts)}
{chr(10).join(sym_bodies)}
  (sheet_instances (path "/" (page "1")))
)
'''
os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, 'discrete-potentiostat.kicad_sch'), 'w').write(sch)
json.dump({k: sorted(list(map(list, v))) for k, v in expected.items()},
          open(os.path.join(GEN, 'expected_nets.json'), 'w'), indent=1)
print('wrote schematic:', len(INSTS), 'instances,', len(expected), 'nets,',
      len(power_instances), 'power symbols')
