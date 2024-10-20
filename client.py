# Filename: client.py
import asyncio
import websockets
import json
import requests
import time

# Azure REST endpoint (replace with your actual endpoint)
AZURE_REST_API_URL = "https://bbb-telemetry-apim.azure-api.net/api/processTelemetry"
# Optional: Azure API key if required
AZURE_API_KEY = "your-azure-api-key"

# Headers including the Azure API key for authentication
headers = {
    "Content-Type": "application/json",
    "Ocp-Apim-Subscription-Key": AZURE_API_KEY  # Change if using another header
}

async def collect_data_from_bbb():
    uri = "ws://192.168.2.241:8001/ws/data"
    async with websockets.connect(uri) as websocket:
        while True:
            try:
                # Receive data from BeagleBone Blue WebSocket server
                message = await websocket.recv()
                data = json.loads(message)
                print(f"Received data: {data}")
                
                # Forward data to Azure REST API with retries
                for attempt in range(3):  # Retry up to 3 times if sending fails
                    try:
                        response = requests.post(AZURE_REST_API_URL, headers=headers, json=data)
                        if response.status_code == 200:
                            print("Data successfully sent to Azure.")
                            break
                        else:
                            print(f"Failed to send data to Azure: {response.status_code}")
                    except requests.exceptions.RequestException as req_error:
                        print(f"Request Error: {req_error}")
                        if attempt < 2:  # Only sleep for retries, not the last attempt
                            time.sleep(2 ** attempt)  # Exponential backoff

            except websockets.exceptions.ConnectionClosedError:
                print("WebSocket connection closed unexpectedly. Attempting to reconnect...")
                await asyncio.sleep(5)  # Wait before attempting to reconnect
                break  # Exit loop and let the outer code retry connecting

            except Exception as e:
                print(f"Unexpected Error: {e}")
                break

if __name__ == "__main__":
    try:
        asyncio.run(collect_data_from_bbb())
    except KeyboardInterrupt:
        print("Client disconnected.")