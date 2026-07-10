import json

def tokenize(s):
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c in '()':
            out.append(c); i += 1
        elif c == '"':
            j = i+1
            while s[j] != '"' or s[j-1] == '\\': j += 1
            out.append(('STR', s[i+1:j])); i = j+1
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
    first = next(it)
    assert first == '('
    return walk()

tree = parse(tokenize(open('out.net').read()))
nets_sec = [x for x in tree if isinstance(x, list) and x and x[0] == 'nets'][0]
nets = {}
for net in nets_sec[1:]:
    name = None; nodes = set()
    for item in net[1:]:
        if item[0] == 'name': name = item[1].lstrip('/')
        if item[0] == 'node':
            d = {k[0]: k[1] for k in item[1:] if isinstance(k, list)}
            nodes.add((d['ref'], d['pin']))
    nets[name] = nodes

exp = {k: set(map(tuple, v)) for k, v in json.load(open('expected_nets.json')).items()}
ok = True
for name, nodes in sorted(exp.items()):
    got = nets.get(name)
    if got is None:
        cand = [n for n, g in nets.items() if nodes & g]
        print(f'MISSING net {name}; overlapping actual nets: {cand}'); ok = False
        continue
    if got != nodes:
        print(f'MISMATCH {name}:')
        if nodes - got: print('   expected-but-absent:', sorted(nodes - got))
        if got - nodes: print('   present-but-unexpected:', sorted(got - nodes))
        ok = False
for n in sorted(x for x in set(nets) - set(exp) if not x.startswith('unconnected-')):
    print('EXTRA net', n, sorted(nets[n])); ok = False
print('nets compared:', len(exp), '/ actual:', len(nets))
print('RESULT:', 'PASS' if ok else 'FAIL')
