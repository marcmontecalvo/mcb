"""Event timeline (requests, routes, steps, prefetches) and positions per tick.

Usage: uv run --locked python scripts/analysis/timeline.py <events.jsonl> <out.txt> <positions.pkl>
"""
import re,json,sys
p=sys.argv[1]
out=open(sys.argv[2],'w',encoding='utf-8')
rx=re.compile(r'"type":\s*"([a-z_]+)"')
SKIP_OPS={'heartbeat'}
tick=None; pos_by_tick={}
with open(p,encoding='utf-8',errors='replace') as f:
    for line in f:
        m=rx.search(line)
        if not m: continue
        t=m.group(1)
        if t not in ('request','policy_observation','navigation_tick','server_event','native_route_finished','terrain_step_finished','terrain_step_planned','native_step_confirmed','terrain_route_planned','route_prefetch_started','route_prefetch_adopted'): continue
        d=json.loads(line); pl=d['payload']
        if t=='policy_observation':
            tick=pl.get('tick'); b=pl.get('body') or {}
            pos=b.get('position')
            pos_by_tick[tick]=(pos, pl.get('step_follower',{}).get('state'), pl.get('route_follower',{}).get('state'), pl.get('state'), pl.get('episode',{}).get('status'))
            continue
        if t=='navigation_tick':
            tick=pl.get('tick'); out.write(f"T{tick} NAVMODE {pl.get('mode')}\n"); continue
        if t=='server_event':
            if pl.get('tick') is not None: tick=pl.get('tick')
            ty=pl.get('type'); ph=pl.get('phase')
            if ty in ('policy_sample','received','route_progress') and ty!='route_progress': 
                if ty=='received' and pl.get('data',{}).get('op')!='heartbeat':
                    out.write(f"T{tick} SE recv op={pl.get('data',{}).get('op')} seq={pl.get('data',{}).get('request_sequence')}\n")
                continue
            if ty=='policy_sample': continue
            out.write(f"T{tick} SE {ph}/{ty} {json.dumps(pl.get('data'))[:260]}\n"); continue
        if t=='request':
            op=pl.get('op')
            if op in SKIP_OPS: continue
            if op=='follow_route':
                r=pl.get('route'); out.write(f"T{tick} REQ follow_route id={pl.get('route_id')} len={len(r)} first={r[0]} last={r[-1]}\n")
            elif op=='follow_step':
                pp=pl.get('plan',{}); out.write(f"T{tick} REQ follow_step id={pl.get('step_id')} plan={json.dumps(pp)[:260]}\n")
            else:
                out.write(f"T{tick} REQ {op} {json.dumps({k:v for k,v in pl.items() if k not in ('schema_version','run_id','episode_id','body_id')})[:200]}\n")
            continue
        if t=='native_route_finished':
            out.write(f"T{tick} ROUTE_DONE {pl.get('route_id')} state={pl.get('state')} reason={pl.get('reason')} len={pl.get('length')} next={pl.get('next_index')} motor={pl.get('motor_ticks')}\n"); continue
        if t=='terrain_step_finished':
            out.write(f"T{tick} STEP_FIN state={pl.get('state')} reason={pl.get('reason')} target={pl.get('target')} br={pl.get('breaks_confirmed')} pl={pl.get('places_confirmed')}\n"); continue
        if t=='terrain_step_planned':
            out.write(f"T{tick} STEP_PLAN {pl.get('source')}->{pl.get('target')} br={len(pl.get('breaks',[]))} pl={len(pl.get('places',[]))} kind={pl.get('kind')}\n"); continue
        if t=='native_step_confirmed':
            out.write(f"T{tick} STEP_CONF {pl.get('step_id')} state={pl.get('state')} reason={pl.get('reason')} finished_tick={pl.get('finished_tick')} broke={len(pl.get('broken_positions',[]))} placed={len(pl.get('placed_positions',[]))}\n"); continue
        if t=='terrain_route_planned':
            out.write(f"T{tick} ROUTE_PLAN status={pl.get('status')} steps={len(pl.get('steps',[]))}\n"); continue
        if t in ('route_prefetch_started','route_prefetch_adopted'):
            out.write(f"T{tick} {t} {json.dumps(pl)[:160]}\n"); continue
out.close()
import pickle
pickle.dump(pos_by_tick,open(sys.argv[3],'wb'))
print(len(pos_by_tick), min(pos_by_tick), max(pos_by_tick))
