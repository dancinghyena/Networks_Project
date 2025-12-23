#!/usr/bin/env python3

import socket
import threading
import json
import time
import struct
import zlib
import pygame
import sys
import csv
from copy import deepcopy
from collections import deque

# Server configuration
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 5000

# Client configuration
GRID_N = 5
CELL_SIZE = 100
WINDOW_SIZE = GRID_N * CELL_SIZE

# INIT retry configuration
INIT_TIMEOUT = 0.1  # 100ms
MAX_INIT_RETRIES = 3

# Interpolation configuration
INTERPOLATION_DELAY_MS = 100  # Render 100ms in the past for smoothing
SNAPSHOT_BUFFER_SIZE = 50     # Keep last 50 snapshots for interpolation

# NetRush Protocol Constants
PROTOCOL_ID = b"NRSH"
VERSION = 1
MSG_TYPE_INIT = 0
MSG_TYPE_DATA = 1
MSG_TYPE_EVENT = 2
MSG_TYPE_ACK = 3
HEADER_SIZE = 28

# Client state
client_id = None
grid = [['UNCLAIMED' for _ in range(GRID_N)] for _ in range(GRID_N)]
last_snapshot_id = -1
lock = threading.Lock()
running = True
game_over = False
winner = None

# Snapshot buffer for interpolation (stores {snapshot_id, timestamp, grid_state})
snapshot_buffer = deque(maxlen=SNAPSHOT_BUFFER_SIZE)

# Metrics tracking
latencies = []
last_recv_time = None
jitter_values = []
client_log_file = None
client_log_writer = None

# Socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(0.01)  # Non-blocking with short timeout

# Colors
COLORS = {
    'UNCLAIMED': (200, 200, 200),
    'background': (50, 50, 50),
    'grid_line': (100, 100, 100),
    'text': (255, 255, 255)
}

def init_logging():
    """Initialize CSV logging for client metrics"""
    global client_log_file, client_log_writer
    log_filename = f"client_{client_id}_log.csv" if client_id else "client_log.csv"
    client_log_file = open(log_filename, "w", newline="")
    client_log_writer = csv.DictWriter(client_log_file, fieldnames=[
        'client_id', 'snapshot_id', 'seq_num', 'server_timestamp_ms',
        'recv_time_ms', 'latency_ms', 'jitter_ms', 'perceived_position_error'
    ])
    client_log_writer.writeheader()

def log_snapshot_metrics(snapshot_id, seq_num, server_timestamp_ms, recv_time_ms, latency_ms, jitter_ms):
    """Log snapshot metrics to CSV"""
    if client_log_writer:
        client_log_writer.writerow({
            'client_id': client_id,
            'snapshot_id': snapshot_id,
            'seq_num': seq_num,
            'server_timestamp_ms': server_timestamp_ms,
            'recv_time_ms': recv_time_ms,
            'latency_ms': latency_ms,
            'jitter_ms': jitter_ms,
            'perceived_position_error': 0  # Grid-based game, error is binary (correct/incorrect cell state)
        })
        client_log_file.flush()

def create_header(msg_type, snapshot_id, seq_num, payload):
    """Create NetRush protocol header"""
    server_timestamp = int(time.time() * 1000)
    payload_len = len(payload)
    checksum = zlib.crc32(payload) & 0xFFFFFFFF
    
    header = struct.pack(
        '!4s B B I I Q H I',
        PROTOCOL_ID,      
        VERSION,          
        msg_type,         
        snapshot_id,      
        seq_num,          
        server_timestamp, 
        payload_len,      
        checksum          
    )
    return header

def parse_header(data):
    
    if len(data) < HEADER_SIZE:
        return None
    
    try:
        protocol_id, version, msg_type, snapshot_id, seq_num, server_timestamp, payload_len, checksum = struct.unpack(
            '!4s B B I I Q H I',
            data[:HEADER_SIZE]
        )
        
        if protocol_id != PROTOCOL_ID:
            return None
        
        return {
            'protocol_id': protocol_id,
            'version': version,
            'msg_type': msg_type,
            'snapshot_id': snapshot_id,
            'seq_num': seq_num,
            'server_timestamp': server_timestamp,
            'payload_len': payload_len,
            'checksum': checksum
        }
    except:
        return None

def get_player_color(player_id):
    
    if player_id == 'UNCLAIMED':
        return COLORS['UNCLAIMED']
    
    # Hash the player_id to get consistent colors
    match player_id:
        case 1:
            return (255, 0, 0)  # Red
        case 2:
            return (0, 255, 0)  # Green
        case 3:
            return (0, 0, 255)  # Blue
        case 4:
            return (255, 255, 0)  # Yellow
        case _:
            # Generate color based on hash
            h = hash(player_id)
            r = (h & 0xFF0000) >> 16
            g = (h & 0x00FF00) >> 8
            b = h & 0x0000FF;
            return (r, g, b)

def get_interpolated_grid():
    
    if not snapshot_buffer:
        return grid
    
    target_time = int(time.time() * 1000) - INTERPOLATION_DELAY_MS
    

    best_snapshot = None
    min_diff = float('inf')
    
    for snapshot in snapshot_buffer:
        diff = abs(snapshot['recv_time_ms'] - target_time)
        if diff < min_diff:
            min_diff = diff
            best_snapshot = snapshot
    
    if best_snapshot:
        return best_snapshot['grid_state']
    
    return grid

def send_init():
    
    global client_id, grid
    
    print("[CLIENT] Sending INIT to server...")
    
    for attempt in range(MAX_INIT_RETRIES):
        init_msg = {
            'type': 'INIT',
            'timestamp': time.time()
        }
        
        payload = json.dumps(init_msg).encode()
        header = create_header(MSG_TYPE_INIT, 0, 0, payload)
        packet = header + payload
        
        print(f"[CLIENT] Packet size: {len(packet)} bytes (header: {HEADER_SIZE}, payload: {len(payload)})")
        print(f"[CLIENT] Header bytes: {packet[:HEADER_SIZE].hex()}")
        
        try:
            sock.sendto(packet, (SERVER_HOST, SERVER_PORT))
            print(f"[CLIENT] INIT sent (attempt {attempt + 1}/{MAX_INIT_RETRIES})")
        except Exception as e:
            print(f"[CLIENT] Failed to send INIT: {e}")
            continue
        
        # Wait for ACK
        start_time = time.time()
        while time.time() - start_time < INIT_TIMEOUT:
            try:
                data, addr = sock.recvfrom(65536)
                
                # Parse header
                header_data = parse_header(data)
                if not header_data or header_data['msg_type'] != MSG_TYPE_ACK:
                    continue
                
                # Extract and verify payload
                payload_data = data[HEADER_SIZE:HEADER_SIZE + header_data['payload_len']]
                calculated_checksum = zlib.crc32(payload_data) & 0xFFFFFFFF
                if calculated_checksum != header_data['checksum']:
                    continue
                
                msg = json.loads(payload_data.decode())
                
                if msg.get('type') == 'ACK':
                    with lock:
                        client_id = msg.get('assigned_id')
                        received_grid = msg.get('grid')
                        if received_grid:
                            grid = deepcopy(received_grid)
                    
                    print(f"[CLIENT] ACK received! Assigned ID: {client_id}")
                    init_logging()  
                    return True
                    
            except socket.timeout:
                continue
            except Exception as e:
                continue
        
        print(f"[CLIENT] No ACK received, retrying...")
        time.sleep(0.05)
    
    print("[CLIENT] Failed to receive ACK after max retries")
    return False

def send_acquire(cell):
    """Send ACQUIRE_REQUEST for a cell"""
    if client_id is None:
        return
    
    msg = {
        'type': 'ACQUIRE_REQUEST',
        'cell': cell,
        'client_id': client_id,
        'timestamp': time.time()
    }
    
    payload = json.dumps(msg).encode()
    header = create_header(MSG_TYPE_EVENT, 0, 0, payload)
    packet = header + payload
    
    try:
        sock.sendto(packet, (SERVER_HOST, SERVER_PORT))
    except Exception as e:
        print(f"[CLIENT] Failed to send ACQUIRE_REQUEST: {e}")

def receive_updates():
    """Background thread to receive SNAPSHOT and GAME_OVER messages"""
    global grid, last_snapshot_id, running, game_over, winner, last_recv_time
    
    print("[CLIENT] Receive thread started")
    
    while running:
        try:
            data, addr = sock.recvfrom(65536)
            recv_time_ms = int(time.time() * 1000)
            
            # Parse header
            header = parse_header(data)
            if not header:
                continue
            
            # Extract and verify payload
            payload = data[HEADER_SIZE:HEADER_SIZE + header['payload_len']]
            calculated_checksum = zlib.crc32(payload) & 0xFFFFFFFF
            if calculated_checksum != header['checksum']:
                continue
            
            msg = json.loads(payload.decode())
            msg_type_name = msg.get('type')
            
          
            if header['msg_type'] == MSG_TYPE_DATA and msg_type_name == 'SNAPSHOT':
                snapshot_id = msg.get('snapshot_id', -1)
                server_timestamp_ms = header['server_timestamp']
                seq_num = header['seq_num']
                
                # Calculate latency and jitter
                latency_ms = recv_time_ms - server_timestamp_ms
                latencies.append(latency_ms)
                
                jitter_ms = 0
                if last_recv_time is not None:
                    inter_arrival = recv_time_ms - last_recv_time
                    if len(latencies) >= 2:
                        jitter_ms = abs(inter_arrival - (1000.0 / 20))  # Expected 50ms at 20Hz
                        jitter_values.append(jitter_ms)
                
                last_recv_time = recv_time_ms
                
               
                log_snapshot_metrics(snapshot_id, seq_num, server_timestamp_ms, 
                                    recv_time_ms, latency_ms, jitter_ms)
                
                with lock:
                    if snapshot_id > last_snapshot_id:
                        if msg.get('full') and 'grid' in msg:
                            grid = deepcopy(msg['grid'])
                        
                        changes = msg.get('changes', [])
                        for change in changes:
                            r, c, owner = change
                            if 0 <= r < GRID_N and 0 <= c < GRID_N:
                                grid[r][c] = owner
                        
                 
                        redundant = msg.get('redundant', [])
                        for red_snapshot in redundant:
                            red_changes = red_snapshot.get('changes', [])
                            for change in red_changes:
                                r, c, owner = change
                                if 0 <= r < GRID_N and 0 <= c < GRID_N:
                                    if grid[r][c] == 'UNCLAIMED':  # Don't overwrite newer state
                                        grid[r][c] = owner
                        
                        snapshot_buffer.append({
                            'snapshot_id': snapshot_id,
                            'recv_time_ms': recv_time_ms,
                            'grid_state': deepcopy(grid)
                        })
                        
                        last_snapshot_id = snapshot_id
            
            elif header['msg_type'] == MSG_TYPE_EVENT and msg_type_name == 'GAME_OVER':
                with lock:
                    game_over = True
                    winner = msg.get('winner')
                    final_grid = msg.get('final_grid')
                    if final_grid:
                        grid = deepcopy(final_grid)
                
                if latencies:
                    avg_latency = sum(latencies) / len(latencies)
                    print(f"[CLIENT] Average latency: {avg_latency:.2f}ms")
                if jitter_values:
                    avg_jitter = sum(jitter_values) / len(jitter_values)
                    print(f"[CLIENT] Average jitter: {avg_jitter:.2f}ms")
                
                print(f"[CLIENT] GAME OVER! Winner: {winner}")
                
        except socket.timeout:
            continue
        except Exception as e:
            if running:
                pass  
        
        time.sleep(0.001)

def main():
    global running, game_over, winner
    
   
    pygame.init()
    screen = pygame.display.set_mode((WINDOW_SIZE, WINDOW_SIZE))
    pygame.display.set_caption("Grid Clash Client (NetRush Protocol with Interpolation)")
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 36)
    small_font = pygame.font.Font(None, 24)
    
    
    if not send_init():
        print("[CLIENT] Failed to initialize with server. Exiting.")
        pygame.quit()
        sys.exit(1)
    
  
    recv_thread = threading.Thread(target=receive_updates, daemon=True)
    recv_thread.start()
    
    print(f"[CLIENT] Connected as Player {client_id}")
    print(f"[CLIENT] Interpolation delay: {INTERPOLATION_DELAY_MS}ms")
    
    # Main game loop
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            
            elif event.type == pygame.MOUSEBUTTONDOWN and not game_over:
                # Handle cell clicks
                mouse_x, mouse_y = pygame.mouse.get_pos()
                col = mouse_x // CELL_SIZE
                row = mouse_y // CELL_SIZE
                
                if 0 <= row < GRID_N and 0 <= col < GRID_N:
                    cell_index = row * GRID_N + col
                    
                    # Use current grid (not interpolated) for click detection
                    with lock:
                        if grid[row][col] == 'UNCLAIMED':
                            send_acquire(cell_index)
        
        # Draw
        screen.fill(COLORS['background'])
        
        # Get interpolated grid for smooth display
        with lock:
            display_grid = get_interpolated_grid()
            current_game_over = game_over
            current_winner = winner
        
        # Draw cells
        for r in range(GRID_N):
            for c in range(GRID_N):
                owner = display_grid[r][c]
                color = get_player_color(owner)
                
                rect = pygame.Rect(c * CELL_SIZE, r * CELL_SIZE, CELL_SIZE, CELL_SIZE)
                pygame.draw.rect(screen, color, rect)
                pygame.draw.rect(screen, COLORS['grid_line'], rect, 2)
                
                # Draw owner text
                if owner != 'UNCLAIMED':
                    text = font.render(str(owner), True, COLORS['text'])
                    text_rect = text.get_rect(center=rect.center)
                    screen.blit(text, text_rect)
        
        # Draw interpolation indicator
        info_text = small_font.render(f"Player {client_id} | Delay: {INTERPOLATION_DELAY_MS}ms", True, COLORS['text'])
        screen.blit(info_text, (5, WINDOW_SIZE - 25))
        
        # Draw game over message
        if current_game_over:
            overlay = pygame.Surface((WINDOW_SIZE, WINDOW_SIZE))
            overlay.set_alpha(200)
            overlay.fill((0, 0, 0))
            screen.blit(overlay, (0, 0))
            
            game_over_text = font.render("GAME OVER!", True, (255, 255, 0))
            game_over_rect = game_over_text.get_rect(center=(WINDOW_SIZE // 2, WINDOW_SIZE // 2 - 30))
            screen.blit(game_over_text, game_over_rect)
            
            winner_text = font.render(f"Winner: Player {current_winner}", True, (255, 255, 255))
            winner_rect = winner_text.get_rect(center=(WINDOW_SIZE // 2, WINDOW_SIZE // 2 + 30))
            screen.blit(winner_text, winner_rect)
        
        pygame.display.flip()
        clock.tick(60)
    
    # Cleanup
    if client_log_file:
        client_log_file.close()
    pygame.quit()
    sock.close()
    print("[CLIENT] Shutting down")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[CLIENT] Interrupted by user")
        running = False