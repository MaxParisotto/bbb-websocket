# Filename: client.py
import asyncio
import websockets
import json
import requests

# Azure REST endpoint (or use Azure IoT SDK as needed)
AZURE_REST_API_URL = "https://bbb-telemetry-apim.azure-api.net/processTelemetry"

async def collect_data_from_bbb():
    uri = "ws://192.168.2.241:8001/ws/data"
    async with websockets.connect(uri) as websocket:
        while True:
            try:
                # Receive data from BeagleBone Blue WebSocket server
                message = await websocket.recv()
                data = json.loads(message)
                print(f"Received data: {data}")
                
                # Forward data to Azure REST API
                response = requests.post(AZURE_REST_API_URL, json=data)
                if response.status_code == 200:
                    print("Data successfully sent to Azure.")
                else:
                    print(f"Failed to send data to Azure: {response.status_code}")

            except Exception as e:
                print(f"Error: {e}")
                break

if __name__ == "__main__":
    asyncio.run(collect_data_from_bbb())