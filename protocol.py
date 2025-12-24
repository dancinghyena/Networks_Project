#!/usr/bin/env python3
"""NetRush Protocol (NRSH) v1 - Binary Protocol with CRC32 Checksum"""
import struct, zlib, json, time
from enum import IntEnum

PROTOCOL_ID, VERSION, HEADER_SIZE = b"NRSH", 1, 28
MAX_PACKET, MAX_PAYLOAD = 1200, 1172
HEADER_FMT = "!4sBBIIQHI"

class MsgType(IntEnum):
    INIT = 0
    INIT_ACK = 1
    SNAPSHOT = 2
    EVENT = 3
    ACK = 4
    GAME_OVER = 5

def _checksum(hdr_no_csum: bytes, payload: bytes) -> int:
    return zlib.crc32(hdr_no_csum + payload) & 0xFFFFFFFF

def _pack_hdr(msg_type, snap_id, seq, ts_ms, payload):
    hdr_no_csum = struct.pack("!4sBBIIQH", PROTOCOL_ID, VERSION, int(msg_type), snap_id, seq, ts_ms, len(payload))
    return struct.pack(HEADER_FMT, PROTOCOL_ID, VERSION, int(msg_type), snap_id, seq, ts_ms, len(payload), _checksum(hdr_no_csum, payload))

def _unpack_hdr(data):
    if len(data) < HEADER_SIZE: return None
    try:
        pid, ver, mtype, snap_id, seq, ts, plen, csum = struct.unpack(HEADER_FMT, data[:HEADER_SIZE])
        if pid != PROTOCOL_ID or ver != VERSION: return None
        return {'msg_type': MsgType(mtype), 'snapshot_id': snap_id, 'seq_num': seq, 'timestamp_ms': ts, 'payload_len': plen, 'checksum': csum}
    except: return None

def _encode_grid(grid):
    """Encode only claimed cells as 'r,c,owner;...'"""
    return ";".join(f"{r},{c},{cell}" for r,row in enumerate(grid) for c,cell in enumerate(row) if cell != 'UNCLAIMED')

def _decode_grid(s, n):
    grid = [['UNCLAIMED']*n for _ in range(n)]
    if s:
        for part in s.split(";"):
            if part:
                p = part.split(",")
                if len(p) >= 3:
                    r, c = int(p[0]), int(p[1])
                    grid[r][c] = int(p[2]) if p[2].isdigit() else p[2]
    return grid

def pack_packet(msg_type, snap_id, seq, payload_dict, compress=False):
    """Create packet with binary header + JSON payload (optionally compressed)"""
    for key in ['grid', 'final_grid']:
        if key in payload_dict and payload_dict[key]:
            payload_dict = payload_dict.copy()
            payload_dict[key + '_enc'] = _encode_grid(payload_dict[key])
            del payload_dict[key]
    
    raw = json.dumps(payload_dict, separators=(',',':')).encode()
    if compress or len(raw) > MAX_PAYLOAD - 1:
        payload = b'\x01' + zlib.compress(raw, 6)
    else:
        payload = b'\x00' + raw
    
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"Payload too large: {len(payload)}")
    
    ts_ms = int(time.time() * 1000)
    return _pack_hdr(msg_type, snap_id, seq, ts_ms, payload) + payload

def unpack_packet(data, grid_n=20):
    """Unpack packet, validate checksum, decode payload"""
    hdr = _unpack_hdr(data)
    if not hdr: return None, None
    
    payload_bytes = data[HEADER_SIZE:HEADER_SIZE + hdr['payload_len']]
    if not payload_bytes: return hdr, {}
    
    hdr_no_csum = struct.pack("!4sBBIIQH", PROTOCOL_ID, VERSION, int(hdr['msg_type']), 
                              hdr['snapshot_id'], hdr['seq_num'], hdr['timestamp_ms'], hdr['payload_len'])
    if _checksum(hdr_no_csum, payload_bytes) != hdr['checksum']:
        return None, None
    
    try:
        raw = zlib.decompress(payload_bytes[1:]) if payload_bytes[0] == 1 else payload_bytes[1:]
        d = json.loads(raw.decode()) if raw else {}
        for key in ['grid', 'final_grid']:
            if key + '_enc' in d:
                d[key] = _decode_grid(d[key + '_enc'], grid_n)
                del d[key + '_enc']
        return hdr, d
    except: return hdr, {}

# Helper functions
def make_init(): return pack_packet(MsgType.INIT, 0, 0, {})
def make_init_ack(cid): return pack_packet(MsgType.INIT_ACK, 0, 0, {'client_id': cid})
def make_event(cell, cid, seq): return pack_packet(MsgType.EVENT, 0, seq, {'cell': cell, 'client_id': cid, 'ts': int(time.time()*1000)})
def make_ack(cell, owner, seq): return pack_packet(MsgType.ACK, 0, seq, {'cell': cell, 'owner': owner})
def make_snapshot(sid, grid, changes, full, redundant=None):
    p = {'full': full, 'changes': changes}
    if full and grid: p['grid'] = grid
    if redundant: p['redundant'] = redundant
    return pack_packet(MsgType.SNAPSHOT, sid, sid, p, compress=full)
def make_game_over(winners, grid): return pack_packet(MsgType.GAME_OVER, 0, 0, {'winner': winners, 'final_grid': grid}, compress=True)
