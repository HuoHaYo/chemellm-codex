"""Native flowsheet layout requests and deterministic, topology based placement.

Coordinates are DWSIM canvas units. This module never changes a process graph.
"""
from __future__ import annotations

from collections import defaultdict, deque
import math
from typing import Literal

from pydantic import Field, model_validator
from app.schemas import Inputs


class LayoutNode(Inputs):
    object: str = Field(min_length=1, max_length=200, description='原生 name 或唯一 tag')
    x: float | None = Field(default=None, ge=-100000, le=100000)
    y: float | None = Field(default=None, ge=-100000, le=100000)
    width: int | None = Field(default=None, ge=10, le=2000)
    height: int | None = Field(default=None, ge=10, le=2000)
    rotation: Literal[0, 90, 180, 270] | None = None
    flipped_h: bool | None = None
    flipped_v: bool | None = None

    @model_validator(mode='after')
    def position(self):
        if (self.x is None) != (self.y is None):
            raise ValueError('布局位置须同时提供 x 和 y')
        return self


class LayoutGroup(Inputs):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,49}$')
    title: str = Field(min_length=1, max_length=120)
    members: list[str] = Field(min_length=1, max_length=1000)
    x: float | None = Field(default=None, ge=-100000, le=100000)
    y: float | None = Field(default=None, ge=-100000, le=100000)

    @model_validator(mode='after')
    def position(self):
        if (self.x is None) != (self.y is None):
            raise ValueError('分区位置须同时提供 x 和 y')
        if len(set(self.members)) != len(self.members):
            raise ValueError('分区成员重复')
        return self


class LayoutOptions(Inputs):
    mode: Literal['auto', 'manual'] = 'auto'
    groups: list[LayoutGroup] = Field(default_factory=list, max_length=100)
    nodes: list[LayoutNode] = Field(default_factory=list, max_length=1000)
    pinned: list[str] | None = Field(default=None, max_length=1000,
        description='省略时沿用保存的固定对象；显式 [] 清除固定约束')
    grid: int = Field(default=20, ge=1, le=100)
    layer_gap: int = Field(default=220, ge=80, le=2000)
    node_gap: int = Field(default=100, ge=40, le=1000)
    group_gap: int = Field(default=240, ge=100, le=2000)

    @model_validator(mode='after')
    def unique(self):
        for values in ([g.id for g in self.groups], [n.object for n in self.nodes], self.pinned or []):
            if len(set(values)) != len(values):
                raise ValueError('布局分区、修改对象或固定对象重复')
        return self


def _resolve(nodes, ref):
    matches = [n['name'] for n in nodes if ref in (n['name'], n['tag'])]
    if len(matches) != 1:
        raise ValueError(('布局对象不存在：' if not matches else '布局对象引用有歧义：') + ref)
    return matches[0]


def _layers(ids, edges):
    """Tarjan SCCs, then stable feedback-edge removal inside each SCC."""
    adj = {k: [] for k in sorted(ids)}
    for a, b in sorted(set(edges)):
        if a in adj and b in adj:
            adj[a].append(b)
    stack, active, index, low, components = [], set(), {}, {}, []

    def visit(a):
        index[a] = low[a] = len(index)
        stack.append(a); active.add(a)
        for b in adj[a]:
            if b not in index:
                visit(b); low[a] = min(low[a], low[b])
            elif b in active:
                low[a] = min(low[a], index[b])
        if low[a] == index[a]:
            group = []
            while True:
                b = stack.pop(); active.remove(b); group.append(b)
                if b == a:
                    break
            components.append(sorted(group))

    # Native models accepted by this API are bounded; avoid recursion at graph scale.
    import sys
    previous = sys.getrecursionlimit()
    try:
        if len(ids) * 3 + 100 > previous:
            sys.setrecursionlimit(len(ids) * 3 + 100)
        for a in adj:
            if a not in index:
                visit(a)
    finally:
        sys.setrecursionlimit(previous)
    comp = {a: i for i, group in enumerate(components) for a in group}
    forward = [(a, b) for a in adj for b in adj[a] if comp[a] != comp[b] or a < b]
    incoming = {a: 0 for a in ids}; succ = defaultdict(list)
    for a, b in forward:
        incoming[b] += 1; succ[a].append(b)
    queue = deque(sorted(a for a in ids if incoming[a] == 0)); ranks = dict.fromkeys(ids, 0)
    while queue:
        a = queue.popleft()
        for b in sorted(succ[a]):
            ranks[b] = max(ranks[b], ranks[a] + 1)
            incoming[b] -= 1
            if incoming[b] == 0:
                queue.append(b)
    layers = defaultdict(list)
    for a in sorted(ids):
        layers[ranks[a]].append(a)
    pred = defaultdict(list)
    for a, b in forward:
        pred[b].append(a)
    # Alternating barycentric sweeps reduce branch/merge crossings deterministically.
    for sweep in range(8):
        pos = {a: i for row in layers.values() for i, a in enumerate(row)}
        for rank in sorted(layers, reverse=bool(sweep % 2)):
            neighbors = succ if sweep % 2 else pred
            layers[rank].sort(key=lambda a: (sum(pos[b] for b in neighbors[a]) / len(neighbors[a])
                                            if neighbors[a] else pos[a], a))
    return layers


def footprint(n):
    """Native-measured label and symbol bounds relative to the symbol origin."""
    w, h = n['width'], n['height']
    label = n.get('label_bounds')
    if n.get('rotation',0) in (90,270):
        body=((w-h)/2,(h-w)/2,(w+h)/2,(h+w)/2)
    else:
        body=(0,0,w,h)
    if label:
        return min(body[0], label[0]), min(body[1], label[1]), max(body[2], label[2]), max(body[3], label[3])
    tw = n.get('label_width', max(30, len(n['tag']) * 8))
    return min(body[0], (w-tw)/2), min(0,body[1]), max(body[2], (w+tw)/2), max(h+24,body[3])


def compile_layout(graph, options: LayoutOptions):
    nodes = [dict(n) for n in graph['nodes'] if n.get('simulation')]
    by_id = {n['name']: n for n in nodes}
    if len(by_id) > 1000:
        raise ValueError('布局最多支持1000个仿真对象')
    resolve = lambda ref: _resolve(nodes, ref)
    overrides = {}
    for edit in options.nodes:
        key = resolve(edit.object)
        if key in overrides:
            raise ValueError('同一布局对象通过不同引用重复修改：'+edit.object)
        overrides[key] = edit.model_dump(exclude_none=True, exclude={'object'})
    pin_refs=options.pinned if options.pinned is not None else graph.get('metadata',{}).get('pinned',[])
    pinned = {resolve(ref) for ref in pin_refs}
    if len(pinned) != len(pin_refs):
        raise ValueError('同一对象重复固定')
    pinned.update(k for k, v in overrides.items() if 'x' in v)
    for key, edit in overrides.items():
        by_id[key].update({k: v for k, v in edit.items() if k not in {'x', 'y'}})
    edges = [(e['source_id'], e['target_id']) for e in graph['edges']]
    upstream, downstream = defaultdict(list), defaultdict(list)
    for a, b in edges:
        upstream[b].append(a); downstream[a].append(b)
    # Reuse embedded groups unless the caller explicitly replaces them.
    group_specs = [g.model_dump() for g in options.groups]
    if not group_specs:
        for g in graph.get('metadata', {}).get('groups', []):
            members = [m for m in g['members'] if m in by_id]
            if members:
                group_specs.append(dict(id=g['id'], title=g['title'], members=members, x=None, y=None))
    membership, groups = {}, []
    for g in group_specs:
        members = [resolve(m) for m in g['members']]
        if len(set(members)) != len(members):
            raise ValueError('分区包含同一对象的重复引用：'+g['id'])
        for a in members:
            if a in membership:
                raise ValueError('对象属于多个分区：'+by_id[a]['tag'])
            membership[a] = g['id']
        groups.append({**g, 'members': members})
    streams = {k for k, n in by_id.items() if n['type'] in {'MaterialStream', 'EnergyStream'}}
    # Assign connecting streams to the upstream unit; feeds to their downstream unit.
    for a in sorted(streams):
        if a not in membership:
            owners = [b for b in upstream[a]+downstream[a] if b not in streams and b in membership]
            if owners:
                membership[a] = membership[owners[0]]
    remaining = set(by_id)-membership.keys()
    if remaining:
        rest_edges = [(a, b) for a, b in edges if a in remaining and b in remaining]
        adj = defaultdict(set)
        for a, b in rest_edges:
            adj[a].add(b); adj[b].add(a)
        while remaining:
            todo = [min(remaining)]; members = []
            while todo:
                a = todo.pop()
                if a not in remaining:
                    continue
                remaining.remove(a); members.append(a)
                todo.extend(sorted(adj[a] & remaining, reverse=True))
            gid = 'auto_'+str(len(groups)+1)
            while any(g['id'] == gid for g in groups):
                gid += '_'
            groups.append(dict(id=gid, title='流程分区 '+str(len(groups)+1), members=members, x=None, y=None))
            membership.update({a: gid for a in members})
    for g in groups:
        g['members'] = sorted(a for a in by_id if membership[a] == g['id'])
    snap = lambda v: math.ceil(v/options.grid)*options.grid
    cursor_y = 100
    for g in groups:
        ids = set(g['members']); units = ids-streams
        if options.mode == 'manual' and g.get('x') is not None:
            dx=g['x']-min(by_id[a]['x'] for a in ids)
            dy=g['y']-min(by_id[a]['y'] for a in ids)
            for a in ids-pinned:
                by_id[a]['x']+=dx;by_id[a]['y']+=dy
        if options.mode == 'auto':
            unit_edges = [(a, b) for a, b in edges if a in units and b in units]
            for s in streams:
                unit_edges.extend((a, b) for a in upstream[s] for b in downstream[s] if a in units and b in units)
            layers = _layers(units or ids, unit_edges if units else [(a, b) for a, b in edges if a in ids and b in ids])
            ox = g['x'] if g.get('x') is not None else 160
            oy = g['y'] if g.get('y') is not None else cursor_y+80
            x = ox+options.layer_gap
            for rank in sorted(layers):
                y = oy
                row = layers[rank]
                max_width = max((footprint(by_id[a])[2]-footprint(by_id[a])[0] for a in row), default=50)
                for a in row:
                    n = by_id[a]
                    if a not in pinned:
                        n.update(x=snap(x), y=snap(y))
                    y += max(n['height']+options.node_gap+160, footprint(n)[3]+options.node_gap+160)
                x += max(options.layer_gap*2, max_width+options.layer_gap)
            # Place each material/energy stream near its owning unit's actual port.
            for a in sorted(ids & streams):
                if a in pinned or not units:
                    continue
                n = by_id[a]
                sources = [b for b in upstream[a] if b in by_id and b not in streams]
                targets = [b for b in downstream[a] if b in by_id and b not in streams]
                owner = sources[0] if sources else (targets[0] if targets else None)
                if owner is None:
                    n.update(x=snap(ox), y=snap(oy)); continue
                u = by_id[owner]
                outgoing = bool(sources)
                siblings = sorted(b for b in (downstream[owner] if outgoing else upstream[owner]) if b in streams and b in ids)
                i = siblings.index(a) if a in siblings else 0
                if n['type'] == 'EnergyStream':
                    n.update(x=snap(u['x']-100-i*100), y=snap(u['y']+170))
                else:
                    # Port ordering matches native top/bottom outlet ordering.
                    related = [e for e in graph['edges'] if (e['source_id']==owner and e['target_id']==a) or (e['source_id']==a and e['target_id']==owner)]
                    port = related[0]['source_port' if outgoing else 'target_port'] if related else i
                    dy = 0 if len(siblings)==1 else (port-(len(siblings)-1)/2)*110
                    n.update(x=snap(u['x']+(160 if outgoing else -180)), y=snap(u['y']+dy))
            # Long transfers leave through separate lanes above the unit train.
            # Native routing then avoids carrying branch products through later units.
            transfers=sorted((a for a in ids & streams if by_id[a]['type']=='MaterialStream'
                              and any(b in by_id and membership.get(b)!=g['id'] for b in downstream[a])),
                             key=lambda a:(by_id[a]['x'],by_id[a]['tag']))
            for i,a in enumerate(transfers):
                if a not in pinned:
                    by_id[a]['y']=snap(oy-130-i*80)
        for a in ids:
            by_id[a].update(overrides.get(a, {}))
        # Resolve symbol+label collisions without moving any explicitly fixed node.
        if options.mode == 'auto':
            placed = []
            for a in sorted(ids, key=lambda a: (a not in pinned, by_id[a]['x'], by_id[a]['y'], a)):
                n = by_id[a]
                if a not in pinned:
                    for _ in range(1000):
                        rect = bounds(n)
                        hits = [b for b in placed if intersects(rect, bounds(by_id[b]), 14)]
                        if not hits:
                            break
                        n['y'] = snap(max(bounds(by_id[b])[3] for b in hits)+40-footprint(n)[1])
                placed.append(a)
        rects = [bounds(by_id[a]) for a in ids]
        if rects:
            left=min(r[0] for r in rects)-60; top=min(r[1] for r in rects)-70
            right=max(r[2] for r in rects)+60; bottom=max(r[3] for r in rects)+60
            g['bounds']=[left, top, right, bottom]
            cursor_y=max(cursor_y, bottom+options.group_gap)
    if options.mode == 'auto':
        group_edges=[(membership[a],membership[b]) for a,b in edges
                     if a in membership and b in membership and membership[a]!=membership[b]]
        rows=_layers({g['id'] for g in groups},group_edges)
        by_group={g['id']:g for g in groups};gx=100
        for rank in sorted(rows):
            gy=100;max_width=0
            for gid in rows[rank]:
                g=by_group[gid];l,t,r,b=g['bounds'];max_width=max(max_width,r-l)
                if g.get('x') is None and not (set(g['members'])&pinned):
                    dx,dy=snap(gx-l),snap(gy-t)
                    for a in g['members']:
                        by_id[a]['x']+=dx;by_id[a]['y']+=dy
                    g['bounds']=[l+dx,t+dy,r+dx,b+dy]
                gy+=b-t+options.group_gap
            gx+=max_width+options.group_gap
    return {'nodes': [{k: n[k] for k in ('name','x','y','width','height','rotation','flipped_h','flipped_v') if k in n}
                      for n in sorted(nodes, key=lambda n:n['name'])],
            'groups': groups, 'pinned': sorted(pinned), 'version': 1}


def bounds(n):
    l, t, r, b = footprint(n)
    return n['x']+l, n['y']+t, n['x']+r, n['y']+b


def intersects(a, b, margin=0):
    return a[0] < b[2]+margin and a[2]+margin > b[0] and a[1] < b[3]+margin and a[3]+margin > b[1]


def quality(graph):
    nodes = [n for n in graph['nodes'] if n.get('simulation')]
    overlaps = [{'a': a['tag'], 'b': b['tag']} for i, a in enumerate(nodes) for b in nodes[i+1:]
                if intersects(bounds(a), bounds(b))]
    crossings, through, long_edges = [], [], []
    segments=[]
    for ei, edge in enumerate(graph['edges']):
        pts=edge.get('points', [])
        length=sum(math.dist(a,b) for a,b in zip(pts,pts[1:]))
        if length>1500:
            long_edges.append(dict(source=edge['source'],target=edge['target'],length=round(length,1)))
        for a,b in zip(pts,pts[1:]):
            if math.dist(a,b)<1e-6:
                continue
            segments.append((ei,a,b))
        for n in nodes:
            if n['name'] in (edge['source_id'],edge['target_id']):
                continue
            rect=(n['x']+1,n['y']+1,n['x']+n['width']-1,n['y']+n['height']-1)
            if any(_segment_rect(a,b,rect) for a,b in zip(pts,pts[1:])):
                through.append(dict(source=edge['source'],target=edge['target'],object=n['tag']))
    seen=set()
    for i,(ei,a,b) in enumerate(segments):
        for ej,c,d in segments[i+1:]:
            if ei==ej or (ei,ej) in seen or (ej,ei) in seen:
                continue
            e,f=graph['edges'][ei],graph['edges'][ej]
            if {e['source_id'],e['target_id']}&{f['source_id'],f['target_id']}:
                continue
            if _cross(a,b,c,d):
                seen.add((ei,ej));crossings.append({'a':[e['source'],e['target']],'b':[f['source'],f['target']]})
    return dict(overlaps=overlaps,overlap_count=len(overlaps),crossings=crossings,crossing_count=len(crossings),
                lines_through_equipment=through,through_count=len(through),long_connections=long_edges,
                passed=not overlaps, note='图形可读性检查；不是工艺求解或守恒验收')


def _cross(a,b,c,d):
    def orient(p,q,r):return (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
    return orient(a,b,c)*orient(a,b,d)<-1e-7 and orient(c,d,a)*orient(c,d,b)<-1e-7


def _segment_rect(a,b,r):
    # Liang-Barsky clipping includes a segment entirely inside the open rectangle.
    t0,t1=0.,1.
    for p,q in ((a[0]-b[0],a[0]-r[0]),(b[0]-a[0],r[2]-a[0]),
                (a[1]-b[1],a[1]-r[1]),(b[1]-a[1],r[3]-a[1])):
        if abs(p)<1e-12:
            if q<0:return False
        elif p<0:t0=max(t0,q/p)
        else:t1=min(t1,q/p)
    return t0<=t1 and t1>=0 and t0<=1
