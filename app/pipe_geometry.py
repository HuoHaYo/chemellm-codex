"""Dependency-free swept presentation pipes; lengths use caller coordinates.

This geometry is not a pipe fitting catalogue or a mechanical design check.
The same frozen source runs in the kernel and Blender's bundled interpreter.
"""
import math


def add(a, b): return tuple(x+y for x, y in zip(a, b))
def mul(a, k): return tuple(x*k for x in a)
def sub(a, b): return add(a, mul(b, -1))
def dot(a, b): return sum(x*y for x, y in zip(a, b))
def cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def norm(a): return math.sqrt(dot(a, a))
def unit(a): return mul(a, 1/norm(a))


def rotate(v, axis, angle):
    c, s = math.cos(angle), math.sin(angle)
    return add(add(mul(v, c), mul(cross(axis, v), s)), mul(axis, dot(axis, v)*(1-c)))


def pipe_path(points, diameter, bend_radius=None):
    """Return sampled (point,tangent) pairs and explicit presentation choices."""
    points = [tuple(p) for p in points]
    radius = bend_radius if bend_radius is not None else 1.5*diameter
    if not math.isfinite(diameter) or diameter <= 0 or not math.isfinite(radius) or radius <= diameter/2:
        raise ValueError('管道弯曲中心线半径必须大于外半径，且数值有限')
    if len(points) < 2 or any(not math.isfinite(v) for p in points for v in p):
        raise ValueError('管道路径至少两个有限三维点')
    lengths = [norm(sub(b, a)) for a, b in zip(points, points[1:])]
    tol = max(diameter, max(lengths))*1e-9
    if min(lengths) <= tol:
        raise ValueError('管道路径存在零长或近零长段')
    directions = [unit(sub(b, a)) for a, b in zip(points, points[1:])]
    bends, trims = {}, [0.0]*len(points)
    for i in range(1, len(points)-1):
        u, v = directions[i-1:i+1]
        cosine = max(-1., min(1., dot(u, v)))
        if cosine < -1+1e-8:
            raise ValueError('管道路径不能直接折返；请提供实际回弯路径')
        if cosine > 1-1e-10:
            continue
        angle = math.acos(cosine)
        trims[i] = radius*math.tan(angle/2)
        bends[i] = (angle, unit(cross(u, v)))
    for i, length in enumerate(lengths):
        if trims[i]+trims[i+1] >= length-tol:
            raise ValueError(f'管道第 {i+1} 段不足以容纳相邻弯头；增大路径间距或明确减小 bend_radius')
    samples = [(points[0], directions[0])]
    for i in range(1, len(points)-1):
        if i not in bends:
            continue
        angle, axis = bends[i]
        u, v = directions[i-1:i+1]
        start = sub(points[i], mul(u, trims[i]))
        center = add(start, mul(cross(axis, u), radius))
        samples.append((start, u))
        steps = max(2, math.ceil(angle/math.radians(7.5)))
        for j in range(1, steps+1):
            theta = angle*j/steps
            p = add(center, rotate(sub(start, center), axis, theta))
            if j == steps: p = add(points[i], mul(v, trims[i]))
            samples.append((p, rotate(u, axis, theta)))
    samples.append((points[-1], directions[-1]))
    return samples, {'bend_count': len(bends), 'bend_radius': radius,
                     'radius_source': 'explicit' if bend_radius is not None else 'presentation_default_1.5D',
                     'radius_is_design_verified': False}


def pipe_mesh(points, diameter, bend_radius=None, sides=32):
    samples, report = pipe_path(points, diameter, bend_radius)
    vertices, faces = [], []
    tangent = samples[0][1]
    normal = unit(cross(tangent, min(((1,0,0),(0,1,0),(0,0,1)), key=lambda a: abs(dot(tangent,a)))))
    previous = tangent
    for point, tangent in samples:
        axis = cross(previous, tangent)
        if norm(axis) > 1e-12:
            normal = rotate(normal, unit(axis), math.acos(max(-1., min(1., dot(previous, tangent)))))
        normal = unit(sub(normal, mul(tangent, dot(normal, tangent))))
        binormal = cross(tangent, normal)
        for j in range(sides):
            theta = 2*math.pi*j/sides
            vertices.append(add(point, mul(add(mul(normal, math.cos(theta)), mul(binormal, math.sin(theta))), diameter/2)))
        previous = tangent
    for i in range(len(samples)-1):
        for j in range(sides):
            k = (j+1)%sides
            faces.append((i*sides+j, i*sides+k, (i+1)*sides+k, (i+1)*sides+j))
    faces.extend((tuple(reversed(range(sides))), tuple((len(samples)-1)*sides+j for j in range(sides))))
    return vertices, faces, {**report, 'rings': len(samples), 'endpoints': [points[0], points[-1]]}
