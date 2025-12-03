#!/usr/bin/env python3
"""Test motor control via WebSocket"""

import websocket
import json
import time

def test_motors():
    ws = websocket.WebSocket()
    ws.settimeout(5)
    
    print("Connecting to BBB...")
    ws.connect('ws://192.168.2.212:8001/ws/control')
    print('Connected!')

    # Send forward command
    cmd = {'type': 'mecanum', 'vx': 0.5, 'vy': 0.0, 'omega': 0.0}
    ws.send(json.dumps(cmd))
    print(f'Sent: {cmd}')

    response = ws.recv()
    print(f'Response: {response}')

    # Hold for 2 seconds
    print("Waiting 2 seconds...")
    time.sleep(2)

    # Stop
    stop_cmd = {'type': 'stop'}
    ws.send(json.dumps(stop_cmd))
    print(f'Sent: {stop_cmd}')

    response = ws.recv()
    print(f'Response: {response}')

    ws.close()
    print('Done')

if __name__ == "__main__":
    test_motors()
