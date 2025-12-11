#!/usr/bin/env python3
import socket, threading, json, time, csv
from collections import deque
import pygame
import random

SERVER_IP = "127.0.0.1"
SERVER_PORT = 5000
GRID_N = 5
CELL_SIZE = 100
INTERP_DELAY_MS = 100
MAX_BUFFERED = 200
CLIENT_LOG_TEMPLATE = "client_{}_log.csv"
HEARTBEAT_INTERVAL = 1
RDT_TIMEOUT = 0.5
MAX_RETRIES = 3

CLIENT_ID = None
SERVER_ADDR = (SERVER_IP, SERVER_PORT)
event_queue = {}

reconstructed_grid = [['UNCLAIMED' for _ in range(GRID_N)] for _ in range(GRID_N)]
buffered_snapshots = deque(maxlen=MAX_BUFFERED)
lock = threading.Lock()
last_applied_id = -1

log_file = None
writer = None
last_recv_time = None
jitter = 0.0

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 0))
sock.setblocking(True)

client_colors = {}
BASE_COLORS = [(255,0,0),(0,200,0),(0,0,255),(255,255,0),(255,0,255),(0,255,255)]

def get_color(client_id):
    if client_id=='UNCLAIMED':
        return (200,200,200)
    if client_id not in client_colors:
        if client_id <= len(BASE_COLORS):
            client_colors[client_id] = BASE_COLORS[client_id-1]
        else:
            client_colors[client_id] = (random.randint(50,255),random.randint(50,255),random.randint(50,255))
    return client_colors[client_id]

def send_init():
    global CLIENT_ID
    while CLIENT_ID is None:
        sock.sendto(json.dumps({'type':'Init'}).encode(), SERVER_ADDR)
        try:
            data,_ = sock.recvfrom(65536)
            resp = json.loads(data.decode())
            if resp.get('type')=='Init_ACK':
                CLIENT_ID = resp['client_id']
                print(f"[Client] Assigned ID {CLIENT_ID}")
        except:
            time.sleep(1)

def send_heartbeat():
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        if CLIENT_ID:
            msg={'type':'HEARTBEAT','client_id':CLIENT_ID,'timestamp':time.time()}
            sock.sendto(json.dumps(msg).encode(), SERVER_ADDR)

def send_event(row,col):
    cell_id=row*GRID_N+col
    msg={'type':'Event','cell':cell_id,'timestamp':time.time(),'client_id':CLIENT_ID}
    event_queue[cell_id]={'msg':msg,'last_sent':0,'retries':0}

def retransmit_loop():
    while True:
        time.sleep(0.05)
        now=time.time()
        for cell_id,data in list(event_queue.items()):
            if now - data['last_sent']>RDT_TIMEOUT:
                if data['retries']>=MAX_RETRIES:
                    del event_queue[cell_id]
                    continue
                try:
                    sock.sendto(json.dumps(data['msg']).encode(), SERVER_ADDR)
                    data['last_sent']=now
                    data['retries']+=1
                except:
                    pass

def apply_changes_to_grid(grid, changes):
    for r,c,owner in changes:
        grid[r][c]=owner

def listener_loop():
    global last_recv_time,jitter
    while True:
        try:
            data,_=sock.recvfrom(65536)
            recv_time_ms=int(time.time()*1000)
            pkt=json.loads(data.decode())
        except:
            continue
        if pkt.get('type')=='ACK':
            cell_id=pkt.get('cell')
            if cell_id in event_queue: del event_queue[cell_id]
            continue
        sid=pkt.get('snapshot_id')
        server_ts=pkt.get('server_timestamp_ms',recv_time_ms)
        if last_recv_time:
            inter=recv_time_ms-last_recv_time
            jitter=0.9*jitter+0.1*abs(inter-(jitter if jitter else inter))
        last_recv_time=recv_time_ms
        with lock:
            buffered_snapshots.append(pkt)

def reconstruct_state_for_playback(playback_time_ms):
    with lock: buf=list(buffered_snapshots)
    if not buf: return None
    chosen_idx=max([i for i in range(len(buf)) if buf[i].get('server_timestamp_ms',0)<=playback_time_ms], default=0)
    start_idx=max([i for i in range(chosen_idx+1) if buf[i].get('full')], default=0)
    grid_state=[row[:] for row in buf[start_idx].get('grid',[['UNCLAIMED']*GRID_N for _ in range(GRID_N)])]
    for i in range(start_idx+1,chosen_idx+1):
        apply_changes_to_grid(grid_state, buf[i].get('changes',[]))
    return grid_state, buf[chosen_idx].get('snapshot_id'), buf[chosen_idx].get('server_timestamp_ms')

# --- Main execution ---
pygame.init()
screen=pygame.display.set_mode((GRID_N*CELL_SIZE, GRID_N*CELL_SIZE))
pygame.display.set_caption("Grid Clash")
font=pygame.font.SysFont(None,36)

threading.Thread(target=send_init,daemon=True).start()
while CLIENT_ID is None: time.sleep(0.1)

log_file=open(CLIENT_LOG_TEMPLATE.format(CLIENT_ID),"w",newline="")
writer=csv.DictWriter(log_file,fieldnames=['client_id','snapshot_id','server_timestamp_ms','recv_time_ms','latency_ms'])
writer.writeheader()

threading.Thread(target=listener_loop,daemon=True).start()
threading.Thread(target=send_heartbeat,daemon=True).start()
threading.Thread(target=retransmit_loop,daemon=True).start()

clock=pygame.time.Clock()
running=True
game_over=False
winner_ids=None

while running:
    now_ms=int(time.time()*1000)
    playback_ms=now_ms-INTERP_DELAY_MS
    reconstructed=reconstruct_state_for_playback(playback_ms)
    if reconstructed:
        grid_state,sid,s_ts=reconstructed
        with lock: reconstructed_grid=grid_state
        writer.writerow({'client_id':CLIENT_ID,'snapshot_id':sid,'server_timestamp_ms':s_ts,'recv_time_ms':now_ms,'latency_ms':now_ms-s_ts})
        log_file.flush()

    for pkt in list(buffered_snapshots):
        if pkt.get('type')=='GAME_OVER' and not game_over:
            game_over=True
            winner_ids=pkt.get('winner')  # list of winner IDs

    for ev in pygame.event.get():
        if ev.type==pygame.QUIT: running=False
        elif ev.type==pygame.MOUSEBUTTONDOWN and ev.button==1:
            x,y=ev.pos
            row,col=y//CELL_SIZE,x//CELL_SIZE
            if not game_over: send_event(row,col)

    screen.fill((0,0,0))
    with lock:
        for r in range(GRID_N):
            for c in range(GRID_N):
                owner=reconstructed_grid[r][c]
                color=get_color(owner)
                pygame.draw.rect(screen,color,(c*CELL_SIZE,r*CELL_SIZE,CELL_SIZE-1,CELL_SIZE-1))

    if game_over:
        screen.fill((0,0,0))
        if not winner_ids:
            text="No winner"
            color=(255,255,255)
        elif len(winner_ids)==1:
            text=f"Player {winner_ids[0]} Wins!"
            color=get_color(winner_ids[0])
        else:
            text=f"Tie: Players {', '.join(map(str,winner_ids))}!"
            color=(255,255,255)
        winner_text=font.render(text,True,color)
        rect=winner_text.get_rect(center=(GRID_N*CELL_SIZE//2, GRID_N*CELL_SIZE//2))
        screen.blit(winner_text,rect)

    pygame.display.flip()
    clock.tick(60)

pygame.quit()
log_file.close()
sock.close()
