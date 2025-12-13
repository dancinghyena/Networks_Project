#!/usr/bin/env python3
import socket
import threading
import json
import time
import csv
import zlib
import os
from collections import deque

import pygame

# -----------------------
# Config
# -----------------------
SERVER_IP = "127.0.0.1"
SERVER_PORT = int(input("Enter server port: ").strip())
SERVER_ADDR = (SERVER_IP, SERVER_PORT)

GRID_N = 5
CELL_SIZE = 100

INTERP_DELAY_MS = 100
MAX_BUFFERED = 200

CLIENT_LOG_TEMPLATE = os.path.join("tests", "results", "baseline", "client_{}_log.csv")

HEARTBEAT_INTERVAL = 1.0

RDT_TIMEOUT = 0.5
MAX_RETRIES = 3

FREEZE_GAP_MS = 300
REINIT_GAP_MS = 2500

INIT_RETRY_SEC = 0.5
INIT_TOTAL_TIMEOUT_SEC = 10.0  # IMPORTANT: no infinite hangs

# -----------------------
# State
# -----------------------
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setblocking(False)  # never block the UI thread

CLIENT_ID = None
lock = threading.Lock()

buffered_snapshots = deque(maxlen=MAX_BUFFERED)
event_queue = {}
reconstructed_grid = [["UNCLAIMED" for _ in range(GRID_N)] for _ in range(GRID_N)]

last_snapshot_id_received = -1
total_snapshots = 0
lost_snapshots = 0
duplicate_or_old = 0
bad_checksum = 0

freeze_mode = False
last_snapshot_arrival_ms = None

last_recv_time_ms = None
jitter = 0.0


# -----------------------
# Helpers
# -----------------------
def stable_json_bytes(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def verify_checksum(pkt: dict) -> bool:
    if "checksum" not in pkt:
        return True
    chk = pkt["checksum"]
    tmp = dict(pkt)
    tmp.pop("checksum", None)
    raw = stable_json_bytes(tmp)
    return (zlib.crc32(raw) & 0xFFFFFFFF) == chk


def cell_to_rc(cell: int):
    return (cell // GRID_N, cell % GRID_N)


def apply_changes_to_grid(g, changes):
    for ch in changes:
        cell = int(ch["cell"])
        val = ch["value"]
        r, c = cell_to_rc(cell)
        if 0 <= r < GRID_N and 0 <= c < GRID_N:
            g[r][c] = val


def get_color(owner):
    if owner == "UNCLAIMED":
        return (35, 35, 35)
    try:
        cid = int(owner)
    except Exception:
        return (200, 200, 200)
    r = (cid * 97) % 200 + 55
    g = (cid * 57) % 200 + 55
    b = (cid * 37) % 200 + 55
    return (r, g, b)


def request_resync():
    try:
        sock.sendto(stable_json_bytes({"type": "RESYNC_REQ", "client_id": CLIENT_ID}), SERVER_ADDR)
    except Exception:
        pass


def poll_recv_json():
    try:
        data, _ = sock.recvfrom(65536)
    except BlockingIOError:
        return None
    except OSError:
        return None

    try:
        return json.loads(data.decode(errors="ignore"))
    except Exception:
        return None


# -----------------------
# Threads
# -----------------------
def send_heartbeat():
    while True:
        if CLIENT_ID is not None:
            try:
                sock.sendto(stable_json_bytes({"type": "HEARTBEAT", "client_id": CLIENT_ID}), SERVER_ADDR)
            except Exception:
                pass
        time.sleep(HEARTBEAT_INTERVAL)


def retransmit_loop():
    while True:
        time.sleep(RDT_TIMEOUT)
        now = time.time()
        resend = []
        with lock:
            for cell, info in list(event_queue.items()):
                if now - info["sent_time"] >= RDT_TIMEOUT:
                    if info["retries"] >= MAX_RETRIES:
                        del event_queue[cell]
                    else:
                        info["retries"] += 1
                        info["sent_time"] = now
                        resend.append(cell)

        for cell in resend:
            try:
                sock.sendto(stable_json_bytes({"type": "Event", "client_id": CLIENT_ID, "cell": cell}), SERVER_ADDR)
            except Exception:
                pass


def listener_loop():
    global last_snapshot_id_received, total_snapshots, lost_snapshots, duplicate_or_old, bad_checksum
    global last_snapshot_arrival_ms, last_recv_time_ms, jitter

    while True:
        pkt = poll_recv_json()
        if pkt is None:
            time.sleep(0.005)
            continue

        recv_ms = int(time.time() * 1000)

        if pkt.get("type") == "ACK":
            try:
                cell = int(pkt.get("cell"))
            except Exception:
                continue
            with lock:
                event_queue.pop(cell, None)
            continue

        if pkt.get("type") == "GAME_OVER":
            with lock:
                buffered_snapshots.append(pkt)
            continue

        if pkt.get("type") == "SNAPSHOT" or "snapshot_id" in pkt:
            if not verify_checksum(pkt):
                bad_checksum += 1
                request_resync()
                continue

            if last_recv_time_ms is not None:
                inter = recv_ms - last_recv_time_ms
                jitter = 0.9 * jitter + 0.1 * abs(inter - (jitter if jitter else inter))
            last_recv_time_ms = recv_ms

            try:
                sid = int(pkt.get("snapshot_id", -1))
            except Exception:
                continue

            total_snapshots += 1

            if sid <= last_snapshot_id_received:
                duplicate_or_old += 1
                continue

            if last_snapshot_id_received != -1 and sid > last_snapshot_id_received + 1:
                lost_snapshots += (sid - last_snapshot_id_received - 1)

            last_snapshot_id_received = sid
            last_snapshot_arrival_ms = recv_ms

            with lock:
                buffered_snapshots.append(pkt)
            continue


def reconstruct_state_for_playback(playback_time_ms: int):
    with lock:
        snaps = [
            p for p in buffered_snapshots
            if isinstance(p, dict) and ("snapshot_id" in p) and (p.get("type") != "GAME_OVER")
        ]

    if not snaps:
        return None

    chosen_idx = 0
    for i in range(len(snaps)):
        if snaps[i].get("server_timestamp_ms", 0) <= playback_time_ms:
            chosen_idx = i

    start_idx = 0
    for i in range(chosen_idx, -1, -1):
        if snaps[i].get("full"):
            start_idx = i
            break

    base_grid = snaps[start_idx].get("grid")
    if not base_grid:
        base_grid = [["UNCLAIMED" for _ in range(GRID_N)] for _ in range(GRID_N)]

    grid_state = [row[:] for row in base_grid]

    for i in range(start_idx + 1, chosen_idx + 1):
        apply_changes_to_grid(grid_state, snaps[i].get("changes", []))

    sid = snaps[chosen_idx].get("snapshot_id", -1)
    s_ts = snaps[chosen_idx].get("server_timestamp_ms", 0)
    return grid_state, sid, s_ts


# -----------------------
# Main (pygame)
# -----------------------
def main():
    global CLIENT_ID, freeze_mode, reconstructed_grid, last_snapshot_arrival_ms

    pygame.init()
    screen = pygame.display.set_mode((GRID_N * CELL_SIZE, GRID_N * CELL_SIZE))
    pygame.display.set_caption("Grid Clash")
    font = pygame.font.SysFont(None, 36)
    clock = pygame.time.Clock()

    # --- handshake WITH UI alive ---
    start_time = time.time()
    last_send = 0.0

    while CLIENT_ID is None:
        # keep window responsive
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit()
                return

        now = time.time()
        if now - start_time > INIT_TOTAL_TIMEOUT_SEC:
            pygame.quit()
            raise RuntimeError(
                f"INIT timeout after {INIT_TOTAL_TIMEOUT_SEC}s. "
                f"Check: server running? correct port? Windows firewall?"
            )

        if now - last_send >= INIT_RETRY_SEC:
            try:
                sock.sendto(stable_json_bytes({"type": "Init"}), SERVER_ADDR)
            except Exception:
                pass
            last_send = now

        pkt = poll_recv_json()
        if pkt and pkt.get("type") == "Init_ACK":
            CLIENT_ID = pkt["client_id"]
            break

        # draw connecting screen
        screen.fill((20, 20, 20))
        text = font.render("Connecting...", True, (255, 255, 255))
        screen.blit(text, (10, 10))
        pygame.display.flip()
        clock.tick(60)

    print(f"[Client] Assigned ID {CLIENT_ID}")

    # logging
    os.makedirs(os.path.join("tests", "results", "baseline"), exist_ok=True)
    log_path = CLIENT_LOG_TEMPLATE.format(CLIENT_ID)
    log_file = open(log_path, "w", newline="")
    writer = csv.DictWriter(
        log_file,
        fieldnames=[
            "client_id",
            "snapshot_id",
            "server_timestamp_ms",
            "recv_time_ms",
            "latency_ms",
            "total_snapshots",
            "lost_snapshots",
            "loss_rate",
            "duplicate_or_old",
            "bad_checksum",
            "jitter",
            "freeze_mode",
        ],
    )
    writer.writeheader()
    log_file.flush()

    # start threads
    threading.Thread(target=listener_loop, daemon=True).start()
    threading.Thread(target=send_heartbeat, daemon=True).start()
    threading.Thread(target=retransmit_loop, daemon=True).start()

    running = True
    game_over = False
    winner_ids = None

    while running:
        now_ms = int(time.time() * 1000)

        if last_snapshot_arrival_ms is not None and (now_ms - last_snapshot_arrival_ms) > FREEZE_GAP_MS:
            freeze_mode = True
            request_resync()
        else:
            freeze_mode = False

        if last_snapshot_arrival_ms is not None and (now_ms - last_snapshot_arrival_ms) > REINIT_GAP_MS:
            try:
                sock.sendto(stable_json_bytes({"type": "Init"}), SERVER_ADDR)
            except Exception:
                pass

        if not freeze_mode:
            playback_ms = now_ms - INTERP_DELAY_MS
            reconstructed = reconstruct_state_for_playback(playback_ms)
            if reconstructed:
                grid_state, sid, s_ts = reconstructed
                with lock:
                    reconstructed_grid = grid_state

                loss_rate = (lost_snapshots / total_snapshots) if total_snapshots else 0.0
                writer.writerow(
                    {
                        "client_id": CLIENT_ID,
                        "snapshot_id": sid,
                        "server_timestamp_ms": s_ts,
                        "recv_time_ms": now_ms,
                        "latency_ms": now_ms - s_ts,
                        "total_snapshots": total_snapshots,
                        "lost_snapshots": lost_snapshots,
                        "loss_rate": loss_rate,
                        "duplicate_or_old": duplicate_or_old,
                        "bad_checksum": bad_checksum,
                        "jitter": jitter,
                        "freeze_mode": int(freeze_mode),
                    }
                )
                log_file.flush()

        with lock:
            for pkt in list(buffered_snapshots):
                if isinstance(pkt, dict) and pkt.get("type") == "GAME_OVER" and not game_over:
                    game_over = True
                    winner_ids = pkt.get("winner")

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.MOUSEBUTTONDOWN and not game_over:
                x, y = pygame.mouse.get_pos()
                c = x // CELL_SIZE
                r = y // CELL_SIZE
                cell = r * GRID_N + c
                with lock:
                    if cell not in event_queue:
                        event_queue[cell] = {"sent_time": time.time(), "retries": 0}
                        try:
                            sock.sendto(stable_json_bytes({"type": "Event", "client_id": CLIENT_ID, "cell": cell}), SERVER_ADDR)
                        except Exception:
                            pass

        if not game_over:
            screen.fill((0, 0, 0))
            with lock:
                for r in range(GRID_N):
                    for c in range(GRID_N):
                        owner = reconstructed_grid[r][c]
                        color = get_color(owner)
                        pygame.draw.rect(screen, color, (c * CELL_SIZE, r * CELL_SIZE, CELL_SIZE - 1, CELL_SIZE - 1))

            if freeze_mode:
                overlay = font.render("FREEZE (resyncing...)", True, (255, 255, 255))
                screen.blit(overlay, (10, 10))
        else:
            screen.fill((0, 0, 0))
            if not winner_ids:
                text = "No winner"
                color = (255, 255, 255)
            elif len(winner_ids) == 1:
                text = f"Player {winner_ids[0]} Wins!"
                color = get_color(str(winner_ids[0]))
            else:
                text = f"Tie: Players {', '.join(map(str, winner_ids))}"
                color = (255, 255, 255)

            winner_text = font.render(text, True, color)
            rect = winner_text.get_rect(center=(GRID_N * CELL_SIZE // 2, GRID_N * CELL_SIZE // 2))
            screen.blit(winner_text, rect)

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()
    log_file.close()
    sock.close()


if __name__ == "__main__":
    main()
