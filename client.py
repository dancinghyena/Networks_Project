#!/usr/bin/env python3
"""
client.py
UDP client for Grid Clash with INIT/ACK handshake and retransmission.
- Sends INIT on startup, retries until ACK received
- Receives assigned ID and initial grid state from server
- Sends ACQUIRE_REQUEST for cell clicks
- Receives and applies SNAPSHOT updates
- Handles GAME_OVER message
"""

import socket
import threading
import json
import time
import pygame
import sys
from copy import deepcopy

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

# Client state
client_id = None
grid = [['UNCLAIMED' for _ in range(GRID_N)] for _ in range(GRID_N)]
last_snapshot_id = -1
lock = threading.Lock()
running = True
game_over = False
winner = None

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

def get_player_color(player_id):
    """Generate a consistent color for each player ID"""
    if player_id == 'UNCLAIMED':
        return COLORS['UNCLAIMED']
    
    # Hash the player_id to get consistent colors
    hash_val = hash(str(player_id))
    r = (hash_val & 0xFF0000) >> 16
    g = (hash_val & 0x00FF00) >> 8
    b = (hash_val & 0x0000FF)
    
    # Ensure colors are bright enough
    r = max(100, r)
    g = max(100, g)
    b = max(100, b)
    
    return (r, g, b)

def send_init():
    """Send INIT message and wait for ACK with retransmission"""
    global client_id, grid
    
    print("[CLIENT] Sending INIT to server...")
    
    for attempt in range(MAX_INIT_RETRIES):
        init_msg = {
            'type': 'INIT',
            'timestamp': time.time()
        }
        
        try:
            sock.sendto(json.dumps(init_msg).encode(), (SERVER_HOST, SERVER_PORT))
            print(f"[CLIENT] INIT sent (attempt {attempt + 1}/{MAX_INIT_RETRIES})")
        except Exception as e:
            print(f"[CLIENT] Failed to send INIT: {e}")
            continue
        
        # Wait for ACK
        start_time = time.time()
        while time.time() - start_time < INIT_TIMEOUT:
            try:
                data, addr = sock.recvfrom(65536)
                msg = json.loads(data.decode())
                
                if msg.get('type') == 'ACK':
                    with lock:
                        client_id = msg.get('assigned_id')
                        received_grid = msg.get('grid')
                        if received_grid:
                            grid = deepcopy(received_grid)
                    
                    print(f"[CLIENT] ACK received! Assigned ID: {client_id}")
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
    
    try:
        sock.sendto(json.dumps(msg).encode(), (SERVER_HOST, SERVER_PORT))
    except Exception as e:
        print(f"[CLIENT] Failed to send ACQUIRE_REQUEST: {e}")

def receive_updates():
    """Background thread to receive SNAPSHOT and GAME_OVER messages"""
    global grid, last_snapshot_id, running, game_over, winner
    
    print("[CLIENT] Receive thread started")
    
    while running:
        try:
            data, addr = sock.recvfrom(65536)
            msg = json.loads(data.decode())
            msg_type = msg.get('type')
            
            if msg_type == 'SNAPSHOT':
                snapshot_id = msg.get('snapshot_id', -1)
                
                with lock:
                    if snapshot_id > last_snapshot_id:
                        # Apply full grid if present
                        if msg.get('full') and 'grid' in msg:
                            grid = deepcopy(msg['grid'])
                        
                        # Apply changes
                        changes = msg.get('changes', [])
                        for change in changes:
                            r, c, owner = change
                            if 0 <= r < GRID_N and 0 <= c < GRID_N:
                                grid[r][c] = owner
                        
                        last_snapshot_id = snapshot_id
            
            elif msg_type == 'GAME_OVER':
                with lock:
                    game_over = True
                    winner = msg.get('winner')
                    final_grid = msg.get('final_grid')
                    if final_grid:
                        grid = deepcopy(final_grid)
                
                print(f"[CLIENT] GAME OVER! Winner: {winner}")
                
        except socket.timeout:
            continue
        except Exception as e:
            if running:
                pass  # Suppress errors when shutting down
        
        time.sleep(0.001)

def main():
    global running, game_over, winner
    
    # Initialize Pygame
    pygame.init()
    screen = pygame.display.set_mode((WINDOW_SIZE, WINDOW_SIZE))
    pygame.display.set_caption("Grid Clash Client")
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 36)
    
    # Send INIT and wait for ACK
    if not send_init():
        print("[CLIENT] Failed to initialize with server. Exiting.")
        pygame.quit()
        sys.exit(1)
    
    # Start receive thread
    recv_thread = threading.Thread(target=receive_updates, daemon=True)
    recv_thread.start()
    
    print(f"[CLIENT] Connected as Player {client_id}")
    
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
                    
                    with lock:
                        if grid[row][col] == 'UNCLAIMED':
                            send_acquire(cell_index)
        
        # Draw
        screen.fill(COLORS['background'])
        
        with lock:
            current_grid = deepcopy(grid)
            current_game_over = game_over
            current_winner = winner
        
        # Draw cells
        for r in range(GRID_N):
            for c in range(GRID_N):
                owner = current_grid[r][c]
                color = get_player_color(owner)
                
                rect = pygame.Rect(c * CELL_SIZE, r * CELL_SIZE, CELL_SIZE, CELL_SIZE)
                pygame.draw.rect(screen, color, rect)
                pygame.draw.rect(screen, COLORS['grid_line'], rect, 2)
                
                # Draw owner text
                if owner != 'UNCLAIMED':
                    text = font.render(str(owner), True, COLORS['text'])
                    text_rect = text.get_rect(center=rect.center)
                    screen.blit(text, text_rect)
        
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
    pygame.quit()
    sock.close()
    print("[CLIENT] Shutting down")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[CLIENT] Interrupted by user")
        running = False