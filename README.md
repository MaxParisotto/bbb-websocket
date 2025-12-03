# BeagleBone Blue Rover Control System

A WebSocket-based control system for a 4-wheel mecanum rover built on BeagleBone Blue. Includes a robot control server, web dashboard, and real-time telemetry.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        BeagleBone Blue                          │
│  ┌─────────────────────┐    ┌─────────────────────────────────┐ │
│  │  bbb-server.py      │    │  dashboard-bbb.py               │ │
│  │  (Port 8001)        │◄───│  (Port 8080)                    │ │
│  │                     │    │                                 │ │
│  │  - Motor Control    │    │  - Web UI                       │ │
│  │  - IMU Sensors      │    │  - WebSocket Proxy              │ │
│  │  - Encoders         │    │  - Telemetry Display            │ │
│  │  - Battery Monitor  │    │                                 │ │
│  └─────────────────────┘    └─────────────────────────────────┘ │
│           │                              │                       │
│           ▼                              │                       │
│  ┌─────────────────────┐                 │                       │
│  │  librobotcontrol    │                 │                       │
│  │  (Hardware Access)  │                 │                       │
│  └─────────────────────┘                 │                       │
└─────────────────────────────────────────────────────────────────┘
                    │                      │
                    ▼                      ▼
            ┌─────────────────────────────────────┐
            │           Browser / Client          │
            │  http://192.168.2.212:8080          │
            └─────────────────────────────────────┘
```

## Hardware Setup

- **BeagleBone Blue** running Debian
- **4x DC Motors** with encoders (mecanum wheels)
  - Motor 1: Front Left (FL)
  - Motor 2: Front Right (FR)
  - Motor 3: Rear Right (RR)
  - Motor 4: Rear Left (RL)
- **2S LiPo Battery** for power
- **MPU9250 IMU** (built into BBB)

## Quick Start

### On the BeagleBone Blue

1. Clone the repository:
   ```bash
   cd /home/debian
   git clone https://github.com/MaxParisotto/bbb-websocket.git
   cd bbb-websocket
   ```

2. Install dependencies:
   ```bash
   pip3 install -r requirements.txt
   ```

3. Install and enable services:
   ```bash
   sudo cp bbb-dashboard.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable bbb-websocket bbb-dashboard
   sudo systemctl start bbb-websocket bbb-dashboard
   ```

4. Access the dashboard:
   ```
   http://<BBB_IP>:8080/
   ```

### Updating

```bash
cd /home/debian/bbb-websocket
git fetch && git reset --hard origin/main
sudo systemctl restart bbb-websocket bbb-dashboard
```

---

# API Documentation

## Robot Control Server (Port 8001)

The main robot control server provides REST and WebSocket APIs for controlling the rover and receiving telemetry.

### REST Endpoints

#### Health Check
```
GET /health
```

Returns the server status.

**Response:**
```json
{
  "status": "ok",
  "connections": 2,
  "emergency_stop": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Server status ("ok") |
| `connections` | integer | Number of active WebSocket connections |
| `emergency_stop` | boolean | Whether emergency stop is active |

---

#### Emergency Stop
```
POST /emergency_stop
```

Triggers an emergency stop, immediately halting all motors.

**Response:**
```json
{
  "status": "emergency_stop_activated"
}
```

---

#### Reset Emergency Stop
```
POST /reset_emergency_stop
```

Resets the emergency stop, allowing motor commands again.

**Response:**
```json
{
  "status": "emergency_stop_reset"
}
```

---

### WebSocket Endpoints

#### Control WebSocket
```
WS /ws/control
```

Bidirectional WebSocket for sending motor commands and receiving responses.

##### Command: Mecanum Drive
Controls the rover using mecanum wheel kinematics.

**Request:**
```json
{
  "type": "mecanum",
  "vx": 0.5,
  "vy": 0.0,
  "omega": 0.0
}
```

| Field | Type | Range | Description |
|-------|------|-------|-------------|
| `type` | string | - | Must be "mecanum" |
| `vx` | float | -1.0 to 1.0 | Forward/backward velocity (positive = forward) |
| `vy` | float | -1.0 to 1.0 | Left/right strafe velocity (positive = right) |
| `omega` | float | -1.0 to 1.0 | Rotational velocity (positive = clockwise) |

**Response:**
```json
{
  "type": "mecanum_response",
  "success": true,
  "input": {"vx": 0.5, "vy": 0.0, "omega": 0.0},
  "wheel_speeds": {
    "1": 0.5,
    "2": 0.5,
    "3": 0.5,
    "4": 0.5
  }
}
```

---

##### Command: Direct Motor Control
Control individual motors directly.

**Request:**
```json
{
  "type": "motor",
  "motor_1": 0.5,
  "motor_2": -0.5,
  "motor_3": 0.5,
  "motor_4": -0.5
}
```

| Field | Type | Range | Description |
|-------|------|-------|-------------|
| `type` | string | - | Must be "motor" |
| `motor_1` | float | -1.0 to 1.0 | Motor 1 speed (Front Left) |
| `motor_2` | float | -1.0 to 1.0 | Motor 2 speed (Front Right) |
| `motor_3` | float | -1.0 to 1.0 | Motor 3 speed (Rear Right) |
| `motor_4` | float | -1.0 to 1.0 | Motor 4 speed (Rear Left) |

**Response:**
```json
{
  "type": "motor_response",
  "success": true,
  "speeds": {
    "1": 0.5,
    "2": -0.5,
    "3": 0.5,
    "4": -0.5
  }
}
```

---

##### Command: Servo Control
Control servo motors (channels 1-8).

**Request:**
```json
{
  "type": "servo",
  "servo_1": 1500,
  "servo_2": 1000
}
```

| Field | Type | Range | Description |
|-------|------|-------|-------------|
| `type` | string | - | Must be "servo" |
| `servo_N` | integer | 500-2500 | Pulse width in microseconds |

**Response:**
```json
{
  "type": "servo_response",
  "success": true
}
```

---

##### Command: Stop Motors
Stops all motors gracefully.

**Request:**
```json
{
  "type": "stop"
}
```

**Response:**
```json
{
  "type": "stop_response",
  "success": true
}
```

---

##### Command: Emergency Stop
Triggers emergency stop.

**Request:**
```json
{
  "type": "emergency_stop"
}
```

**Response:**
```json
{
  "type": "emergency_stop_response",
  "success": true
}
```

---

##### Command: Reset Emergency Stop
Resets emergency stop state.

**Request:**
```json
{
  "type": "reset_emergency_stop"
}
```

**Response:**
```json
{
  "type": "reset_emergency_stop_response",
  "success": true
}
```

---

##### Command: Ping
Health check over WebSocket.

**Request:**
```json
{
  "type": "ping"
}
```

**Response:**
```json
{
  "type": "pong",
  "timestamp": 1701625200.123
}
```

---

#### Telemetry WebSocket
```
WS /ws/telemetry
```

Streams real-time sensor data from the rover.

**Telemetry Message:**
```json
{
  "timestamp": 1701625200.123,
  "imu": {
    "accel_x": 0.05,
    "accel_y": -0.02,
    "accel_z": 9.81,
    "gyro_x": 0.01,
    "gyro_y": -0.005,
    "gyro_z": 0.002,
    "temp": 35.5
  },
  "encoders": {
    "encoder_1": 1234,
    "encoder_2": 1230,
    "encoder_3": 1228,
    "encoder_4": 1235
  },
  "battery": {
    "voltage": 7.4
  },
  "system": {
    "cpu_percent": 25.5,
    "memory_percent": 45.2,
    "cpu_temp": 55.0
  },
  "motors": {
    "1": 0.0,
    "2": 0.0,
    "3": 0.0,
    "4": 0.0
  }
}
```

| Section | Field | Type | Unit | Description |
|---------|-------|------|------|-------------|
| **imu** | accel_x/y/z | float | m/s² | Accelerometer readings |
| | gyro_x/y/z | float | rad/s | Gyroscope readings |
| | temp | float | °C | IMU temperature |
| **encoders** | encoder_1-4 | integer | ticks | Encoder counts |
| **battery** | voltage | float | V | Battery voltage |
| **system** | cpu_percent | float | % | CPU usage |
| | memory_percent | float | % | Memory usage |
| | cpu_temp | float | °C | CPU temperature |
| **motors** | 1-4 | float | -1 to 1 | Current motor speeds |

**Update Rates:**
- IMU: 50 Hz
- Encoders: 50 Hz
- Battery: 1 Hz
- System metrics: 1 Hz

---

## Dashboard Server (Port 8080)

The dashboard provides a web interface and proxies requests to the control server.

### REST Endpoints

#### Dashboard Page
```
GET /
```
Returns the HTML dashboard page.

---

#### Health Check
```
GET /health
```
Returns dashboard status.

**Response:**
```json
{
  "status": "ok"
}
```

---

### WebSocket Endpoints

#### Telemetry Proxy
```
WS /ws/telemetry
```
Proxies telemetry from the control server to browser clients.

---

#### Control Proxy
```
WS /ws/control
```
Proxies control commands from browser to the control server.

---

## Mecanum Wheel Kinematics

The rover uses mecanum wheels for omnidirectional movement. The kinematic equations are:

```
FL = vx + vy + omega
FR = vx - vy - omega
RL = vx - vy + omega
RR = vx + vy - omega
```

Where:
- `vx` = Forward velocity (-1 to 1)
- `vy` = Strafe velocity (-1 to 1)  
- `omega` = Rotation velocity (-1 to 1)

### Movement Examples

| Action | vx | vy | omega |
|--------|----|----|-------|
| Forward | 1.0 | 0 | 0 |
| Backward | -1.0 | 0 | 0 |
| Strafe Right | 0 | 1.0 | 0 |
| Strafe Left | 0 | -1.0 | 0 |
| Rotate Clockwise | 0 | 0 | 1.0 |
| Rotate Counter-CW | 0 | 0 | -1.0 |
| Forward-Right Diagonal | 1.0 | 1.0 | 0 |
| Forward while Rotating | 0.5 | 0 | 0.5 |

---

## Safety Features

### Watchdog Timer
If no control commands are received within 1 second, all motors automatically stop. This prevents runaway situations if the connection is lost.

### Emergency Stop
Emergency stop immediately halts all motors and ignores subsequent commands until reset.

### Motor Speed Clamping
All motor speeds are clamped to the range [-1.0, 1.0] and normalized if the combined mecanum calculation exceeds limits.

### Graceful Disconnect
When a control WebSocket disconnects, all motors are automatically stopped.

---

## Dashboard Features

- **Dual Joystick Control**: Left joystick for rotation, right joystick for movement
- **Speed Mode Toggle**: Switch between Low (30%) and High (100%) speed
- **Real-time Telemetry**: IMU, encoders, battery, and system metrics
- **Emergency Stop Button**: One-click emergency stop
- **Connection Status**: Visual indicator for WebSocket connection

---

## Systemd Services

### bbb-websocket.service
Main robot control server.

```ini
[Unit]
Description=BeagleBone Blue WebSocket Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/debian/bbb-websocket
ExecStart=/usr/bin/python3 /home/debian/bbb-websocket/bbb-server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

### bbb-dashboard.service
Web dashboard server.

```ini
[Unit]
Description=BeagleBone Blue Dashboard
After=network.target bbb-websocket.service
Wants=bbb-websocket.service

[Service]
Type=simple
User=debian
WorkingDirectory=/home/debian/bbb-websocket
ExecStart=/usr/bin/python3 /home/debian/bbb-websocket/dashboard-bbb.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

### Service Commands

```bash
# Start services
sudo systemctl start bbb-websocket bbb-dashboard

# Stop services
sudo systemctl stop bbb-websocket bbb-dashboard

# Restart services
sudo systemctl restart bbb-websocket bbb-dashboard

# Check status
sudo systemctl status bbb-websocket bbb-dashboard

# View logs
journalctl -u bbb-websocket -f
journalctl -u bbb-dashboard -f
```

---

## Troubleshooting

### Motors not moving
1. Check battery connection and voltage
2. Verify motor wiring to correct ports
3. Check for emergency stop state: `curl http://192.168.2.212:8001/health`
4. Reset emergency stop if needed: `curl -X POST http://192.168.2.212:8001/reset_emergency_stop`

### WebSocket connection failing
1. Ensure services are running: `sudo systemctl status bbb-websocket bbb-dashboard`
2. Check firewall allows ports 8001 and 8080
3. Verify network connectivity to BBB

### Dashboard not loading
1. Check dashboard service: `sudo systemctl status bbb-dashboard`
2. View logs: `journalctl -u bbb-dashboard -n 50`
3. Restart service: `sudo systemctl restart bbb-dashboard`

### IMU showing zeros
1. Run gyro calibration: `rc_calibrate_gyro`
2. Restart the bbb-websocket service

---

## License

MIT License
