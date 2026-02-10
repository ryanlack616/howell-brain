"""
Knowledge Graph Explorer — Taichi GPU Edition
================================================
GPU-accelerated force-directed graph with real-time 3D rendering.
All force calculations run as Taichi kernels on the GPU.

Interaction:
  LMB click    — fire neural activation from a node
  LMB drag     — grab & drag nodes (edges follow)
  Controls panel (top-left) for camera, graph settings

Dragged nodes become pinned. Use "Unpin All" to release them.
After idle, the graph starts "dreaming" — random neurons fire softly.
"""
import json, math, os, sys, time, random

import taichi as ti
import numpy as np

# ── Init Taichi ───────────────────────────────────────
ti.init(arch=ti.gpu, default_fp=ti.f32)

# ── Load Knowledge Graph ──────────────────────────────
KG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge.json")
with open(KG_PATH, encoding="utf-8") as f:
    kg = json.load(f)

ent_dict = kg.get("entities", {})
rel_list = kg.get("relations", [])

# Build node/edge arrays
node_names = list(ent_dict.keys())
name_to_idx = {n: i for i, n in enumerate(node_names)}
N = len(node_names)

# Node metadata
node_types = []
node_obs_counts = []
for name in node_names:
    e = ent_dict[name]
    node_types.append(e.get("entity_type", "Unknown"))
    node_obs_counts.append(len(e.get("observations", [])))

# Edges
edges_src = []
edges_dst = []
edge_types = []
for r in rel_list:
    src = r.get("from_entity", "")
    dst = r.get("to_entity", "")
    if src in name_to_idx and dst in name_to_idx:
        edges_src.append(name_to_idx[src])
        edges_dst.append(name_to_idx[dst])
        edge_types.append(r.get("relation_type", ""))
M = len(edges_src)

print(f"[KG] {N} nodes, {M} edges")

# ── Semantic Color Maps ──────────────────────────────
NODE_COLORS = {
    'AI_Identity':    (0.96, 0.62, 0.04),
    'Human':          (0.98, 0.45, 0.09),
    'Artist':         (0.98, 0.57, 0.24),
    'Instance':       (0.98, 0.75, 0.14),
    'Project':        (0.39, 0.40, 0.95),
    'Poem':           (0.51, 0.55, 0.97),
    'Tool':           (0.06, 0.73, 0.51),
    'System':         (0.08, 0.72, 0.65),
    'Platform':       (0.02, 0.71, 0.83),
    'Service':        (0.13, 0.83, 0.93),
    'Hardware':       (0.18, 0.83, 0.75),
    'Infrastructure': (0.20, 0.83, 0.60),
    'Art Form':       (0.93, 0.29, 0.60),
    'Event':          (0.96, 0.45, 0.71),
    'Inventory':      (0.58, 0.64, 0.72),
}
DEFAULT_COLOR = (0.39, 0.45, 0.56)

EDGE_GROUPS = {
    'creation':    {'color': (0.13, 0.83, 0.93), 'curve': +1.0,
                    'rels': {'created', 'co-created', 'built', 'designed'}},
    'stewardship': {'color': (0.96, 0.62, 0.04), 'curve': -1.0,
                    'rels': {'owns', 'maintains', 'works_on', 'works_with', 'worked_with'}},
    'flow':        {'color': (0.06, 0.73, 0.51), 'curve': +0.6,
                    'rels': {'uses', 'extends', 'powers', 'stores'}},
    'identity':    {'color': (0.65, 0.55, 0.98), 'curve': -0.6,
                    'rels': {'instance_of', 'named_after', 'understands'}},
    'presence':    {'color': (0.18, 0.83, 0.75), 'curve': +0.3,
                    'rels': {'deployed_on', 'REGISTERED_ON', 'presenting_at', 'monitors', 'protects'}},
}

def edge_color_of(rel):
    for g in EDGE_GROUPS.values():
        if rel in g['rels']:
            return g['color']
    return (0.39, 0.45, 0.56)

def edge_curve_dir(rel):
    for g in EDGE_GROUPS.values():
        if rel in g['rels']:
            return g['curve']
    return 1.0

# ── Build adjacency list (for neural firing) ────────
# adj_flat[adj_offset[i] .. adj_offset[i]+adj_count[i]] = neighbors of node i
adj_list_py = [[] for _ in range(N)]
for _k in range(M):
    _s, _d = edges_src[_k], edges_dst[_k]
    adj_list_py[_s].append(_d)
    adj_list_py[_d].append(_s)

# Flatten for Taichi
adj_flat_py = []
adj_offset_py = []
adj_count_py = []
for _i in range(N):
    adj_offset_py.append(len(adj_flat_py))
    neighbors = list(set(adj_list_py[_i]))
    adj_count_py.append(len(neighbors))
    adj_flat_py.extend(neighbors)
MAX_ADJ = max(len(adj_flat_py), 4)

# ── Taichi Fields ─────────────────────────────────────
MAX_N = max(N, 4)
MAX_M = max(M, 4)

# Node positions & velocity (3D)
pos = ti.Vector.field(3, dtype=ti.f32, shape=MAX_N)
vel = ti.Vector.field(3, dtype=ti.f32, shape=MAX_N)
force = ti.Vector.field(3, dtype=ti.f32, shape=MAX_N)
node_color = ti.Vector.field(3, dtype=ti.f32, shape=MAX_N)
node_radius = ti.field(dtype=ti.f32, shape=MAX_N)
pinned = ti.field(dtype=ti.i32, shape=MAX_N)  # 1 = pinned (won't move in sim)

# Neural firing
activation = ti.field(dtype=ti.f32, shape=MAX_N)       # 0.0 = resting, 1.0 = fully fired
activation_next = ti.field(dtype=ti.f32, shape=MAX_N)  # double-buffer for propagation
adj_flat = ti.field(dtype=ti.i32, shape=MAX_ADJ)
adj_offset = ti.field(dtype=ti.i32, shape=MAX_N)
adj_count = ti.field(dtype=ti.i32, shape=MAX_N)

# Edge spark particles (travel along edges during firing)
MAX_SPARKS = MAX_M * 2  # at most 2 sparks per edge
spark_pos = ti.Vector.field(3, dtype=ti.f32, shape=MAX_SPARKS)
spark_t = ti.field(dtype=ti.f32, shape=MAX_SPARKS)       # 0-1 along edge, <0 = inactive
spark_edge = ti.field(dtype=ti.i32, shape=MAX_SPARKS)     # which edge
spark_color = ti.Vector.field(3, dtype=ti.f32, shape=MAX_SPARKS)
num_sparks = ti.field(dtype=ti.i32, shape=())

# Edges
e_src = ti.field(dtype=ti.i32, shape=MAX_M)
e_dst = ti.field(dtype=ti.i32, shape=MAX_M)
e_color = ti.Vector.field(3, dtype=ti.f32, shape=MAX_M)
e_curve = ti.field(dtype=ti.f32, shape=MAX_M)

# Simulation params
num_nodes = ti.field(dtype=ti.i32, shape=())
num_edges = ti.field(dtype=ti.i32, shape=())
sim_running = ti.field(dtype=ti.i32, shape=())
dt_field = ti.field(dtype=ti.f32, shape=())

# ── Initialize Data ──────────────────────────────────
def init_data():
    num_nodes[None] = N
    num_edges[None] = M
    sim_running[None] = 1
    dt_field[None] = 0.016

    for i in range(N):
        pos[i] = ti.Vector([
            (np.random.random() - 0.5) * 8.0,
            (np.random.random() - 0.5) * 8.0,
            (np.random.random() - 0.5) * 8.0,
        ])
        vel[i] = ti.Vector([0.0, 0.0, 0.0])
        c = NODE_COLORS.get(node_types[i], DEFAULT_COLOR)
        node_color[i] = ti.Vector(list(c))
        node_radius[i] = max(0.08, math.sqrt(node_obs_counts[i]) * 0.06 + 0.05)
        pinned[i] = 0

    for i in range(M):
        e_src[i] = edges_src[i]
        e_dst[i] = edges_dst[i]
        c = edge_color_of(edge_types[i])
        e_color[i] = ti.Vector(list(c))
        e_curve[i] = edge_curve_dir(edge_types[i])

    # Neural firing init
    for i in range(N):
        activation[i] = 0.0
        activation_next[i] = 0.0
        adj_offset[i] = adj_offset_py[i]
        adj_count[i] = adj_count_py[i]
    for i in range(len(adj_flat_py)):
        adj_flat[i] = adj_flat_py[i]

    # Sparks init
    num_sparks[None] = 0
    for i in range(MAX_SPARKS):
        spark_t[i] = -1.0

# ── Force Simulation Kernels ─────────────────────────
@ti.kernel
def compute_forces():
    n = num_nodes[None]

    for i in range(n):
        force[i] = ti.Vector([0.0, 0.0, 0.0])

    # Repulsion (Coulomb)
    for i in range(n):
        if pinned[i] == 0:
            f = ti.Vector([0.0, 0.0, 0.0])
            for j in range(n):
                if i != j:
                    diff = pos[i] - pos[j]
                    dist_sq = diff.dot(diff) + 0.01
                    dist = ti.sqrt(dist_sq)
                    repulsion = 3.5 / dist_sq
                    f += diff / dist * repulsion
            force[i] += f

    # Attraction (spring)
    m = num_edges[None]
    for k in range(m):
        i = e_src[k]
        j = e_dst[k]
        diff = pos[j] - pos[i]
        dist = diff.norm() + 0.001
        rest = 1.5
        attraction = (dist - rest) * 0.15
        f = diff / dist * attraction
        if pinned[i] == 0:
            force[i] += f
        if pinned[j] == 0:
            force[j] -= f

    # Center gravity
    for i in range(n):
        if pinned[i] == 0:
            force[i] -= pos[i] * 0.02


@ti.kernel
def integrate():
    n = num_nodes[None]
    dt = dt_field[None]
    damping = 0.88

    for i in range(n):
        if pinned[i] == 0:
            vel[i] = (vel[i] + force[i] * dt) * damping
            speed = vel[i].norm()
            if speed > 2.0:
                vel[i] = vel[i] / speed * 2.0
            pos[i] += vel[i] * dt
        else:
            vel[i] = ti.Vector([0.0, 0.0, 0.0])


# ── Rendering ────────────────────────────────────────
CURVE_SEGMENTS = 12
max_edge_verts = MAX_M * (CURVE_SEGMENTS + 1)
edge_verts = ti.Vector.field(3, dtype=ti.f32, shape=max_edge_verts)
edge_vert_colors = ti.Vector.field(3, dtype=ti.f32, shape=max_edge_verts)

max_edge_indices = MAX_M * CURVE_SEGMENTS * 2
edge_indices = ti.field(dtype=ti.i32, shape=max_edge_indices)

curve_mode = ti.field(dtype=ti.i32, shape=())
curve_strength = ti.field(dtype=ti.f32, shape=())

@ti.kernel
def build_edge_geometry():
    m = num_edges[None]
    cs = curve_mode[None]
    strength = curve_strength[None]
    segs = CURVE_SEGMENTS

    for k in range(m):
        i = e_src[k]
        j = e_dst[k]
        p0 = pos[i]
        p1 = pos[j]
        col = e_color[k]

        diff = p1 - p0
        dist = diff.norm() + 0.001

        up = ti.Vector([0.0, 1.0, 0.0])
        perp = diff.cross(up)
        if perp.norm() < 0.01:
            up = ti.Vector([1.0, 0.0, 0.0])
            perp = diff.cross(up)
        perp = perp.normalized()

        offset_mag = 0.0
        if cs == 1:
            offset_mag = ti.min(dist * strength, 2.5)
        elif cs == 2:
            offset_mag = ti.min(dist * strength, 2.5) * e_curve[k]

        mid = (p0 + p1) * 0.5 + perp * offset_mag

        for s in range(segs + 1):
            t = ti.cast(s, ti.f32) / ti.cast(segs, ti.f32)
            omt = 1.0 - t
            pt = omt * omt * p0 + 2.0 * omt * t * mid + t * t * p1
            idx = k * (segs + 1) + s
            edge_verts[idx] = pt
            edge_vert_colors[idx] = col

        for s in range(segs):
            base = k * segs * 2 + s * 2
            vert_base = k * (segs + 1)
            edge_indices[base] = vert_base + s
            edge_indices[base + 1] = vert_base + s + 1


# ── Neural Firing Kernels ────────────────────────────
@ti.kernel
def propagate_activation(decay: ti.f32, threshold: ti.f32):
    """Spread activation from fired nodes to neighbors, with decay."""
    n = num_nodes[None]
    for i in range(n):
        activation_next[i] = activation[i] * 0.92  # natural decay each frame

    # Propagate: each active node sends signal to neighbors
    for i in range(n):
        if activation[i] > threshold:
            off = adj_offset[i]
            cnt = adj_count[i]
            for k in range(cnt):
                j = adj_flat[off + k]
                # Add activation (clamped), decayed by distance in hops
                contribution = activation[i] * decay
                ti.atomic_max(activation_next[j], contribution)

    # Swap buffers
    for i in range(n):
        activation[i] = ti.min(activation_next[i], 1.0)


@ti.kernel
def advance_sparks(speed: ti.f32):
    """Move spark particles along their edges."""
    ns = num_sparks[None]
    for i in range(ns):
        if spark_t[i] >= 0.0:
            spark_t[i] += speed
            if spark_t[i] > 1.0:
                spark_t[i] = -1.0  # deactivate
            else:
                # Interpolate position along edge
                k = spark_edge[i]
                p0 = pos[e_src[k]]
                p1 = pos[e_dst[k]]
                t = spark_t[i]
                spark_pos[i] = p0 * (1.0 - t) + p1 * t


# ── Glow kernel ──────────────────────────────────────
node_color_display = ti.Vector.field(3, dtype=ti.f32, shape=MAX_N)
edge_vert_colors_glow = ti.Vector.field(3, dtype=ti.f32, shape=max_edge_verts)

@ti.kernel
def apply_glow_with_activation(boost: ti.f32):
    n = num_nodes[None]
    for i in range(n):
        c = node_color[i]
        a = activation[i]
        # Blend toward white based on activation
        base = ti.min(c * boost, ti.Vector([1.0, 1.0, 1.0]))
        fire_color = ti.Vector([1.0, 0.95, 0.7])  # warm white-gold
        blended = base * (1.0 - a) + fire_color * a
        node_color_display[i] = ti.min(blended, ti.Vector([1.0, 1.0, 1.0]))
    m = num_edges[None]
    segs = CURVE_SEGMENTS
    for k in range(m * (segs + 1)):
        c = edge_vert_colors[k]
        # Edges connected to active nodes glow too
        edge_idx = k // (segs + 1)
        src_a = activation[e_src[edge_idx]]
        dst_a = activation[e_dst[edge_idx]]
        ea = ti.max(src_a, dst_a) * 0.7
        base = ti.min(c * boost, ti.Vector([1.0, 1.0, 1.0]))
        fire_color = ti.Vector([1.0, 0.85, 0.5])
        blended = base * (1.0 - ea) + fire_color * ea
        edge_vert_colors_glow[k] = ti.min(blended, ti.Vector([1.0, 1.0, 1.0]))


# ── 3D Picking & Projection ─────────────────────────
def compute_camera_basis(cam_pos, lookat):
    """Return (forward, right, up) unit vectors for the camera."""
    fwd = np.array([lookat[i] - cam_pos[i] for i in range(3)], dtype=np.float64)
    fwd /= np.linalg.norm(fwd) + 1e-12
    world_up = np.array([0.0, 1.0, 0.0])
    right = np.cross(fwd, world_up)
    rn = np.linalg.norm(right)
    if rn < 1e-6:
        world_up = np.array([0.0, 0.0, 1.0])
        right = np.cross(fwd, world_up)
        rn = np.linalg.norm(right)
    right /= rn
    up = np.cross(right, fwd)
    return fwd, right, up


def project_node_to_screen(node_pos, cam_pos, fwd, right, up, fov_rad, aspect):
    """Project a 3D point to screen coords (0-1, 0-1). Returns (sx, sy, depth)."""
    rel = np.array([node_pos[i] - cam_pos[i] for i in range(3)], dtype=np.float64)
    depth = np.dot(rel, fwd)
    if depth <= 0.1:
        return -1, -1, 0  # behind camera
    half_h = math.tan(fov_rad / 2.0)
    half_w = half_h * aspect
    sx = np.dot(rel, right) / (depth * half_w) * 0.5 + 0.5
    sy = np.dot(rel, up) / (depth * half_h) * 0.5 + 0.5
    return sx, sy, depth


def unproject_cursor_to_3d(cursor_x, cursor_y, depth, cam_pos, fwd, right, up, fov_rad, aspect):
    """Convert 2D cursor (0-1, 0-1) at given depth back to 3D world position."""
    half_h = math.tan(fov_rad / 2.0)
    half_w = half_h * aspect
    x_offset = (cursor_x - 0.5) * 2.0 * depth * half_w
    y_offset = (cursor_y - 0.5) * 2.0 * depth * half_h
    world = np.array(cam_pos) + fwd * depth + right * x_offset + up * y_offset
    return world


def pick_nearest_node(cursor_x, cursor_y, cam_pos, fwd, right, up, fov_rad, aspect, threshold=0.04):
    """Find node nearest to cursor in screen space. Returns (index, screen_dist) or (-1, inf)."""
    best_idx = -1
    best_dist = float('inf')
    for i in range(N):
        p = pos[i]
        np_p = [p[0], p[1], p[2]]
        sx, sy, depth = project_node_to_screen(np_p, cam_pos, fwd, right, up, fov_rad, aspect)
        if sx < 0:
            continue
        d = math.sqrt((sx - cursor_x)**2 + (sy - cursor_y)**2)
        if d < threshold and d < best_dist:
            best_dist = d
            best_idx = i
    return best_idx, best_dist


# ── Spark spawning (Python-side) ─────────────────────
def spawn_sparks_from_node(node_idx):
    """Create spark particles on all edges connected to node_idx."""
    ns = num_sparks[None]
    for k in range(M):
        src_i = edges_src[k]
        dst_i = edges_dst[k]
        if src_i == node_idx or dst_i == node_idx:
            # Find an inactive spark slot
            slot = -1
            for s in range(MAX_SPARKS):
                if spark_t[s] < 0:
                    slot = s
                    break
            if slot < 0:
                continue
            spark_edge[slot] = k
            # Direction: spark travels FROM the fired node
            if src_i == node_idx:
                spark_t[slot] = 0.01
            else:
                spark_t[slot] = 0.99  # travel backward (will be handled)
            spark_color[slot] = ti.Vector([1.0, 0.95, 0.6])  # gold spark
            p0 = pos[src_i]
            spark_pos[slot] = ti.Vector([p0[0], p0[1], p0[2]])
            if ns < MAX_SPARKS:
                ns += 1
    num_sparks[None] = min(ns, MAX_SPARKS)


# ── Main ─────────────────────────────────────────────
def main():
    init_data()
    curve_mode[None] = 0
    curve_strength[None] = 0.3

    W, H = 1400, 900
    ASPECT = W / H
    FOV_DEG = 55
    FOV_RAD = math.radians(FOV_DEG)

    window = ti.ui.Window("Knowledge Graph — Taichi GPU", (W, H), vsync=True)
    canvas = window.get_canvas()
    scene = window.get_scene()
    camera = ti.ui.Camera()

    # GUI state
    sim_on = True
    glow_on = True
    curve_sel = 0
    edge_width = 2.0
    node_size = 0.15
    c_strength = 0.30
    cam_dist = 18.0
    cam_azimuth = 0.4
    cam_elev = 0.35
    labels_on = True
    neural_on = True
    dream_on = True

    # Drag state
    dragged_node = -1
    drag_depth = 0.0
    drag_started_at = (0.0, 0.0)
    hover_node = -1
    was_lmb = False

    # Neural firing state
    fire_decay = 0.55       # how much activation propagates per hop
    fire_threshold = 0.08   # minimum activation to propagate
    spark_speed = 0.04      # how fast sparks travel along edges

    # Dreaming state
    last_interaction = time.time()
    dream_idle_secs = 8.0   # seconds of idle before dreaming starts
    dream_interval = 0.0
    dream_timer = 0.0

    # Frame counter for animation
    frame = 0

    curve_names = ["Straight", "Arc", "Semantic"]

    print("\n[Knowledge Graph — Taichi GPU]")
    print(f"  {N} nodes, {M} edges")
    print("  LMB click — fire neural activation")
    print("  LMB drag  — grab & move nodes")
    print("  Dreaming mode activates after 8s idle")
    print()

    while window.running:
        frame += 1
        now = time.time()

        # ── Camera from spherical coords ──
        cx = cam_dist * math.cos(cam_elev) * math.sin(cam_azimuth)
        cy = cam_dist * math.sin(cam_elev)
        cz = cam_dist * math.cos(cam_elev) * math.cos(cam_azimuth)
        cam_pos_np = np.array([cx, cy, cz])
        lookat_np = np.array([0.0, 0.0, 0.0])
        fwd, right_v, up_v = compute_camera_basis(cam_pos_np, lookat_np)

        camera.position(cx, cy, cz)
        camera.lookat(0.0, 0.0, 0.0)
        camera.up(0.0, 1.0, 0.0)
        camera.fov(FOV_DEG)

        # ── Mouse picking & dragging ──
        cursor = window.get_cursor_pos()
        cur_x, cur_y = cursor[0], cursor[1]
        lmb = window.is_pressed(ti.ui.LMB)

        # Hover detection (always)
        hover_node, _ = pick_nearest_node(cur_x, cur_y, cam_pos_np, fwd, right_v, up_v, FOV_RAD, ASPECT, threshold=0.05)

        if lmb and not was_lmb:
            # Mouse just pressed
            last_interaction = now
            if hover_node >= 0:
                dragged_node = hover_node
                drag_started_at = (cur_x, cur_y)
                p = pos[dragged_node]
                np_p = [p[0], p[1], p[2]]
                _, _, drag_depth = project_node_to_screen(np_p, cam_pos_np, fwd, right_v, up_v, FOV_RAD, ASPECT)
                pinned[dragged_node] = 1
        elif lmb and dragged_node >= 0:
            # Dragging — move node to cursor position in 3D
            last_interaction = now
            world = unproject_cursor_to_3d(cur_x, cur_y, drag_depth, cam_pos_np, fwd, right_v, up_v, FOV_RAD, ASPECT)
            pos[dragged_node] = ti.Vector([float(world[0]), float(world[1]), float(world[2])])
            vel[dragged_node] = ti.Vector([0.0, 0.0, 0.0])
        elif not lmb and was_lmb:
            # Mouse released
            if dragged_node >= 0:
                # Check if this was a click (not a drag) → fire neuron
                dx = cur_x - drag_started_at[0]
                dy = cur_y - drag_started_at[1]
                click_dist = math.sqrt(dx*dx + dy*dy)
                if click_dist < 0.015 and neural_on:
                    # It was a click! Fire neural activation
                    activation[dragged_node] = 1.0
                    spawn_sparks_from_node(dragged_node)
                    pinned[dragged_node] = 0  # don't pin on click
            dragged_node = -1

        was_lmb = lmb

        # ── Graph Dreaming ──
        idle_time = now - last_interaction
        if dream_on and idle_time > dream_idle_secs:
            dream_timer += 0.016
            # Fire a random node softly every ~1.5 seconds
            dream_interval_cur = 1.5 - min(idle_time / 60.0, 1.0) * 0.8  # speeds up over time
            if dream_timer > dream_interval_cur:
                dream_timer = 0.0
                dream_node = random.randint(0, N - 1)
                activation[dream_node] = 0.5 + random.random() * 0.3  # softer than user click
                spawn_sparks_from_node(dream_node)
        else:
            dream_timer = 0.0

        # ── Neural propagation ──
        if neural_on:
            propagate_activation(fire_decay, fire_threshold)
            advance_sparks(spark_speed)

        # ── Highlight hovered/dragged node ──
        highlight_idx = dragged_node if dragged_node >= 0 else hover_node

        # ── GUI Controls Panel ──
        with window.GUI.sub_window("Controls", 0.01, 0.01, 0.24, 0.88) as g:
            g.text("Camera")
            cam_dist = g.slider_float("Distance", cam_dist, 3.0, 50.0)
            cam_azimuth = g.slider_float("Orbit H", cam_azimuth, -3.14, 3.14)
            cam_elev = g.slider_float("Orbit V", cam_elev, -1.2, 1.2)
            g.text("")
            g.text("Graph")
            sim_on = g.checkbox("Simulate", sim_on)
            glow_on = g.checkbox("Glow", glow_on)
            labels_on = g.checkbox("Labels", labels_on)
            neural_on = g.checkbox("Neural Fire", neural_on)
            dream_on = g.checkbox("Dreaming", dream_on)
            curve_sel = g.slider_int("Curve Style", curve_sel, 0, 2)
            g.text(f"  Mode: {curve_names[curve_sel]}")
            c_strength = g.slider_float("Curvature", c_strength, 0.0, 0.8)
            edge_width = g.slider_float("Edge Width", edge_width, 0.5, 6.0)
            node_size = g.slider_float("Node Size", node_size, 0.05, 0.40)
            g.text("")
            if g.button("Unpin All"):
                for i in range(N):
                    pinned[i] = 0
            if g.button("Clear Fire"):
                for i in range(N):
                    activation[i] = 0.0
                for i in range(MAX_SPARKS):
                    spark_t[i] = -1.0
            # Show hovered/selected node info
            if highlight_idx >= 0:
                g.text(f"Node: {node_names[highlight_idx]}")
                g.text(f"Type: {node_types[highlight_idx]}")
                act_val = activation[highlight_idx]
                if act_val > 0.01:
                    g.text(f"  activation: {act_val:.2f}")
                if pinned[highlight_idx]:
                    g.text("  [pinned]")
            # Dreaming indicator
            if dream_on and idle_time > dream_idle_secs:
                g.text("")
                g.text("~ dreaming ~")

        # Sync GUI → Taichi fields
        curve_mode[None] = curve_sel
        curve_strength[None] = c_strength
        sim_running[None] = 1 if sim_on else 0

        # ── Simulate ──
        if sim_running[None]:
            compute_forces()
            integrate()

        # ── Build edge geometry ──
        build_edge_geometry()

        # ── Glow + Neural activation colors ──
        glow_boost = 1.5 if glow_on else 1.0
        apply_glow_with_activation(glow_boost)

        # Highlight hovered node: make it white/bright
        if highlight_idx >= 0:
            node_color_display[highlight_idx] = ti.Vector([1.0, 1.0, 1.0])

        # ── Set up camera ──
        scene.set_camera(camera)

        # ── Lighting ──
        if glow_on:
            scene.ambient_light((0.22, 0.22, 0.30))
            scene.point_light(pos=(cx + 5, cy + 8, cz + 5), color=(1.0, 1.0, 1.0))
            scene.point_light(pos=(-10, 10, -8), color=(0.7, 0.7, 0.9))
            scene.point_light(pos=(8, -5, 8), color=(0.5, 0.4, 0.7))
        else:
            scene.ambient_light((0.12, 0.12, 0.18))
            scene.point_light(pos=(cx + 5, cy + 8, cz + 5), color=(0.7, 0.7, 0.8))
            scene.point_light(pos=(-10, 10, -8), color=(0.4, 0.4, 0.6))
            scene.point_light(pos=(8, -5, 8), color=(0.25, 0.2, 0.4))

        # ── Draw edges ──
        total_edge_indices = M * CURVE_SEGMENTS * 2
        if total_edge_indices > 0:
            scene.lines(edge_verts,
                        width=edge_width,
                        indices=edge_indices,
                        per_vertex_color=edge_vert_colors_glow)

        # ── Draw spark particles ──
        active_spark_count = 0
        for s in range(MAX_SPARKS):
            if spark_t[s] >= 0:
                active_spark_count += 1
        if active_spark_count > 0:
            scene.particles(spark_pos,
                            radius=0.08,
                            per_vertex_color=spark_color)

        # ── Draw nodes ──
        scene.particles(pos,
                        radius=node_size,
                        per_vertex_color=node_color_display)

        # ── Canvas ──
        canvas.scene(scene)
        canvas.set_background_color((0.028, 0.028, 0.047))

        # ── Floating Labels (2D overlay via GUI text) ──
        if labels_on:
            # Collect visible nodes with screen positions, sorted by depth
            label_items = []
            for i in range(N):
                p = pos[i]
                np_p = [p[0], p[1], p[2]]
                sx, sy, depth = project_node_to_screen(np_p, cam_pos_np, fwd, right_v, up_v, FOV_RAD, ASPECT)
                if sx < 0 or depth < 0.5:
                    continue
                alpha = max(0.0, min(1.0, 1.0 - (depth - 5.0) / 40.0))
                if alpha < 0.1:
                    continue
                name = node_names[i]
                if len(name) > 18:
                    name = name[:17] + "."
                a = activation[i]
                # Build display string with activation indicator
                if a > 0.1:
                    name = f"* {name}"
                label_items.append((depth, sx, sy, name, i))

            # Sort front-to-back, show up to 20 nearest labels
            label_items.sort(key=lambda x: x[0])
            visible_labels = label_items[:20]

            if visible_labels:
                with window.GUI.sub_window("##labels", 0.76, 0.01, 0.23, 0.55) as lg:
                    lg.text("Nearby Nodes")
                    lg.text("")
                    for _, sx, sy, name, idx in visible_labels:
                        a = activation[idx]
                        if a > 0.3:
                            lg.text(f">> {name} [{a:.1f}]")
                        elif pinned[idx]:
                            lg.text(f" @ {name}")
                        else:
                            lg.text(f"   {name}")

        window.show()


if __name__ == "__main__":
    main()
