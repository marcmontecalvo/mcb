import json, sqlite3, pathlib, collections

p = pathlib.Path('D:/mcb/results/companion/20261110T170929838024Z')
d = json.load(open(p / 'checkpoints/final.json', encoding='utf-8'))
inv = d['inventory']
for k in ('minecraft:scaffolding', 'minecraft:dirt', 'minecraft:cobblestone',
          'minecraft:lantern', 'minecraft:campfire', 'minecraft:glass_pane'):
    print(f'inv {k}: {inv.get(k)}')
print('total items:', sum(inv.values()))

con = sqlite3.connect(p / 'map.sqlite3')
print('\ntables:', [r[0] for r in con.execute(
    "select name from sqlite_master where type='table'")])
for t in [r[0] for r in con.execute("select name from sqlite_master where type='table'")]:
    cols = [r[1] for r in con.execute(f'pragma table_info({t})')]
    n = con.execute(f'select count(*) from {t}').fetchone()[0]
    print(f'  {t} cols={cols} rows={n}')
