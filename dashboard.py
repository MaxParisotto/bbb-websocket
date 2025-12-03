"""
BeagleBone Blue Rover Dashboard

A web-based dashboard for:
- Monitoring rover telemetry (IMU, encoders, battery, system)
- Controlling the rover (mecanum drive)
- OTA updates via SSH
- Service management on the BBB
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
import paramiko
import websockets
from dotenv import load_dotenv
import logging

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

@dataclass
class Config:
    """Dashboard configuration"""
    # BBB Connection
    bbb_host: str = os.getenv("BeagleBoneBlue_IP", "192.168.2.212")
    bbb_user: str = os.getenv("username", "debian")
    bbb_password: str = os.getenv("password", "temppwd")
    bbb_port: int = 22
    
    # BBB WebSocket server
    bbb_ws_port: int = 8001
    
    # Paths on BBB
    bbb_app_path: str = "/home/debian/bbb-websocket"
    bbb_service_name: str = "bbb-websocket"
    
    # Dashboard server
    dashboard_port: int = 8080

config = Config()

# ============================================================================
# SSH Manager
# ============================================================================

class SSHManager:
    """Manages SSH connections to the BeagleBone Blue"""
    
    def __init__(self):
        self._client: Optional[paramiko.SSHClient] = None
    
    def connect(self) -> bool:
        """Establish SSH connection"""
        try:
            self._client = paramiko.SSHClient()
            self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self._client.connect(
                hostname=config.bbb_host,
                port=config.bbb_port,
                username=config.bbb_user,
                password=config.bbb_password,
                timeout=10
            )
            logger.info(f"SSH connected to {config.bbb_host}")
            return True
        except Exception as e:
            logger.error(f"SSH connection failed: {e}")
            self._client = None
            return False
    
    def disconnect(self):
        """Close SSH connection"""
        if self._client:
            self._client.close()
            self._client = None
            logger.info("SSH disconnected")
    
    def is_connected(self) -> bool:
        """Check if connected"""
        if not self._client:
            return False
        try:
            transport = self._client.get_transport()
            return transport is not None and transport.is_active()
        except:
            return False
    
    def ensure_connected(self) -> bool:
        """Ensure connection is active, reconnect if needed"""
        if not self.is_connected():
            return self.connect()
        return True
    
    def exec_command(self, command: str, sudo: bool = False) -> Dict[str, Any]:
        """Execute a command on the BBB"""
        if not self.ensure_connected():
            return {"success": False, "error": "Not connected"}
        
        try:
            if sudo:
                command = f"echo {config.bbb_password} | sudo -S {command}"
            
            stdin, stdout, stderr = self._client.exec_command(command, timeout=30)
            exit_code = stdout.channel.recv_exit_status()
            
            return {
                "success": exit_code == 0,
                "stdout": stdout.read().decode('utf-8').strip(),
                "stderr": stderr.read().decode('utf-8').strip(),
                "exit_code": exit_code
            }
        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return {"success": False, "error": str(e)}
    
    def upload_file(self, local_path: str, remote_path: str) -> bool:
        """Upload a file to the BBB"""
        if not self.ensure_connected():
            return False
        
        try:
            sftp = self._client.open_sftp()
            sftp.put(local_path, remote_path)
            sftp.close()
            logger.info(f"Uploaded {local_path} to {remote_path}")
            return True
        except Exception as e:
            logger.error(f"File upload failed: {e}")
            return False
    
    def upload_file_content(self, content: bytes, remote_path: str) -> bool:
        """Upload file content directly to the BBB"""
        if not self.ensure_connected():
            return False
        
        try:
            sftp = self._client.open_sftp()
            with sftp.file(remote_path, 'wb') as f:
                f.write(content)
            sftp.close()
            logger.info(f"Uploaded content to {remote_path}")
            return True
        except Exception as e:
            logger.error(f"File upload failed: {e}")
            return False
    
    def get_sftp(self):
        """Get SFTP client"""
        if not self.ensure_connected():
            return None
        return self._client.open_sftp()

# Global SSH manager
ssh_manager = SSHManager()

# ============================================================================
# OTA Update Manager
# ============================================================================

class OTAManager:
    """Manages Over-The-Air updates to the BBB"""
    
    def __init__(self, ssh: SSHManager):
        self.ssh = ssh
    
    def get_service_status(self) -> Dict[str, Any]:
        """Get the status of the robot service"""
        result = self.ssh.exec_command(
            f"systemctl is-active {config.bbb_service_name}",
            sudo=True
        )
        
        status = result.get("stdout", "unknown")
        
        # Get more details
        details = self.ssh.exec_command(
            f"systemctl status {config.bbb_service_name} --no-pager -l",
            sudo=True
        )
        
        return {
            "active": status == "active",
            "status": status,
            "details": details.get("stdout", "")
        }
    
    def restart_service(self) -> Dict[str, Any]:
        """Restart the robot service"""
        result = self.ssh.exec_command(
            f"systemctl restart {config.bbb_service_name}",
            sudo=True
        )
        return {
            "success": result.get("success", False),
            "message": result.get("stdout", result.get("error", ""))
        }
    
    def stop_service(self) -> Dict[str, Any]:
        """Stop the robot service"""
        result = self.ssh.exec_command(
            f"systemctl stop {config.bbb_service_name}",
            sudo=True
        )
        return {
            "success": result.get("success", False),
            "message": result.get("stdout", result.get("error", ""))
        }
    
    def start_service(self) -> Dict[str, Any]:
        """Start the robot service"""
        result = self.ssh.exec_command(
            f"systemctl start {config.bbb_service_name}",
            sudo=True
        )
        return {
            "success": result.get("success", False),
            "message": result.get("stdout", result.get("error", ""))
        }
    
    def deploy_update(self, file_content: bytes, filename: str) -> Dict[str, Any]:
        """Deploy an update to the BBB"""
        remote_path = f"{config.bbb_app_path}/{filename}"
        backup_path = f"{config.bbb_app_path}/{filename}.backup"
        
        steps = []
        
        # 1. Stop service
        steps.append({"step": "Stopping service...", "status": "running"})
        stop_result = self.stop_service()
        steps[-1]["status"] = "done" if stop_result["success"] else "warning"
        
        # 2. Backup existing file
        steps.append({"step": f"Backing up {filename}...", "status": "running"})
        backup_result = self.ssh.exec_command(f"cp {remote_path} {backup_path} 2>/dev/null || true")
        steps[-1]["status"] = "done"
        
        # 3. Upload new file
        steps.append({"step": f"Uploading {filename}...", "status": "running"})
        if self.ssh.upload_file_content(file_content, remote_path):
            steps[-1]["status"] = "done"
        else:
            steps[-1]["status"] = "error"
            return {"success": False, "steps": steps, "error": "Upload failed"}
        
        # 4. Set permissions
        steps.append({"step": "Setting permissions...", "status": "running"})
        self.ssh.exec_command(f"chmod +x {remote_path}")
        steps[-1]["status"] = "done"
        
        # 5. Install requirements if requirements.txt
        if filename == "requirements.txt":
            steps.append({"step": "Installing dependencies...", "status": "running"})
            pip_result = self.ssh.exec_command(
                f"cd {config.bbb_app_path} && pip3 install -r requirements.txt",
                sudo=True
            )
            steps[-1]["status"] = "done" if pip_result["success"] else "warning"
        
        # 6. Start service
        steps.append({"step": "Starting service...", "status": "running"})
        start_result = self.start_service()
        steps[-1]["status"] = "done" if start_result["success"] else "error"
        
        return {
            "success": start_result["success"],
            "steps": steps
        }
    
    def rollback(self, filename: str) -> Dict[str, Any]:
        """Rollback to the backup version"""
        remote_path = f"{config.bbb_app_path}/{filename}"
        backup_path = f"{config.bbb_app_path}/{filename}.backup"
        
        self.stop_service()
        result = self.ssh.exec_command(f"cp {backup_path} {remote_path}")
        self.start_service()
        
        return {"success": result.get("success", False)}
    
    def get_bbb_info(self) -> Dict[str, Any]:
        """Get BBB system information"""
        info = {}
        
        # Uptime
        result = self.ssh.exec_command("uptime -p")
        info["uptime"] = result.get("stdout", "unknown")
        
        # CPU temp
        result = self.ssh.exec_command("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null")
        try:
            temp = int(result.get("stdout", "0")) / 1000
            info["cpu_temp"] = f"{temp:.1f}°C"
        except:
            info["cpu_temp"] = "unknown"
        
        # Memory
        result = self.ssh.exec_command("free -h | grep Mem | awk '{print $3\"/\"$2}'")
        info["memory"] = result.get("stdout", "unknown")
        
        # Disk
        result = self.ssh.exec_command("df -h / | tail -1 | awk '{print $3\"/\"$2\" (\"$5\")\"}'")
        info["disk"] = result.get("stdout", "unknown")
        
        # Kernel
        result = self.ssh.exec_command("uname -r")
        info["kernel"] = result.get("stdout", "unknown")
        
        # IP addresses
        result = self.ssh.exec_command("hostname -I")
        info["ip_addresses"] = result.get("stdout", "unknown")
        
        return info

# Global OTA manager
ota_manager = OTAManager(ssh_manager)

# ============================================================================
# WebSocket Proxy for Telemetry
# ============================================================================

class TelemetryProxy:
    """Proxies telemetry from BBB to dashboard clients"""
    
    def __init__(self):
        self._clients: set = set()
        self._bbb_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_telemetry: Dict[str, Any] = {}
    
    async def start(self):
        """Start the telemetry proxy"""
        self._running = True
        self._task = asyncio.create_task(self._proxy_loop())
        logger.info("Telemetry proxy started")
    
    async def stop(self):
        """Stop the telemetry proxy"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._bbb_ws:
            await self._bbb_ws.close()
        logger.info("Telemetry proxy stopped")
    
    async def add_client(self, ws: WebSocket):
        """Add a dashboard client"""
        await ws.accept()
        self._clients.add(ws)
        # Send last known telemetry immediately
        if self._last_telemetry:
            try:
                await ws.send_json(self._last_telemetry)
            except:
                pass
        logger.info(f"Dashboard client connected. Total: {len(self._clients)}")
    
    async def remove_client(self, ws: WebSocket):
        """Remove a dashboard client"""
        self._clients.discard(ws)
        logger.info(f"Dashboard client disconnected. Total: {len(self._clients)}")
    
    async def _proxy_loop(self):
        """Main proxy loop - connects to BBB and forwards telemetry"""
        uri = f"ws://{config.bbb_host}:{config.bbb_ws_port}/ws/telemetry"
        
        while self._running:
            try:
                async with websockets.connect(uri, ping_interval=5) as ws:
                    self._bbb_ws = ws
                    logger.info(f"Connected to BBB telemetry at {uri}")
                    
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            data["_connected"] = True
                            self._last_telemetry = data
                            
                            # Broadcast to all dashboard clients
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
                
                # Notify clients of disconnection
                for client in list(self._clients):
                    try:
                        await client.send_json(self._last_telemetry)
                    except:
                        pass
            
            if self._running:
                await asyncio.sleep(2)  # Reconnect delay

# Global telemetry proxy
telemetry_proxy = TelemetryProxy()

# ============================================================================
# Control Proxy
# ============================================================================

async def send_control_command(command: Dict[str, Any]) -> Dict[str, Any]:
    """Send a control command to the BBB"""
    uri = f"ws://{config.bbb_host}:{config.bbb_ws_port}/ws/control"
    
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(command))
            response = await asyncio.wait_for(ws.recv(), timeout=5.0)
            return json.loads(response)
    except Exception as e:
        logger.error(f"Control command failed: {e}")
        return {"success": False, "error": str(e)}

# ============================================================================
# FastAPI Application
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle"""
    # Startup
    logger.info("Starting BBB Rover Dashboard")
    ssh_manager.connect()
    await telemetry_proxy.start()
    
    yield
    
    # Shutdown
    await telemetry_proxy.stop()
    ssh_manager.disconnect()
    logger.info("Dashboard shutdown complete")

app = FastAPI(
    title="BBB Rover Dashboard",
    description="Control and monitor BeagleBone Blue Rover",
    version="1.0.0",
    lifespan=lifespan
)

# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/api/status")
async def get_status():
    """Get overall system status"""
    return {
        "ssh_connected": ssh_manager.is_connected(),
        "bbb_host": config.bbb_host,
        "telemetry_clients": len(telemetry_proxy._clients)
    }

@app.get("/api/bbb/info")
async def get_bbb_info():
    """Get BBB system information"""
    return ota_manager.get_bbb_info()

@app.get("/api/service/status")
async def get_service_status():
    """Get robot service status"""
    return ota_manager.get_service_status()

@app.post("/api/service/restart")
async def restart_service():
    """Restart the robot service"""
    return ota_manager.restart_service()

@app.post("/api/service/stop")
async def stop_service():
    """Stop the robot service"""
    return ota_manager.stop_service()

@app.post("/api/service/start")
async def start_service():
    """Start the robot service"""
    return ota_manager.start_service()

@app.post("/api/deploy")
async def deploy_update(file: UploadFile = File(...)):
    """Deploy an update to the BBB"""
    content = await file.read()
    return ota_manager.deploy_update(content, file.filename)

@app.post("/api/rollback/{filename}")
async def rollback(filename: str):
    """Rollback to backup version"""
    return ota_manager.rollback(filename)

@app.post("/api/ssh/command")
async def run_ssh_command(command: str, sudo: bool = False):
    """Run an SSH command on the BBB"""
    return ssh_manager.exec_command(command, sudo=sudo)

@app.post("/api/control")
async def control_robot(command: Dict[str, Any]):
    """Send control command to the robot"""
    return await send_control_command(command)

@app.post("/api/control/stop")
async def emergency_stop():
    """Emergency stop"""
    return await send_control_command({"type": "emergency_stop"})

@app.post("/api/control/reset")
async def reset_emergency():
    """Reset emergency stop"""
    return await send_control_command({"type": "reset_emergency_stop"})

# ============================================================================
# WebSocket Endpoints
# ============================================================================

@app.websocket("/ws/telemetry")
async def telemetry_websocket(websocket: WebSocket):
    """WebSocket endpoint for live telemetry"""
    await telemetry_proxy.add_client(websocket)
    try:
        while True:
            # Keep connection alive, handle any client messages
            data = await websocket.receive_text()
            # Could handle client requests here
    except WebSocketDisconnect:
        pass
    finally:
        await telemetry_proxy.remove_client(websocket)

@app.websocket("/ws/control")
async def control_websocket(websocket: WebSocket):
    """WebSocket endpoint for robot control"""
    await websocket.accept()
    
    uri = f"ws://{config.bbb_host}:{config.bbb_ws_port}/ws/control"
    
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
    finally:
        pass

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
            background: #e94560;
        }
        
        .status-dot.connected { background: #4ade80; }
        
        .container {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 1rem;
            padding: 1rem;
            max-width: 1600px;
            margin: 0 auto;
        }
        
        .panel {
            background: #16213e;
            border-radius: 8px;
            padding: 1rem;
            border: 1px solid #0f3460;
        }
        
        .panel h2 {
            color: #e94560;
            font-size: 1rem;
            margin-bottom: 1rem;
            padding-bottom: 0.5rem;
            border-bottom: 1px solid #0f3460;
        }
        
        .panel.full-width { grid-column: 1 / -1; }
        .panel.two-cols { grid-column: span 2; }
        
        /* Telemetry */
        .telemetry-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 0.5rem;
        }
        
        .telemetry-item {
            background: #1a1a2e;
            padding: 0.75rem;
            border-radius: 4px;
            text-align: center;
        }
        
        .telemetry-item label {
            display: block;
            font-size: 0.75rem;
            color: #888;
            margin-bottom: 0.25rem;
        }
        
        .telemetry-item .value {
            font-size: 1.25rem;
            font-weight: bold;
            color: #4ade80;
        }
        
        /* Control */
        .joystick-container {
            display: flex;
            justify-content: center;
            gap: 2rem;
            margin: 1rem 0;
        }
        
        .joystick {
            width: 150px;
            height: 150px;
            background: #1a1a2e;
            border-radius: 50%;
            border: 2px solid #0f3460;
            position: relative;
            touch-action: none;
        }
        
        .joystick-knob {
            width: 50px;
            height: 50px;
            background: #e94560;
            border-radius: 50%;
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            cursor: grab;
        }
        
        .joystick-label {
            text-align: center;
            margin-top: 0.5rem;
            font-size: 0.875rem;
            color: #888;
        }
        
        .control-buttons {
            display: flex;
            gap: 0.5rem;
            justify-content: center;
            margin-top: 1rem;
        }
        
        button {
            padding: 0.5rem 1rem;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.2s;
        }
        
        button:hover { transform: translateY(-1px); }
        
        .btn-danger { background: #e94560; color: white; }
        .btn-success { background: #4ade80; color: black; }
        .btn-warning { background: #fbbf24; color: black; }
        .btn-primary { background: #3b82f6; color: white; }
        
        /* OTA */
        .file-upload {
            border: 2px dashed #0f3460;
            border-radius: 8px;
            padding: 2rem;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .file-upload:hover { border-color: #e94560; }
        .file-upload input { display: none; }
        
        .service-status {
            display: flex;
            align-items: center;
            gap: 1rem;
            margin-bottom: 1rem;
        }
        
        .service-badge {
            padding: 0.25rem 0.75rem;
            border-radius: 4px;
            font-size: 0.875rem;
            font-weight: bold;
        }
        
        .service-badge.active { background: #4ade80; color: black; }
        .service-badge.inactive { background: #e94560; color: white; }
        
        /* BBB Info */
        .info-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 0.5rem;
        }
        
        .info-item {
            background: #1a1a2e;
            padding: 0.5rem;
            border-radius: 4px;
        }
        
        .info-item label {
            font-size: 0.7rem;
            color: #888;
        }
        
        .info-item .value {
            font-size: 0.9rem;
        }
        
        /* Motor visualization */
        .motor-viz {
            display: grid;
            grid-template-columns: 1fr 2fr 1fr;
            grid-template-rows: 1fr 1fr;
            gap: 0.5rem;
            max-width: 300px;
            margin: 0 auto;
        }
        
        .motor-wheel {
            background: #1a1a2e;
            padding: 0.5rem;
            border-radius: 4px;
            text-align: center;
        }
        
        .motor-wheel .speed {
            font-size: 1.25rem;
            font-weight: bold;
        }
        
        .motor-wheel .speed.positive { color: #4ade80; }
        .motor-wheel .speed.negative { color: #e94560; }
        
        .rover-body {
            background: #0f3460;
            border-radius: 4px;
            grid-row: span 2;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.75rem;
            color: #888;
        }
        
        /* Deploy log */
        .deploy-log {
            background: #1a1a2e;
            border-radius: 4px;
            padding: 0.5rem;
            max-height: 150px;
            overflow-y: auto;
            font-family: monospace;
            font-size: 0.8rem;
        }
        
        .deploy-step {
            padding: 0.25rem 0;
        }
        
        .deploy-step.done { color: #4ade80; }
        .deploy-step.error { color: #e94560; }
        .deploy-step.running { color: #fbbf24; }
        
        /* Emergency stop overlay */
        .estop-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(233, 69, 96, 0.9);
            z-index: 1000;
            justify-content: center;
            align-items: center;
            flex-direction: column;
        }
        
        .estop-overlay.active { display: flex; }
        
        .estop-overlay h2 {
            font-size: 3rem;
            margin-bottom: 1rem;
        }
        
        /* IMU visualization */
        .imu-viz {
            display: flex;
            justify-content: center;
            gap: 2rem;
        }
        
        .imu-axis {
            text-align: center;
        }
        
        .imu-bar {
            width: 30px;
            height: 100px;
            background: #1a1a2e;
            border-radius: 4px;
            position: relative;
            margin: 0 auto;
        }
        
        .imu-bar-fill {
            position: absolute;
            bottom: 50%;
            left: 0;
            right: 0;
            background: #4ade80;
            border-radius: 4px;
            transition: height 0.1s;
        }
        
        .imu-bar-fill.negative {
            bottom: auto;
            top: 50%;
            background: #e94560;
        }
    </style>
</head>
<body>
    <div class="estop-overlay" id="estopOverlay">
        <h2>⚠️ EMERGENCY STOP</h2>
        <button class="btn-success" onclick="resetEmergencyStop()" style="font-size: 1.5rem; padding: 1rem 2rem;">
            Reset Emergency Stop
        </button>
    </div>

    <header class="header">
        <h1>🤖 BBB Rover Dashboard</h1>
        <div class="status-indicator">
            <div class="status-dot" id="connectionStatus"></div>
            <span id="connectionText">Disconnected</span>
        </div>
    </header>

    <div class="container">
        <!-- Telemetry Panel -->
        <div class="panel">
            <h2>📊 Telemetry</h2>
            <div class="telemetry-grid">
                <div class="telemetry-item">
                    <label>Battery</label>
                    <div class="value" id="battery">--V</div>
                </div>
                <div class="telemetry-item">
                    <label>CPU</label>
                    <div class="value" id="cpu">--%</div>
                </div>
                <div class="telemetry-item">
                    <label>Memory</label>
                    <div class="value" id="memory">--%</div>
                </div>
            </div>
            
            <h3 style="margin: 1rem 0 0.5rem; font-size: 0.9rem; color: #888;">IMU Acceleration</h3>
            <div class="imu-viz">
                <div class="imu-axis">
                    <div class="imu-bar"><div class="imu-bar-fill" id="imuX"></div></div>
                    <span>X</span>
                </div>
                <div class="imu-axis">
                    <div class="imu-bar"><div class="imu-bar-fill" id="imuY"></div></div>
                    <span>Y</span>
                </div>
                <div class="imu-axis">
                    <div class="imu-bar"><div class="imu-bar-fill" id="imuZ"></div></div>
                    <span>Z</span>
                </div>
            </div>
        </div>

        <!-- Control Panel -->
        <div class="panel">
            <h2>🎮 Control</h2>
            <div class="joystick-container">
                <div>
                    <div class="joystick" id="moveJoystick">
                        <div class="joystick-knob" id="moveKnob"></div>
                    </div>
                    <div class="joystick-label">Move (↑↓←→)</div>
                </div>
                <div>
                    <div class="joystick" id="rotateJoystick">
                        <div class="joystick-knob" id="rotateKnob"></div>
                    </div>
                    <div class="joystick-label">Rotate (↻↺)</div>
                </div>
            </div>
            <div class="control-buttons">
                <button class="btn-danger" onclick="emergencyStop()">🛑 E-STOP</button>
                <button class="btn-warning" onclick="stopMotors()">⏹ Stop</button>
            </div>
        </div>

        <!-- Motor Status Panel -->
        <div class="panel">
            <h2>⚙️ Motors</h2>
            <div class="motor-viz">
                <div class="motor-wheel">
                    <div>FL</div>
                    <div class="speed" id="motor1">0.00</div>
                </div>
                <div class="rover-body">ROVER</div>
                <div class="motor-wheel">
                    <div>FR</div>
                    <div class="speed" id="motor2">0.00</div>
                </div>
                <div class="motor-wheel">
                    <div>BL</div>
                    <div class="speed" id="motor3">0.00</div>
                </div>
                <div class="motor-wheel">
                    <div>BR</div>
                    <div class="speed" id="motor4">0.00</div>
                </div>
            </div>
            
            <h3 style="margin: 1rem 0 0.5rem; font-size: 0.9rem; color: #888;">Encoders</h3>
            <div class="telemetry-grid" style="grid-template-columns: repeat(4, 1fr);">
                <div class="telemetry-item">
                    <label>E1</label>
                    <div class="value" id="enc1" style="font-size: 0.9rem;">0</div>
                </div>
                <div class="telemetry-item">
                    <label>E2</label>
                    <div class="value" id="enc2" style="font-size: 0.9rem;">0</div>
                </div>
                <div class="telemetry-item">
                    <label>E3</label>
                    <div class="value" id="enc3" style="font-size: 0.9rem;">0</div>
                </div>
                <div class="telemetry-item">
                    <label>E4</label>
                    <div class="value" id="enc4" style="font-size: 0.9rem;">0</div>
                </div>
            </div>
        </div>

        <!-- BBB Info Panel -->
        <div class="panel">
            <h2>🖥️ BeagleBone Blue</h2>
            <div class="info-grid" id="bbbInfo">
                <div class="info-item"><label>IP</label><div class="value">Loading...</div></div>
            </div>
            <button class="btn-primary" onclick="refreshBBBInfo()" style="margin-top: 1rem; width: 100%;">
                🔄 Refresh
            </button>
        </div>

        <!-- Service Panel -->
        <div class="panel">
            <h2>🔧 Service</h2>
            <div class="service-status">
                <span class="service-badge" id="serviceBadge">Unknown</span>
                <span id="serviceStatus">Checking...</span>
            </div>
            <div style="display: flex; gap: 0.5rem;">
                <button class="btn-success" onclick="startService()">▶ Start</button>
                <button class="btn-danger" onclick="stopService()">⏹ Stop</button>
                <button class="btn-warning" onclick="restartService()">🔄 Restart</button>
            </div>
        </div>

        <!-- OTA Panel -->
        <div class="panel">
            <h2>📤 OTA Update</h2>
            <div class="file-upload" onclick="document.getElementById('fileInput').click()">
                <input type="file" id="fileInput" accept=".py,.txt,.json" onchange="handleFileUpload(event)">
                <p>📁 Click to upload file</p>
                <p style="font-size: 0.8rem; color: #888;">Supports: .py, .txt, .json</p>
            </div>
            <div class="deploy-log" id="deployLog" style="margin-top: 0.5rem; display: none;"></div>
        </div>
    </div>

    <script>
        // WebSocket connections
        let telemetryWs = null;
        let controlWs = null;
        let controlWsReady = false;
        
        // Joystick state
        let moveX = 0, moveY = 0, rotate = 0;
        let lastSentTime = 0;
        const SEND_INTERVAL = 50; // Send at most every 50ms
        const KEEPALIVE_INTERVAL = 100; // Send keepalive every 100ms when moving
        let keepaliveTimer = null;
        
        // Connect to telemetry
        function connectTelemetry() {
            telemetryWs = new WebSocket(`ws://${location.host}/ws/telemetry`);
            
            telemetryWs.onopen = () => {
                document.getElementById('connectionStatus').classList.add('connected');
                document.getElementById('connectionText').textContent = 'Connected';
            };
            
            telemetryWs.onclose = () => {
                document.getElementById('connectionStatus').classList.remove('connected');
                document.getElementById('connectionText').textContent = 'Disconnected';
                setTimeout(connectTelemetry, 2000);
            };
            
            telemetryWs.onmessage = (event) => {
                const data = JSON.parse(event.data);
                updateTelemetry(data);
            };
        }
        
        // Connect to control WebSocket (persistent connection)
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
            
            controlWs.onmessage = (event) => {
                // Handle responses if needed
                const data = JSON.parse(event.data);
                console.log('Control response:', data);
            };
        }
        
        function updateTelemetry(data) {
            // Connection status
            if (data._connected === false) {
                document.getElementById('connectionText').textContent = 'BBB Offline';
                return;
            }
            
            // Battery
            if (data.battery) {
                document.getElementById('battery').textContent = data.battery.voltage.toFixed(2) + 'V';
            }
            
            // System
            if (data.system) {
                document.getElementById('cpu').textContent = data.system.cpu_usage.toFixed(0) + '%';
                document.getElementById('memory').textContent = data.system.memory.percent.toFixed(0) + '%';
            }
            
            // IMU
            if (data.imu && data.imu.accel) {
                updateIMUBar('imuX', data.imu.accel.x);
                updateIMUBar('imuY', data.imu.accel.y);
                updateIMUBar('imuZ', data.imu.accel.z);
            }
            
            // Motors
            if (data.motors) {
                const speeds = data.motors.speeds;
                for (let i = 1; i <= 4; i++) {
                    const el = document.getElementById('motor' + i);
                    const val = speeds['motor_' + i] || speeds[i] || 0;
                    el.textContent = val.toFixed(2);
                    el.className = 'speed ' + (val > 0 ? 'positive' : val < 0 ? 'negative' : '');
                }
                
                // Emergency stop
                if (data.motors.emergency_stop) {
                    document.getElementById('estopOverlay').classList.add('active');
                } else {
                    document.getElementById('estopOverlay').classList.remove('active');
                }
            }
            
            // Encoders
            if (data.encoders) {
                for (let i = 1; i <= 4; i++) {
                    const el = document.getElementById('enc' + i);
                    el.textContent = data.encoders['encoder_' + i] || 0;
                }
            }
        }
        
        function updateIMUBar(id, value) {
            const el = document.getElementById(id);
            const maxG = 2;
            const percent = Math.min(Math.abs(value) / maxG * 50, 50);
            el.style.height = percent + '%';
            el.className = 'imu-bar-fill' + (value < 0 ? ' negative' : '');
        }
        
        // Joystick handling
        function setupJoystick(containerId, knobId, onMove) {
            const container = document.getElementById(containerId);
            const knob = document.getElementById(knobId);
            let active = false;
            
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
                
                // Clamp to circle
                const dist = Math.sqrt(x*x + y*y);
                if (dist > 1) {
                    x /= dist;
                    y /= dist;
                }
                
                return { x, y };
            };
            
            const updateKnob = (x, y) => {
                const rect = container.getBoundingClientRect();
                knob.style.left = (50 + x * 40) + '%';
                knob.style.top = (50 + y * 40) + '%';
            };
            
            const start = (e) => {
                active = true;
                e.preventDefault();
            };
            
            const move = (e) => {
                if (!active) return;
                e.preventDefault();
                const pos = getPosition(e);
                updateKnob(pos.x, pos.y);
                onMove(pos.x, pos.y);
            };
            
            const end = () => {
                active = false;
                updateKnob(0, 0);
                onMove(0, 0);
            };
            
            container.addEventListener('mousedown', start);
            container.addEventListener('touchstart', start);
            document.addEventListener('mousemove', move);
            document.addEventListener('touchmove', move);
            document.addEventListener('mouseup', end);
            document.addEventListener('touchend', end);
        }
        
        setupJoystick('moveJoystick', 'moveKnob', (x, y) => {
            moveX = x;
            moveY = -y;  // Invert Y
            sendControl();
        });
        
        setupJoystick('rotateJoystick', 'rotateKnob', (x, y) => {
            rotate = x;
            sendControl();
        });
        
        function sendControl() {
            // Rate limit sending
            const now = Date.now();
            if (now - lastSentTime < SEND_INTERVAL) return;
            lastSentTime = now;
            
            // Send via persistent WebSocket
            if (controlWs && controlWsReady) {
                controlWs.send(JSON.stringify({
                    type: 'mecanum',
                    vx: moveY,
                    vy: moveX,
                    omega: rotate
                }));
            }
            
            // Start or restart keepalive timer if there's any movement
            if (moveX !== 0 || moveY !== 0 || rotate !== 0) {
                if (keepaliveTimer) clearTimeout(keepaliveTimer);
                keepaliveTimer = setTimeout(sendControl, KEEPALIVE_INTERVAL);
            } else {
                // Stop keepalive when centered
                if (keepaliveTimer) {
                    clearTimeout(keepaliveTimer);
                    keepaliveTimer = null;
                }
            }
        }
        
        // Control buttons
        async function emergencyStop() {
            if (controlWs && controlWsReady) {
                controlWs.send(JSON.stringify({ type: 'emergency_stop' }));
            }
        }
        
        async function stopMotors() {
            if (controlWs && controlWsReady) {
                controlWs.send(JSON.stringify({ type: 'stop' }));
            }
        }
        
        async function resetEmergencyStop() {
            if (controlWs && controlWsReady) {
                controlWs.send(JSON.stringify({ type: 'reset_emergency_stop' }));
            }
        }
        
        // Service management
        async function getServiceStatus() {
            const res = await fetch('/api/service/status');
            const data = await res.json();
            
            const badge = document.getElementById('serviceBadge');
            badge.textContent = data.status;
            badge.className = 'service-badge ' + (data.active ? 'active' : 'inactive');
            document.getElementById('serviceStatus').textContent = data.active ? 'Running' : 'Stopped';
        }
        
        async function startService() {
            await fetch('/api/service/start', { method: 'POST' });
            setTimeout(getServiceStatus, 1000);
        }
        
        async function stopService() {
            await fetch('/api/service/stop', { method: 'POST' });
            setTimeout(getServiceStatus, 1000);
        }
        
        async function restartService() {
            await fetch('/api/service/restart', { method: 'POST' });
            setTimeout(getServiceStatus, 2000);
        }
        
        // BBB Info
        async function refreshBBBInfo() {
            const res = await fetch('/api/bbb/info');
            const data = await res.json();
            
            const container = document.getElementById('bbbInfo');
            container.innerHTML = Object.entries(data).map(([key, value]) => `
                <div class="info-item">
                    <label>${key}</label>
                    <div class="value">${value}</div>
                </div>
            `).join('');
        }
        
        // OTA Update
        async function handleFileUpload(event) {
            const file = event.target.files[0];
            if (!file) return;
            
            const log = document.getElementById('deployLog');
            log.style.display = 'block';
            log.innerHTML = '<div class="deploy-step running">Uploading ' + file.name + '...</div>';
            
            const formData = new FormData();
            formData.append('file', file);
            
            try {
                const res = await fetch('/api/deploy', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                
                log.innerHTML = data.steps.map(step => `
                    <div class="deploy-step ${step.status}">${step.step}</div>
                `).join('');
                
                if (data.success) {
                    log.innerHTML += '<div class="deploy-step done">✓ Deployment complete!</div>';
                } else {
                    log.innerHTML += '<div class="deploy-step error">✗ Deployment failed</div>';
                }
                
                setTimeout(getServiceStatus, 1000);
            } catch (e) {
                log.innerHTML += '<div class="deploy-step error">Error: ' + e.message + '</div>';
            }
            
            event.target.value = '';
        }
        
        // Initialize
        connectTelemetry();
        connectControl();
        getServiceStatus();
        refreshBBBInfo();
        
        // Periodic refresh
        setInterval(getServiceStatus, 10000);
        setInterval(refreshBBBInfo, 30000);
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the dashboard"""
    return DASHBOARD_HTML

# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    print(f"""
╔════════════════════════════════════════════════════════════╗
║           BBB Rover Dashboard                              ║
╠════════════════════════════════════════════════════════════╣
║  Dashboard:  http://localhost:{config.dashboard_port}                       ║
║  BBB Host:   {config.bbb_host}                              ║
╚════════════════════════════════════════════════════════════╝
    """)
    uvicorn.run(app, host="0.0.0.0", port=config.dashboard_port)
