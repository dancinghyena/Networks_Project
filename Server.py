import socket
import struct
import time
import zlib

SERVER_IP = "127.0.0.1"
SERVER_PORT = 5005

# Message types
MSG_INIT = 0
MSG_DATA = 1
MSG_ACK = 3

# Header format (same as Mini-RFC)
# '!4s B B I I Q H I' = protocol_id(4s), version(B), msg_type(B), snapshot_id(I),
# seq_num(I), timestamp(Q), payload_len(H), checksum(I)
HEADER_FORMAT = "!4s B B I I Q H I"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


def calculate_checksum(data: bytes) -> int:
    return zlib.crc32(data) & 0xffffffff


def unpack_header(packet: bytes):
    header = packet[:HEADER_SIZE]
    fields = struct.unpack(HEADER_FORMAT, header)
    return {
        "protocol_id": fields[0].decode(),
        "version": fields[1],
        "msg_type": fields[2],
        "snapshot_id": fields[3],
        "seq_num": fields[4],
        "timestamp": fields[5],
        "payload_len": fields[6],
        "checksum": fields[7],
        "payload": packet[HEADER_SIZE:],
    }


def send_ack(sock, addr, seq_num):
    header = struct.pack(
        HEADER_FORMAT,
        b"NRSH",
        1,
        MSG_ACK,
        0,
        seq_num,
        int(time.time() * 1000),
        0,
        0,
    )
    sock.sendto(header, addr)


def main():
    print(f"[Server] Starting NetRush server on {SERVER_IP}:{SERVER_PORT}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((SERVER_IP, SERVER_PORT))

    while True:
        data, addr = sock.recvfrom(1024)
        msg = unpack_header(data)

        if msg["msg_type"] == MSG_INIT:
            print(f"[Server] INIT received from {addr}")
            send_ack(sock, addr, msg["seq_num"])

        elif msg["msg_type"] == MSG_DATA:
            payload = msg["payload"].decode(errors="ignore")
            print(f"[Server] DATA from {addr}: {payload}")
            send_ack(sock, addr, msg["seq_num"])


if __name__ == "__main__":
    main()
