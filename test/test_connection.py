import socket

HOST = "openapi.q-net.or.kr"

for port in [80, 443]:
    try:
        with socket.create_connection((HOST, port), timeout=5):
            print(f"포트 {port}: 연결 성공")
    except OSError as e:
        print(f"포트 {port}: 연결 실패 ({type(e).__name__})")
