"""
BeagleBone Blue Rover Dashboard (BBB Local Version)

Lightweight dashboard that runs directly on the BBB.
Connects to localhost:8001 for robot control.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import websockets
import uvicorn
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
BBB_WS_PORT = 8001
DASHBOARD_PORT = 8080

# ============================================================================
# Telemetry Proxy
# ============================================================================

class TelemetryProxy:
    """Proxies telemetry from BBB server to dashboard clients"""
    
    def __init__(self):
        self._clients = set()
        self._running = False
        self._task = None
        self._last_telemetry = {}
    
    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._proxy_loop())
        logger.info("Telemetry proxy started")
    
    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Telemetry proxy stopped")
    
    async def add_client(self, ws: WebSocket):
        self._clients.add(ws)
        logger.info(f"Dashboard client connected. Total: {len(self._clients)}")
    
    async def remove_client(self, ws: WebSocket):
        self._clients.discard(ws)
        logger.info(f"Dashboard client disconnected. Total: {len(self._clients)}")
    
    async def _proxy_loop(self):
        uri = f"ws://127.0.0.1:{BBB_WS_PORT}/ws/telemetry"
        
        while self._running:
            try:
                async with websockets.connect(uri, ping_interval=5) as ws:
                    logger.info(f"Connected to BBB telemetry at {uri}")
                    
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            data["_connected"] = True
                            self._last_telemetry = data
                            
                            dead_clients = set()
                            for client in self._clients:
                                try:
                                    await client.send_json(data)
                                except:
                                    dead_clients.add(client)
                            
                            self._clients -= dead_clients
                        except json.JSONDecodeError:
                            pass
            
            except Exception as e:
                logger.warning(f"BBB telemetry connection error: {e}")
                self._last_telemetry = {"_connected": False, "_error": str(e)}
            
            if self._running:
                await asyncio.sleep(2)

telemetry_proxy = TelemetryProxy()

# ============================================================================
# FastAPI Application
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting BBB Rover Dashboard (Local)")
    await telemetry_proxy.start()
    yield
    await telemetry_proxy.stop()
    logger.info("Dashboard shutdown complete")

app = FastAPI(
    title="BBB Rover Dashboard",
    description="Local dashboard for BeagleBone Blue rover",
    version="1.0.0",
    lifespan=lifespan
)

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.websocket("/ws/telemetry")
async def telemetry_websocket(websocket: WebSocket):
    await websocket.accept()
    await telemetry_proxy.add_client(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await telemetry_proxy.remove_client(websocket)

@app.websocket("/ws/control")
async def control_websocket(websocket: WebSocket):
    await websocket.accept()
    uri = f"ws://127.0.0.1:{BBB_WS_PORT}/ws/control"
    
    try:
        async with websockets.connect(uri) as bbb_ws:
            async def forward_to_bbb():
                async for message in websocket.iter_text():
                    await bbb_ws.send(message)
            
            async def forward_to_client():
                async for message in bbb_ws:
                    await websocket.send_text(message)
            
            await asyncio.gather(forward_to_bbb(), forward_to_client())
    except Exception as e:
        logger.error(f"Control WebSocket error: {e}")

# ============================================================================
# Dashboard HTML
# ============================================================================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BBB Rover Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            min-height: 100vh;
        }
        
        .header {
            background: #16213e;
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid #0f3460;
        }
        
        .header h1 { color: #e94560; font-size: 1.5rem; }
        
        .status-indicator {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        
        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #ff4444;
        }
        
        .status-dot.connected { background: #44ff44; }
        
        .main-content {
            display: grid;
            grid-template-columns: 1fr 300px;
            gap: 1rem;
            padding: 1rem;
            max-width: 1400px;
            margin: 0 auto;
        }
        
        .card {
            background: #16213e;
            border-radius: 8px;
            padding: 1rem;
            border: 1px solid #0f3460;
        }
        
        .card h2 {
            color: #e94560;
            font-size: 1rem;
            margin-bottom: 1rem;
            padding-bottom: 0.5rem;
            border-bottom: 1px solid #0f3460;
        }
        
        .control-section {
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }
        
        .joystick-container {
            display: flex;
            justify-content: center;
            gap: 2rem;
            padding: 1rem;
        }
        
        .joystick-wrapper {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 0.5rem;
        }
        
        .joystick {
            width: 120px;
            height: 120px;
            background: #0f3460;
            border-radius: 50%;
            position: relative;
            touch-action: none;
            border: 2px solid #e94560;
        }
        
        .joystick-knob {
            width: 40px;
            height: 40px;
            background: #e94560;
            border-radius: 50%;
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            cursor: grab;
        }
        
        .joystick-label {
            font-size: 0.8rem;
            color: #aaa;
        }
        
        .button-row {
            display: flex;
            gap: 0.5rem;
            justify-content: center;
        }
        
        button {
            padding: 0.5rem 1rem;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.2s;
        }
        
        .btn-stop {
            background: #ff4444;
            color: white;
        }
        
        .btn-stop:hover { background: #ff6666; }
        
        .btn-emergency {
            background: #ff0000;
            color: white;
            font-size: 1.2rem;
            padding: 1rem 2rem;
        }
        
        .btn-emergency:hover { background: #cc0000; }
        
        .btn-speed {
            background: #4ecdc4;
            color: #1a1a2e;
            min-width: 80px;
        }
        
        .btn-speed.low {
            background: #4ecdc4;
        }
        
        .btn-speed.high {
            background: #e94560;
            color: white;
        }
        
        .telemetry-grid {
            display: grid;
            gap: 1rem;
        }
        
        .telemetry-item {
            display: flex;
            justify-content: space-between;
            padding: 0.5rem;
            background: #0f3460;
            border-radius: 4px;
        }
        
        .telemetry-label { color: #aaa; }
        .telemetry-value { color: #4ecdc4; font-family: monospace; }
        
        .imu-display {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 0.5rem;
        }
        
        .imu-axis {
            text-align: center;
            padding: 0.5rem;
            background: #0f3460;
            border-radius: 4px;
        }
        
        .imu-axis-label { font-size: 0.7rem; color: #aaa; }
        .imu-axis-value { font-family: monospace; color: #4ecdc4; }
        
        @media (max-width: 768px) {
            .main-content {
                grid-template-columns: 1fr;
            }
            .joystick-container {
                flex-wrap: wrap;
            }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🤖 BBB Rover Dashboard</h1>
        <div class="status-indicator" id="connectionStatus">
            <span class="status-dot"></span>
            <span id="connectionText">Disconnected</span>
        </div>
    </div>
    
    <div class="main-content">
        <div class="control-section">
            <div class="card">
                <h2>🎮 Controls</h2>
                <div class="joystick-container">
                    <div class="joystick-wrapper">
                        <div class="joystick" id="moveJoystick">
                            <div class="joystick-knob" id="moveKnob"></div>
                        </div>
                        <div class="joystick-label">Move (↑↓←→)</div>
                    </div>
                    <div class="joystick-wrapper">
                        <div class="joystick" id="rotateJoystick">
                            <div class="joystick-knob" id="rotateKnob"></div>
                        </div>
                        <div class="joystick-label">Rotate (↻↺)</div>
                    </div>
                </div>
                <div class="button-row">
                    <button class="btn-speed low" id="speedBtn" onclick="toggleSpeed()">🐢 Low</button>
                    <button class="btn-stop" onclick="stopMotors()">Stop</button>
                    <button class="btn-emergency" onclick="emergencyStop()">🛑 EMERGENCY STOP</button>
                </div>
            </div>
            
            <div class="card">
                <h2>📊 IMU</h2>
                <div class="imu-display">
                    <div class="imu-axis">
                        <div class="imu-axis-label">Accel X</div>
                        <div class="imu-axis-value" id="accelX">0.00</div>
                    </div>
                    <div class="imu-axis">
                        <div class="imu-axis-label">Accel Y</div>
                        <div class="imu-axis-value" id="accelY">0.00</div>
                    </div>
                    <div class="imu-axis">
                        <div class="imu-axis-label">Accel Z</div>
                        <div class="imu-axis-value" id="accelZ">0.00</div>
                    </div>
                    <div class="imu-axis">
                        <div class="imu-axis-label">Gyro X</div>
                        <div class="imu-axis-value" id="gyroX">0.00</div>
                    </div>
                    <div class="imu-axis">
                        <div class="imu-axis-label">Gyro Y</div>
                        <div class="imu-axis-value" id="gyroY">0.00</div>
                    </div>
                    <div class="imu-axis">
                        <div class="imu-axis-label">Gyro Z</div>
                        <div class="imu-axis-value" id="gyroZ">0.00</div>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="telemetry-section">
            <div class="card">
                <h2>🔋 Battery</h2>
                <div class="telemetry-grid">
                    <div class="telemetry-item">
                        <span class="telemetry-label">Voltage</span>
                        <span class="telemetry-value" id="batteryVoltage">--</span>
                    </div>
                </div>
            </div>
            
            <div class="card" style="margin-top: 1rem;">
                <h2>⚙️ Encoders</h2>
                <div class="telemetry-grid">
                    <div class="telemetry-item">
                        <span class="telemetry-label">Enc 1</span>
                        <span class="telemetry-value" id="enc1">0</span>
                    </div>
                    <div class="telemetry-item">
                        <span class="telemetry-label">Enc 2</span>
                        <span class="telemetry-value" id="enc2">0</span>
                    </div>
                    <div class="telemetry-item">
                        <span class="telemetry-label">Enc 3</span>
                        <span class="telemetry-value" id="enc3">0</span>
                    </div>
                    <div class="telemetry-item">
                        <span class="telemetry-label">Enc 4</span>
                        <span class="telemetry-value" id="enc4">0</span>
                    </div>
                </div>
            </div>
            
            <div class="card" style="margin-top: 1rem;">
                <h2>📈 System</h2>
                <div class="telemetry-grid">
                    <div class="telemetry-item">
                        <span class="telemetry-label">CPU</span>
                        <span class="telemetry-value" id="cpuUsage">--%</span>
                    </div>
                    <div class="telemetry-item">
                        <span class="telemetry-label">Memory</span>
                        <span class="telemetry-value" id="memUsage">--%</span>
                    </div>
                    <div class="telemetry-item">
                        <span class="telemetry-label">Temp</span>
                        <span class="telemetry-value" id="cpuTemp">--°C</span>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        // WebSocket connections
        let telemetryWs = null;
        let controlWs = null;
        let controlWsReady = false;
        
        // Joystick state
        let moveX = 0, moveY = 0, rotate = 0;
        const KEEPALIVE_INTERVAL = 100;
        let keepaliveTimer = null;
        
        // Speed mode: 'low' = 30%, 'high' = 100%
        let speedMode = 'low';
        let speedMultiplier = 0.3;
        
        function toggleSpeed() {
            const btn = document.getElementById('speedBtn');
            if (speedMode === 'low') {
                speedMode = 'high';
                speedMultiplier = 1.0;
                btn.textContent = '🐇 High';
                btn.classList.remove('low');
                btn.classList.add('high');
            } else {
                speedMode = 'low';
                speedMultiplier = 0.3;
                btn.textContent = '🐢 Low';
                btn.classList.remove('high');
                btn.classList.add('low');
            }
        }
        
        // Connect to telemetry
        function connectTelemetry() {
            telemetryWs = new WebSocket(`ws://${location.host}/ws/telemetry`);
            
            telemetryWs.onopen = () => {
                document.getElementById('connectionStatus').querySelector('.status-dot').classList.add('connected');
                document.getElementById('connectionText').textContent = 'Connected';
            };
            
            telemetryWs.onclose = () => {
                document.getElementById('connectionStatus').querySelector('.status-dot').classList.remove('connected');
                document.getElementById('connectionText').textContent = 'Disconnected';
                setTimeout(connectTelemetry, 2000);
            };
            
            telemetryWs.onmessage = (event) => {
                const data = JSON.parse(event.data);
                updateTelemetry(data);
            };
        }
        
        // Connect to control WebSocket
        function connectControl() {
            controlWs = new WebSocket(`ws://${location.host}/ws/control`);
            controlWsReady = false;
            
            controlWs.onopen = () => {
                console.log('Control WebSocket connected');
                controlWsReady = true;
            };
            
            controlWs.onclose = () => {
                console.log('Control WebSocket disconnected');
                controlWsReady = false;
                setTimeout(connectControl, 1000);
            };
            
            controlWs.onerror = (e) => {
                console.error('Control WebSocket error:', e);
            };
        }
        
        function updateTelemetry(data) {
            if (data.imu) {
                document.getElementById('accelX').textContent = (data.imu.accel_x || 0).toFixed(2);
                document.getElementById('accelY').textContent = (data.imu.accel_y || 0).toFixed(2);
                document.getElementById('accelZ').textContent = (data.imu.accel_z || 0).toFixed(2);
                document.getElementById('gyroX').textContent = (data.imu.gyro_x || 0).toFixed(2);
                document.getElementById('gyroY').textContent = (data.imu.gyro_y || 0).toFixed(2);
                document.getElementById('gyroZ').textContent = (data.imu.gyro_z || 0).toFixed(2);
            }
            
            if (data.battery) {
                document.getElementById('batteryVoltage').textContent = 
                    (data.battery.voltage || 0).toFixed(2) + 'V';
            }
            
            if (data.encoders) {
                document.getElementById('enc1').textContent = data.encoders.encoder_1 || 0;
                document.getElementById('enc2').textContent = data.encoders.encoder_2 || 0;
                document.getElementById('enc3').textContent = data.encoders.encoder_3 || 0;
                document.getElementById('enc4').textContent = data.encoders.encoder_4 || 0;
            }
            
            if (data.system) {
                document.getElementById('cpuUsage').textContent = 
                    (data.system.cpu_percent || 0).toFixed(1) + '%';
                document.getElementById('memUsage').textContent = 
                    (data.system.memory_percent || 0).toFixed(1) + '%';
                document.getElementById('cpuTemp').textContent = 
                    (data.system.cpu_temp || 0).toFixed(1) + '°C';
            }
        }
        
        // Joystick handling
        let activeJoystick = null;
        
        function setupJoystick(containerId, knobId, onMove) {
            const container = document.getElementById(containerId);
            const knob = document.getElementById(knobId);
            
            const getPosition = (e) => {
                const rect = container.getBoundingClientRect();
                const centerX = rect.width / 2;
                const centerY = rect.height / 2;
                
                let clientX, clientY;
                if (e.touches) {
                    clientX = e.touches[0].clientX;
                    clientY = e.touches[0].clientY;
                } else {
                    clientX = e.clientX;
                    clientY = e.clientY;
                }
                
                let x = (clientX - rect.left - centerX) / centerX;
                let y = (clientY - rect.top - centerY) / centerY;
                
                const dist = Math.sqrt(x*x + y*y);
                if (dist > 1) {
                    x /= dist;
                    y /= dist;
                }
                
                return { x, y };
            };
            
            const updateKnob = (x, y) => {
                knob.style.left = (50 + x * 40) + '%';
                knob.style.top = (50 + y * 40) + '%';
            };
            
            const start = (e) => {
                activeJoystick = containerId;
                e.preventDefault();
            };
            
            const move = (e) => {
                if (activeJoystick !== containerId) return;
                e.preventDefault();
                const pos = getPosition(e);
                updateKnob(pos.x, pos.y);
                onMove(pos.x, pos.y);
            };
            
            const end = () => {
                if (activeJoystick === containerId) {
                    activeJoystick = null;
                    updateKnob(0, 0);
                    onMove(0, 0);
                }
            };
            
            container.addEventListener('mousedown', start);
            container.addEventListener('touchstart', start);
            document.addEventListener('mousemove', move);
            document.addEventListener('touchmove', move);
            document.addEventListener('mouseup', end);
            document.addEventListener('touchend', end);
        }
        
        setupJoystick('moveJoystick', 'moveKnob', (x, y) => {
            rotate = x;
            sendControl();
        });
        
        setupJoystick('rotateJoystick', 'rotateKnob', (x, y) => {
            moveX = -y;  // 90° clockwise: X becomes -Y
            moveY = -x;  // 90° clockwise: Y becomes -X
            sendControl();
        });
        
        function sendControl() {
            const hasMovement = moveX !== 0 || moveY !== 0 || rotate !== 0;
            
            if (controlWs && controlWsReady) {
                const cmd = {
                    type: 'mecanum',
                    vx: moveY * speedMultiplier,
                    vy: moveX * speedMultiplier,
                    omega: rotate * speedMultiplier
                };
                controlWs.send(JSON.stringify(cmd));
            }
            
            if (hasMovement) {
                if (keepaliveTimer) clearTimeout(keepaliveTimer);
                keepaliveTimer = setTimeout(sendControl, KEEPALIVE_INTERVAL);
            } else {
                if (keepaliveTimer) {
                    clearTimeout(keepaliveTimer);
                    keepaliveTimer = null;
                }
            }
        }
        
        function emergencyStop() {
            if (controlWs && controlWsReady) {
                controlWs.send(JSON.stringify({ type: 'emergency_stop' }));
            }
        }
        
        function stopMotors() {
            if (controlWs && controlWsReady) {
                controlWs.send(JSON.stringify({ type: 'stop' }));
            }
        }
        
        // Initialize
        connectTelemetry();
        connectControl();
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=DASHBOARD_PORT)
