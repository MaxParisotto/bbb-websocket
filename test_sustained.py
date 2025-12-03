#!/usr/bin/env python3
"""Test sustained motor control via WebSocket"""

import asyncio
import json
import websockets

async def test_motors():
    uri = "ws://192.168.2.212:8001/ws/control"
    
    print("Connecting to BBB...")
    async with websockets.connect(uri) as ws:
        print("Connected!")
        
        # Send forward command at 10Hz for 3 seconds
        print("Sending forward commands for 3 seconds...")
        for i in range(30):  # 30 commands at 10Hz = 3 seconds
            cmd = {"type": "mecanum", "vx": 0.4, "vy": 0.0, "omega": 0.0}
            await ws.send(json.dumps(cmd))
            response = await ws.recv()
            resp = json.loads(response)
            if i == 0:
                print(f"First response: {resp}")
            await asyncio.sleep(0.1)
        
        # Stop
        print("Stopping motors...")
        await ws.send(json.dumps({"type": "stop"}))
        response = await ws.recv()
        print(f"Stop response: {response}")
        
    print("Done!")

if __name__ == "__main__":
    asyncio.run(test_motors())
