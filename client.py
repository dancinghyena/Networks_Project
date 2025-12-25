#!/usr/bin/env python3
"""NetRush Client - Grid Clash Game"""
import socket, threading, time, csv
from collections import deque
from copy import deepcopy
import pygame
from protocol import MsgType, unpack_packet, make_init, make_event

# Try psutil for CPU monitoring
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# Config
SERVER_IP, SERVER_PORT = "127.0.0.1", 5000
GRID_N = 5
CELL_SIZE = 100
RDT_TIMEOUT, MAX_RETRIES = 0.5, 3

# Cell States
UNCLAIMED = 'UNCLAIMED'
PENDING = 'PENDING'  # Client clicked, waiting for server confirmation

# State
CLIENT_ID = None
SERVER_ADDR = (SERVER_IP, SERVER_PORT)
grid = [[UNCLAIMED]*GRID_N for _ in range(GRID_N)]
pending_cells = {}  # {cell_id: {'player': id, 'time': timestamp}} - local pending state
event_queue = {}
seen_ids = set()
lock = threading.Lock()
running, game_over, winners = True, False, None
last_recv_ms, jitter = None, 0.0
last_snapshot = None

# Smoothing: track cell transition animations
cell_animations = {}  # {(r,c): {'from_color': color, 'to_color': color, 'start': time, 'duration': 0.2}}

# Socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 0))
sock.settimeout(0.1)

# Colors
COLORS = [(255,0,0),(0,200,0),(0,0,255),(255,255,0),(255,0,255),(0,255,255),(128,128,0),(128,0,128)]
PENDING_COLOR = (255, 220, 180)  # Light peach/pastel orange for pending
UNCLAIMED_COLOR = (200, 200, 200)

def get_player_color(owner):
    """Get solid color for a player"""
    if owner == UNCLAIMED: return UNCLAIMED_COLOR
    if owner == PENDING: return PENDING_COLOR
    return COLORS[(owner-1) % len(COLORS)] if isinstance(owner, int) else (150,150,150)

def lerp_color(c1, c2, t):
    """Linear interpolation between two colors for smoothing"""
    t = max(0, min(1, t))  # Clamp to 0-1
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t)
    )

def get_display_color(r, c, owner):
    """Get display color with pending state and smoothing animation"""
    key = (r, c)
    target_color = get_player_color(owner)
    
    # Check if this cell is in pending state (clicked but not confirmed)
    cell_id = r * GRID_N + c
    if cell_id in pending_cells and owner == UNCLAIMED:
        # Show unique PENDING color (orange)
        return PENDING_COLOR
    
    # Check for smooth transition animation
    if key in cell_animations:
        anim = cell_animations[key]
        elapsed = time.time() - anim['start']
        t = elapsed / anim['duration']
        if t >= 1.0:
            del cell_animations[key]
            return target_color
        return lerp_color(anim['from_color'], anim['to_color'], t)
    
    return target_color

def start_animation(r, c, from_color, to_color, duration=0.2):
    """Start a color transition animation (smoothing)"""
    cell_animations[(r, c)] = {
        'from_color': from_color,
        'to_color': to_color,
        'start': time.time(),
        'duration': duration
    }

def get_cpu():
    return psutil.cpu_percent(interval=None) if HAS_PSUTIL else 0.0

def send_init():
    global CLIENT_ID
    while CLIENT_ID is None and running:
        try:
            sock.sendto(make_init(), SERVER_ADDR)
            data, _ = sock.recvfrom(65536)
            hdr, payload = unpack_packet(data, GRID_N)
            if hdr and hdr['msg_type'] == MsgType.INIT_ACK:
                CLIENT_ID = payload.get('client_id')
                print(f"[CLIENT] Connected as Player {CLIENT_ID}")
        except: time.sleep(0.5)

def retransmit():
    """Retransmit critical events (cell claims)"""
    while running:
        time.sleep(0.05)
        now = time.time()
        for cell_id in list(event_queue.keys()):
            d = event_queue.get(cell_id)
            if not d: continue
            if now - d['sent'] > RDT_TIMEOUT:
                if d['retries'] >= MAX_RETRIES:
                    # Give up - remove from pending
                    del event_queue[cell_id]
                    if cell_id in pending_cells:
                        del pending_cells[cell_id]
                else:
                    try: sock.sendto(d['pkt'], SERVER_ADDR)
                    except: pass
                    d['sent'], d['retries'] = now, d['retries'] + 1

def keep_alive():
    """Periodically send INIT as keep-alive"""
    while running:
        time.sleep(3)
        if CLIENT_ID:
            try: sock.sendto(make_init(), SERVER_ADDR)
            except: pass

def apply_changes(g, changes, animate=True):
    """Apply delta changes to grid with optional animation"""
    for ch in changes:
        if len(ch) >= 3:
            r, c, owner = ch[0], ch[1], ch[2]
            if 0 <= r < GRID_N and 0 <= c < GRID_N:
                old_owner = g[r][c]
                if old_owner != owner and animate:
                    # Trigger smooth color transition
                    start_animation(r, c, get_player_color(old_owner), get_player_color(owner))
                g[r][c] = owner
                # Remove from pending if confirmed
                cell_id = r * GRID_N + c
                if cell_id in pending_cells:
                    del pending_cells[cell_id]

def listener():
    global last_recv_ms, jitter, running, game_over, winners, grid, last_snapshot
    
    current_grid = [[UNCLAIMED]*GRID_N for _ in range(GRID_N)]
    last_applied_sid = -1
    
    while running:
        try: 
            data, _ = sock.recvfrom(65536)
            recv_ms = int(time.time() * 1000)
        except socket.timeout: continue
        except: break
        
        hdr, payload = unpack_packet(data, GRID_N)
        if not hdr: continue
        
        if hdr['msg_type'] == MsgType.ACK:
            cell = payload.get('cell')
            owner = payload.get('owner')
            # Remove from event queue
            if cell in event_queue: 
                del event_queue[cell]
            # Remove from pending
            if cell in pending_cells:
                del pending_cells[cell]
            # Apply immediately for responsiveness
            r, c = cell // GRID_N, cell % GRID_N
            if 0 <= r < GRID_N and 0 <= c < GRID_N:
                old = current_grid[r][c]
                if old != owner:
                    start_animation(r, c, get_player_color(old), get_player_color(owner))
                current_grid[r][c] = owner
                with lock:
                    grid[r][c] = owner
                    
        elif hdr['msg_type'] == MsgType.GAME_OVER:
            game_over, winners = True, payload.get('winner')
            if payload.get('final_grid'):
                with lock:
                    grid = deepcopy(payload['final_grid'])
                    
        elif hdr['msg_type'] == MsgType.SNAPSHOT:
            sid = hdr['snapshot_id']
            server_ts = hdr['timestamp_ms']
            
            if sid in seen_ids or sid <= last_applied_sid:
                continue
            seen_ids.add(sid)
            if len(seen_ids) > 500: 
                seen_ids.discard(min(seen_ids))
            
            # Jitter calculation
            if last_recv_ms:
                inter = recv_ms - last_recv_ms
                expected = 1000 / 20
                jitter = 0.9 * jitter + 0.1 * abs(inter - expected)
            last_recv_ms = recv_ms
            
            # Process redundant updates
            for r in payload.get('redundant', []):
                red_sid = r.get('snapshot_id')
                if red_sid and red_sid not in seen_ids and red_sid > last_applied_sid:
                    seen_ids.add(red_sid)
                    apply_changes(current_grid, r.get('changes', []), animate=False)
            
            # Apply snapshot
            if payload.get('full') and payload.get('grid'):
                new_grid = deepcopy(payload['grid'])
                # Animate changes from full snapshot
                for r in range(GRID_N):
                    for c in range(GRID_N):
                        if current_grid[r][c] != new_grid[r][c]:
                            start_animation(r, c, get_player_color(current_grid[r][c]), get_player_color(new_grid[r][c]))
                current_grid = new_grid
            else:
                apply_changes(current_grid, payload.get('changes', []), animate=True)
            
            last_applied_sid = sid
            
            with lock:
                grid = deepcopy(current_grid)
                last_snapshot = {
                    'sid': sid,
                    'server_ts': server_ts,
                    'recv_ms': recv_ms,
                    'grid': current_grid
                }

def main():
    global grid, running, game_over, pending_cells
    pygame.init()
    screen = pygame.display.set_mode((GRID_N * CELL_SIZE, GRID_N * CELL_SIZE))
    pygame.display.set_caption("Grid Clash")
    font = pygame.font.SysFont(None, 24)
    
    if HAS_PSUTIL: psutil.cpu_percent(interval=None)
    
    # Connect
    threading.Thread(target=send_init, daemon=True).start()
    while CLIENT_ID is None and running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT: running = False; return
        time.sleep(0.1)
    
    # Logging
    log = open(f"client_{CLIENT_ID}_log.csv", "w", newline="")
    writer = csv.DictWriter(log, fieldnames=[
        'client_id', 'snapshot_id', 'seq_num', 'server_timestamp_ms', 
        'recv_time_ms', 'latency_ms', 'jitter_ms', 'perceived_position_error', 'cpu_percent'
    ])
    writer.writeheader()
    
    # Start threads
    threading.Thread(target=listener, daemon=True).start()
    threading.Thread(target=retransmit, daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()
    
    clock = pygame.time.Clock()
    last_logged_sid = -1
    
    while running:
        now_ms = int(time.time() * 1000)
        
        # Log metrics
        with lock:
            snap = last_snapshot
        
        if snap and snap.get('sid', -1) > last_logged_sid:
            latency = now_ms - snap.get('server_ts', now_ms)
            writer.writerow({
                'client_id': CLIENT_ID,
                'snapshot_id': snap.get('sid'),
                'seq_num': snap.get('sid'),
                'server_timestamp_ms': snap.get('server_ts'),
                'recv_time_ms': snap.get('recv_ms'),
                'latency_ms': latency,
                'jitter_ms': round(jitter, 2),
                'perceived_position_error': 0,
                'cpu_percent': round(get_cpu(), 2)
            })
            log.flush()
            last_logged_sid = snap.get('sid', -1)
        
        # Events
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT: running = False
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and not game_over:
                c, r = ev.pos[0] // CELL_SIZE, ev.pos[1] // CELL_SIZE
                if 0 <= r < GRID_N and 0 <= c < GRID_N:
                    cell_id = r * GRID_N + c
                    with lock:
                        current_state = grid[r][c]
                    
                    # Only claim unclaimed cells
                    if current_state == UNCLAIMED and cell_id not in pending_cells:
                        # Set to PENDING state locally
                        pending_cells[cell_id] = {
                            'player': CLIENT_ID,
                            'time': time.time()
                        }
                        # Send event to server
                        event_queue[cell_id] = {
                            'pkt': make_event(cell_id, CLIENT_ID, cell_id), 
                            'sent': 0, 
                            'retries': 0
                        }
        
        # Render
        screen.fill((30, 30, 30))
        with lock:
            for r in range(GRID_N):
                for c in range(GRID_N):
                    owner = grid[r][c]
                    # Get color with pending state and smoothing
                    color = get_display_color(r, c, owner)
                    pygame.draw.rect(screen, color, (c*CELL_SIZE, r*CELL_SIZE, CELL_SIZE-1, CELL_SIZE-1))
        
        # Game over overlay
        if game_over:
            overlay = pygame.Surface((GRID_N*CELL_SIZE, GRID_N*CELL_SIZE), pygame.SRCALPHA)
            overlay.fill((0,0,0,180))
            screen.blit(overlay, (0,0))
            if winners:
                if len(winners) == 1:
                    txt = f"Player {winners[0]} Wins!"
                else:
                    txt = f"Tie: Players {', '.join(map(str, winners))}"
            else:
                txt = "Game Over"
            text_surf = font.render(txt, True, (255,255,255))
            text_rect = text_surf.get_rect(center=(GRID_N*CELL_SIZE//2, GRID_N*CELL_SIZE//2))
            screen.blit(text_surf, text_rect)
        
        # Player indicator
        screen.blit(font.render(f"You: Player {CLIENT_ID}", True, get_player_color(CLIENT_ID)), (5, 5))
        pygame.display.flip()
        clock.tick(60)
    
    pygame.quit()
    log.close()
    sock.close()

if __name__ == "__main__": 
    main()