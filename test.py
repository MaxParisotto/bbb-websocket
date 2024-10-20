import asyncio
import websockets
import json

# Define the BeagleBone Blue IP address and WebSocket endpoints
bbb_ip = "192.168.2.241"
ws_port = 8001

async def test_websocket(uri):
    try:
        async with websockets.connect(uri) as websocket:
            while True:
                message = await websocket.recv()
                data = json.loads(message)
                print(f"Received from {uri}: {data}")
    except Exception as e:
        print(f"Connection to {uri} failed: {e}")

async def main():
    # Define the WebSocket endpoints to connect to
    endpoints = {
        "IMU Data": f"ws://{bbb_ip}:{ws_port}/ws/imu",
        "Encoder Data": f"ws://{bbb_ip}:{ws_port}/ws/encoder",
        "Battery Monitoring": f"ws://{bbb_ip}:{ws_port}/ws/battery",
        "System Metrics": f"ws://{bbb_ip}:{ws_port}/ws/system_metrics",
    }

    # Create tasks to connect to each endpoint concurrently
    tasks = [test_websocket(uri) for name, uri in endpoints.items()]

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("WebSocket client interrupted. Exiting.")