

import argparse, socket, threading, json, time, csv
from collections import deque
import pygame


SERVER_IP = "127.0.0.1"
SERVER_PORT = 5000
GRID_N = 20
CELL_SIZE = 30
INTERP_DELAY_MS = 100      # client will render server_time = now - INTERP_DELAY_MS
MAX_BUFFERED = 200
CLIENT_LOG_TEMPLATE = "client_{}_log.csv"

parser = argparse.ArgumentParser()
parser.add_argument('--id', type=int, required=True, help="Client/player id (integer)")
parser.add_argument('--server', type=str, default=SERVER_IP)
parser.add_argument('--port', type=int, default=SERVER_PORT)
args = parser.parse_args()
CLIENT_ID = args.id
SERVER_ADDR = (args.server, args.port)


COLORS = {
    'UNCLAIMED': (200, 200, 200),
    1: (255, 0, 0),
    2: (0, 200, 0),
    3: (0, 0, 255),
    4: (255, 255, 0),
    'PENDING': (180, 180, 255),
}


reconstructed_grid = [['UNCLAIMED' for _ in range(GRID_N)] for _ in range(GRID_N)]
buffered_snapshots = deque(maxlen=MAX_BUFFERED)  # each item: {'snapshot_id','server_timestamp_ms','full','changes','grid'(opt)}
lock = threading.Lock()
last_applied_id = -1

log_file = open(CLIENT_LOG_TEMPLATE.format(CLIENT_ID), "w", newline="")
writer = csv.DictWriter(log_file, fieldnames=[
    'client_id','snapshot_id','server_timestamp_ms','recv_time_ms','latency_ms','inter_arrival_ms','jitter_ms','bytes'
])
writer.writeheader()
last_recv_time = None
jitter = 0.0

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 0))  # bind to any interface, let OS assign port
sock.setblocking(True)

def send_click(row, col):
    cell_id = row * GRID_N + col
    msg = {
        'type': 'ACQUIRE_REQUEST',
        'client_id': CLIENT_ID,
        'cell': cell_id,
        'timestamp': time.time()
    }
    sock.sendto(json.dumps(msg).encode(), SERVER_ADDR)

def apply_changes_to_grid(grid, changes):
    for (r, c, owner) in changes:
        grid[r][c] = owner

def listener():
    """Receive packets, append to buffered_snapshots, log metrics."""
    global last_recv_time, jitter
    while True:
        data, _ = sock.recvfrom(65536)
        recv_time_ms = int(time.time() * 1000)
        try:
            pkt = json.loads(data.decode())
        except Exception:
            continue
        sid = pkt.get('snapshot_id')
        server_ts = pkt.get('server_timestamp_ms', recv_time_ms)
        b = len(data)
        # logging
        inter = None
        if last_recv_time is not None:
            inter = recv_time_ms - last_recv_time
            # simple jitter estimate: exponential avg
            jitter = 0.9 * jitter + 0.1 * abs(inter - (jitter if jitter else inter))
        last_recv_time = recv_time_ms
        writer.writerow({
            'client_id': CLIENT_ID,
            'snapshot_id': sid,
            'server_timestamp_ms': server_ts,
            'recv_time_ms': recv_time_ms,
            'latency_ms': recv_time_ms - server_ts,
            'inter_arrival_ms': inter if inter is not None else '',
            'jitter_ms': jitter,
            'bytes': b
        })
        log_file.flush()
        with lock:
            buffered_snapshots.append(pkt)

def reconstruct_state_for_playback(playback_time_ms):
  
    with lock:
        buf = list(buffered_snapshots)
    if not buf:
        return None
    chosen_idx = None
    for i in range(len(buf)-1, -1, -1):
        if buf[i].get('server_timestamp_ms', 0) <= playback_time_ms:
            chosen_idx = i
            break
    if chosen_idx is None:
        chosen_idx = 0
    start_idx = None
    for i in range(chosen_idx, -1, -1):
        if buf[i].get('full'):
            start_idx = i
            break
    if start_idx is None:
        return None
    grid_state = [row[:] for row in buf[start_idx].get('grid', [['UNCLAIMED']*GRID_N for _ in range(GRID_N)])]
    for i in range(start_idx + 1, chosen_idx + 1):
        changes = buf[i].get('changes', [])
        apply_changes_to_grid(grid_state, changes)
    return grid_state, buf[chosen_idx].get('snapshot_id'), buf[chosen_idx].get('server_timestamp_ms')

pygame.init()
screen = pygame.display.set_mode((GRID_N * CELL_SIZE, GRID_N * CELL_SIZE))
pygame.display.set_caption(f"Grid Clash - Player {CLIENT_ID}")

def draw(grid_to_draw):
    for r in range(GRID_N):
        for c in range(GRID_N):
            owner = grid_to_draw[r][c]
            color = COLORS.get(owner, COLORS['UNCLAIMED'])
            rect = (c * CELL_SIZE, r * CELL_SIZE, CELL_SIZE-1, CELL_SIZE-1)
            pygame.draw.rect(screen, color, rect)
    pygame.display.flip()

threading.Thread(target=listener, daemon=True).start()

clock = pygame.time.Clock()
running = True
while running:
    now_ms = int(time.time() * 1000)
    playback_ms = now_ms - INTERP_DELAY_MS
    reconstructed = reconstruct_state_for_playback(playback_ms)
    if reconstructed:
        grid_state, sid, s_ts = reconstructed
        with lock:
            reconstructed_grid = grid_state
            last_applied_id = sid
    for ev in pygame.event.get():
        if ev.type == pygame.QUIT:
            running = False
        elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            x,y = ev.pos
            row, col = y // CELL_SIZE, x // CELL_SIZE
            send_click(row, col)
    with lock:
        draw(reconstructed_grid)
    clock.tick(60)

pygame.quit()
log_file.close()
sock.close()
