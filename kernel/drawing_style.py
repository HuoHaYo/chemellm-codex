"""Shared output styles; local drawing conventions, not a standards certification."""


def configure_dxf(doc):
    for name, pattern in (('CHEM_DASH', [5., 3., -2.]),
                          ('CHEM_DASHDOT', [9., 5., -1.5, 1., -1.5])):
        if name not in doc.linetypes:
            doc.linetypes.add(name, pattern=pattern, description=name)
    for layer, style in (('SIGNAL', 'CHEM_DASH'), ('INTERLOCK', 'CHEM_DASHDOT')):
        doc.layers.get(layer).dxf.linetype = style
    doc.header['$LWDISPLAY'] = True


def line_attributes(layer, width, dash):
    # DXF stores lineweight in 1/100 mm using a discrete accepted set.
    weights = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211)
    return {'layer': layer, 'linetype': {'dash': 'CHEM_DASH', 'dashdot': 'CHEM_DASHDOT'}.get(dash, 'CONTINUOUS'),
            'lineweight': min(weights, key=lambda n: abs(n-width*100))}


def process_width(edge):
    role = edge.get('line_role', 'main')
    if role not in {'main', 'secondary'}:
        raise ValueError('line_role 必须为 main 或 secondary')
    return .8 if role == 'main' else .4


def equipment_strokes(primitives):
    """0.25 mm equipment outline, following the published competition rubric."""
    index = {'line': 6, 'rect': 7, 'circle': 6, 'ellipse': 7, 'polygon': 4}
    for primitive in primitives:
        values = list(primitive)
        if primitive[0] in index:
            values[index[primitive[0]]] = .25
        yield tuple(values)
