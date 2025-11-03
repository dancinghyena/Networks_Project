import socket

server_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

server_sock.bind(("127.0.0.1", 9999))

print("server running")

while True:

 data, addr = server_sock.recvfrom(1024)
 
 print(f"Received '{data.decode()}' from {addr}")
 
 server_sock.sendto(b"Message received!", addr)