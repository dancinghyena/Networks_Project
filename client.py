import socket
import struct
import time
import zlib
import threading

SERVER_IP = "127.0.0.1"
SERVER_PORT = 5005

MSG_INIT = 0
MSG_DATA = 1
MSG_ACK = 3

# Full header format (8 fields)
HEADER_FORMAT = "!4s B B I I Q H I"
# Header format without the final checksum field (7 fields)
HEADER_FORMAT_NO_CHECKSUM = "!4s B B I I Q H"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


def calculate_checksum(data: bytes) -> int:
    return zlib.crc32(data) & 0xffffffff


def build_packet(msg_type, seq_num, payload=b""):
    payload_len = len(payload)
    # Use the same timestamp for checksum and for the header
    ts = int(time.time() * 1000)

    # Pack header without checksum
    header_wo_checksum = struct.pack(
        HEADER_FORMAT_NO_CHECKSUM,
        b"NRSH",   # protocol id (4 bytes)
        1,         # version (1 byte)
        msg_type,  # msg type (1 byte)
        0,         # snapshot_id (4 bytes) - not used here
        seq_num,   # seq_num (4 bytes)
        ts,        # timestamp (8 bytes)
        payload_len,  # payload length (2 bytes)
    )

    # Compute checksum over header_wo_checksum + payload
    checksum = calculate_checksum(header_wo_checksum + payload)

    # Pack full header including checksum
    header = struct.pack(
        HEADER_FORMAT,
        b"NRSH",
        1,
        msg_type,
        0,
        seq_num,
        ts,
        payload_len,
        checksum,
    )
    return header + payload


def listen_for_acks(sock):
    while True:
        try:
            data, _ = sock.recvfrom(4096)
            if len(data) < HEADER_SIZE:
                # ignore too-small packets
                continue
            # unpack header properly
            fields = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
            protocol_id = fields[0].decode(errors="ignore")
            version = fields[1]
            msg_type = fields[2]
            ack_seq = fields[4]

            if msg_type == MSG_ACK:
                print(f"[Client] ACK received for seq {ack_seq}")
        except Exception as e:
            # likely socket closed or interrupted — stop listener
            # print(f"[Client] listener exiting: {e}")
            break


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq_num = 0

    # Send INIT packet
    init_payload = b"PlayerName:A1"
    packet = build_packet(MSG_INIT, seq_num, init_payload)
    sock.sendto(packet, (SERVER_IP, SERVER_PORT))
    print("[Client] Sent INIT")

    # Start listener thread
    threading.Thread(target=listen_for_acks, args=(sock,), daemon=True).start()

    # Send some DATA packets (simulate player movement)
    for i in range(5):
        seq_num += 1
        payload = f"Action:MOVE_UP | Step:{i}".encode()
        packet = build_packet(MSG_DATA, seq_num, payload)
        sock.sendto(packet, (SERVER_IP, SERVER_PORT))
        print(f"[Client] Sent DATA packet #{seq_num}")
        time.sleep(0.5)

    # give some time to receive ACKs before exiting
    time.sleep(1.0)
    sock.close()


if __name__ == "__main__":
    main()
