"""
BeagleBone Blue Robot Control Server

WebSocket server for controlling a 4-wheel mecanum robot with:
- 4 DC motors with encoders
- IMU sensor
- Battery monitoring
- System metrics

Safety features:
- Watchdog timer (auto-stop if no commands received)
- Emergency stop
- Motor speed clamping
- Graceful disconnect handling
"""

import ctypes
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import asyncio
import psutil
import os
import time
import math
from typing import Optional, Dict, Any, Set
from dataclasses import dataclass, field
from ctypes.util import find_library
import logging
import atexit

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
class RobotConfig:
    """Robot configuration parameters"""
    # Motor settings
    max_motor_speed: float = 1.0  # Maximum motor speed (-1.0 to 1.0)
    motor_count: int = 4
    
    # Watchdog settings
    watchdog_timeout: float = 1.0  # Seconds without command before stopping
    
    # Sensor update rates (Hz)
    imu_rate: float = 50.0  # 50 Hz
    encoder_rate: float = 50.0  # 50 Hz
    battery_rate: float = 1.0  # 1 Hz
    system_metrics_rate: float = 1.0  # 1 Hz
    
    # Servo settings
    servo_count: int = 8
    servo_min_pulse: int = 500
    servo_max_pulse: int = 2500
    servo_default_pulse: int = 1500

config = RobotConfig()

# ============================================================================
# MPU/IMU Structures (must be defined before RobotControlLib)
# ============================================================================

# MPU Configuration structure (matches rc_mpu_config_t exactly - 88 bytes)
class MPUConfig(ctypes.Structure):
    _fields_ = [
        ("gpio_interrupt_pin_chip", ctypes.c_int),
        ("gpio_interrupt_pin", ctypes.c_int),
        ("i2c_bus", ctypes.c_int),
        ("i2c_addr", ctypes.c_uint8),
        ("show_warnings", ctypes.c_int),
        ("accel_fsr", ctypes.c_int),
        ("gyro_fsr", ctypes.c_int),
        ("accel_dlpf", ctypes.c_int),
        ("gyro_dlpf", ctypes.c_int),
        ("enable_magnetometer", ctypes.c_int),
        ("dmp_sample_rate", ctypes.c_int),
        ("dmp_fetch_accel_gyro", ctypes.c_int),
        ("dmp_auto_calibrate_gyro", ctypes.c_int),
        ("orient", ctypes.c_int),
        ("compass_time_constant", ctypes.c_double),
        ("dmp_interrupt_sched_policy", ctypes.c_int),
        ("dmp_interrupt_priority", ctypes.c_int),
        ("read_mag_after_callback", ctypes.c_int),
        ("mag_sample_rate_div", ctypes.c_int),
        ("tap_threshold", ctypes.c_int),
    ]

# MPU Data structure (matches rc_mpu_data_t exactly - 256 bytes)
class MPUData(ctypes.Structure):
    _fields_ = [
        # Base sensor readings in real units
        ("accel", ctypes.c_double * 3),           # accelerometer (XYZ) in m/s^2
        ("gyro", ctypes.c_double * 3),            # gyroscope (XYZ) in degrees/s
        ("mag", ctypes.c_double * 3),             # magnetometer (XYZ) in uT
        ("temp", ctypes.c_double),                # thermometer in Celsius
        # 16-bit raw ADC readings and conversion rates
        ("raw_gyro", ctypes.c_int16 * 3),         # raw gyroscope from 16-bit ADC
        ("raw_accel", ctypes.c_int16 * 3),        # raw accelerometer from 16-bit ADC
        ("accel_to_ms2", ctypes.c_double),        # conversion rate raw accel to m/s^2
        ("gyro_to_degs", ctypes.c_double),        # conversion rate raw gyro to degrees/s
        # DMP data
        ("dmp_quat", ctypes.c_double * 4),        # normalized quaternion from DMP
        ("dmp_TaitBryan", ctypes.c_double * 3),   # Tait-Bryan angles (roll/pitch/yaw) in radians
        ("tap_detected", ctypes.c_int),           # 1 if tap detected on last sample
        ("last_tap_direction", ctypes.c_int),     # direction 1-6: X+ X- Y+ Y- Z+ Z-
        ("last_tap_count", ctypes.c_int),         # counter of rapid consecutive taps
        # Fused DMP data filtered with magnetometer
        ("fused_quat", ctypes.c_double * 4),      # fused and normalized quaternion
        ("fused_TaitBryan", ctypes.c_double * 3), # fused Tait-Bryan angles in radians
        ("compass_heading", ctypes.c_double),    # fused heading filtered with gyro/accel
        ("compass_heading_raw", ctypes.c_double), # unfiltered heading from magnetometer
    ]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "gyro": {"x": self.gyro[0], "y": self.gyro[1], "z": self.gyro[2]},
            "accel": {"x": self.accel[0], "y": self.accel[1], "z": self.accel[2]},
            "mag": {"x": self.mag[0], "y": self.mag[1], "z": self.mag[2]},
            "temp": self.temp,
            "compass_heading": self.compass_heading,
        }

# Legacy alias for compatibility
IMUData = MPUData

# ============================================================================
# Robot Control Library Interface
# ============================================================================

class RobotControlLib:
    """Wrapper for librobotcontrol with proper ctypes typing"""
    
    def __init__(self):
        lib_path = find_library("robotcontrol")
        if lib_path is None:
            raise RuntimeError("librobotcontrol.so not found")
        
        self._lib = ctypes.CDLL(lib_path)
        self._setup_function_signatures()
        
    def _setup_function_signatures(self):
        """Define proper return types and argument types for safety"""
        # Motor functions
        self._lib.rc_motor_set.argtypes = [ctypes.c_int, ctypes.c_double]
        self._lib.rc_motor_set.restype = ctypes.c_int
        
        self._lib.rc_motor_free_spin.argtypes = [ctypes.c_int]
        self._lib.rc_motor_free_spin.restype = ctypes.c_int
        
        self._lib.rc_motor_brake.argtypes = [ctypes.c_int]
        self._lib.rc_motor_brake.restype = ctypes.c_int
        
        self._lib.rc_motor_init.argtypes = []
        self._lib.rc_motor_init.restype = ctypes.c_int
        
        self._lib.rc_motor_cleanup.argtypes = []
        self._lib.rc_motor_cleanup.restype = ctypes.c_int
        
        # Encoder functions
        self._lib.rc_encoder_read.argtypes = [ctypes.c_int]
        self._lib.rc_encoder_read.restype = ctypes.c_int
        
        self._lib.rc_encoder_pru_init.argtypes = []
        self._lib.rc_encoder_pru_init.restype = ctypes.c_int
        
        # ADC/Battery functions
        self._lib.rc_adc_init.argtypes = []
        self._lib.rc_adc_init.restype = ctypes.c_int
        
        self._lib.rc_adc_batt.argtypes = []
        self._lib.rc_adc_batt.restype = ctypes.c_double
        
        self._lib.rc_adc_cleanup.argtypes = []
        self._lib.rc_adc_cleanup.restype = ctypes.c_int
        
        # Servo functions
        self._lib.rc_servo_send_pulse_us.argtypes = [ctypes.c_int, ctypes.c_int]
        self._lib.rc_servo_send_pulse_us.restype = ctypes.c_int
        
        self._lib.rc_servo_init.argtypes = []
        self._lib.rc_servo_init.restype = ctypes.c_int
        
        self._lib.rc_servo_cleanup.argtypes = []
        self._lib.rc_servo_cleanup.restype = ctypes.c_int
        
        # MPU/IMU functions
        # NOTE: rc_mpu_initialize takes config BY VALUE (not pointer!)
        self._lib.rc_mpu_set_config_to_default.argtypes = [ctypes.POINTER(MPUConfig)]
        self._lib.rc_mpu_set_config_to_default.restype = ctypes.c_int
        
        # Config is passed by value, data is passed by pointer
        self._lib.rc_mpu_initialize.argtypes = [ctypes.POINTER(MPUData), MPUConfig]
        self._lib.rc_mpu_initialize.restype = ctypes.c_int
        
        self._lib.rc_mpu_read_accel.argtypes = [ctypes.c_void_p]
        self._lib.rc_mpu_read_accel.restype = ctypes.c_int
        
        self._lib.rc_mpu_read_gyro.argtypes = [ctypes.c_void_p]
        self._lib.rc_mpu_read_gyro.restype = ctypes.c_int
        
        self._lib.rc_mpu_read_temp.argtypes = [ctypes.c_void_p]
        self._lib.rc_mpu_read_temp.restype = ctypes.c_int
        
        self._lib.rc_mpu_power_off.argtypes = []
        self._lib.rc_mpu_power_off.restype = ctypes.c_int
        
        # Initialize/cleanup
        self._lib.rc_initialize.argtypes = []
        self._lib.rc_initialize.restype = ctypes.c_int
        
        self._lib.rc_cleanup.argtypes = []
        self._lib.rc_cleanup.restype = ctypes.c_int
    
    def initialize(self) -> bool:
        return self._lib.rc_initialize() == 0
    
    def init_motors(self) -> bool:
        return self._lib.rc_motor_init() == 0
    
    def cleanup_motors(self):
        self._lib.rc_motor_cleanup()
    
    def init_encoders(self) -> bool:
        return self._lib.rc_encoder_pru_init() == 0
    
    def cleanup(self):
        self._lib.rc_cleanup()
    
    def set_motor(self, motor_id: int, speed: float) -> bool:
        # Clamp speed to safe range
        speed = max(-config.max_motor_speed, min(config.max_motor_speed, speed))
        result = self._lib.rc_motor_set(motor_id, ctypes.c_double(speed))
        if result != 0:
            logger.error(f"rc_motor_set({motor_id}, {speed:.3f}) failed with code {result}")
        return result == 0
    
    def brake_motor(self, motor_id: int) -> bool:
        return self._lib.rc_motor_brake(motor_id) == 0
    
    def free_spin_motor(self, motor_id: int) -> bool:
        return self._lib.rc_motor_free_spin(motor_id) == 0
    
    def read_encoder(self, encoder_id: int) -> Optional[int]:
        value = self._lib.rc_encoder_read(encoder_id)
        return value if value != -1 else None
    
    def init_adc(self) -> bool:
        return self._lib.rc_adc_init() == 0
    
    def read_battery_voltage(self) -> Optional[float]:
        voltage = self._lib.rc_adc_batt()
        return voltage if voltage > 0 else None
    
    def cleanup_adc(self):
        self._lib.rc_adc_cleanup()
    
    def set_servo(self, channel: int, pulse_us: int) -> bool:
        # Clamp pulse to safe range
        pulse_us = max(config.servo_min_pulse, min(config.servo_max_pulse, pulse_us))
        return self._lib.rc_servo_send_pulse_us(channel, pulse_us) == 0
    
    def init_servo(self) -> bool:
        return self._lib.rc_servo_init() == 0
    
    def cleanup_servo(self):
        self._lib.rc_servo_cleanup()
    
    def init_mpu(self, mpu_config: 'MPUConfig', mpu_data: 'MPUData') -> bool:
        self._lib.rc_mpu_set_config_to_default(ctypes.byref(mpu_config))
        # NOTE: rc_mpu_initialize takes config by VALUE (not pointer), data by pointer
        return self._lib.rc_mpu_initialize(ctypes.byref(mpu_data), mpu_config) == 0
    
    def read_accel(self, mpu_data: 'MPUData') -> bool:
        return self._lib.rc_mpu_read_accel(ctypes.byref(mpu_data)) == 0
    
    def read_gyro(self, mpu_data: 'MPUData') -> bool:
        return self._lib.rc_mpu_read_gyro(ctypes.byref(mpu_data)) == 0
    
    def read_temp(self, mpu_data: 'MPUData') -> bool:
        return self._lib.rc_mpu_read_temp(ctypes.byref(mpu_data)) == 0
    
    def power_off_mpu(self):
        self._lib.rc_mpu_power_off()


# ============================================================================
# Motor Controller with Watchdog
# ============================================================================

class MotorController:
    """Motor controller with watchdog safety feature"""
    
    def __init__(self, robot_lib: RobotControlLib):
        self._lib = robot_lib
        self._motor_speeds: Dict[int, float] = {i: 0.0 for i in range(1, config.motor_count + 1)}
        self._last_command_time: float = 0.0
        self._emergency_stop: bool = False
        self._watchdog_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
    
    async def start_watchdog(self):
        """Start the watchdog timer task"""
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        logger.info("Motor watchdog started")
    
    async def stop_watchdog(self):
        """Stop the watchdog timer task"""
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
        logger.info("Motor watchdog stopped")
    
    async def _watchdog_loop(self):
        """Watchdog loop - stops motors if no commands received"""
        while True:
            await asyncio.sleep(config.watchdog_timeout / 2)
            
            if self._emergency_stop:
                continue
                
            elapsed = time.time() - self._last_command_time
            if elapsed > config.watchdog_timeout and any(s != 0 for s in self._motor_speeds.values()):
                logger.warning(f"Watchdog timeout ({elapsed:.2f}s) - stopping motors")
                await self.stop_all()
    
    async def set_motor(self, motor_id: int, speed: float) -> bool:
        """Set motor speed with safety checks"""
        if self._emergency_stop:
            logger.warning("Emergency stop active - ignoring motor command")
            return False
        
        if motor_id < 1 or motor_id > config.motor_count:
            logger.error(f"Invalid motor ID: {motor_id}")
            return False
        
        async with self._lock:
            self._last_command_time = time.time()
            self._motor_speeds[motor_id] = speed
            return self._lib.set_motor(motor_id, speed)
    
    async def set_all_motors(self, speeds: Dict[int, float]) -> bool:
        """Set all motor speeds atomically"""
        if self._emergency_stop:
            logger.warning("Emergency stop active - ignoring motor command")
            return False
        
        async with self._lock:
            self._last_command_time = time.time()
            success = True
            for motor_id, speed in speeds.items():
                if 1 <= motor_id <= config.motor_count:
                    self._motor_speeds[motor_id] = speed
                    result = self._lib.set_motor(motor_id, speed)
                    if not result:
                        logger.error(f"Failed to set motor {motor_id} to {speed}")
                        success = False
                    else:
                        logger.debug(f"Motor {motor_id} set to {speed:.3f}")
            if success:
                logger.info(f"Motors set: {speeds}")
            return success
    
    async def stop_all(self):
        """Stop all motors immediately"""
        async with self._lock:
            for i in range(1, config.motor_count + 1):
                self._motor_speeds[i] = 0.0
                self._lib.set_motor(i, 0.0)
        logger.info("All motors stopped")
    
    async def emergency_stop(self):
        """Activate emergency stop - brakes all motors"""
        self._emergency_stop = True
        async with self._lock:
            for i in range(1, config.motor_count + 1):
                self._motor_speeds[i] = 0.0
                self._lib.brake_motor(i)
        logger.warning("EMERGENCY STOP ACTIVATED")
    
    async def reset_emergency_stop(self):
        """Reset emergency stop"""
        self._emergency_stop = False
        self._last_command_time = time.time()
        logger.info("Emergency stop reset")
    
    @property
    def is_emergency_stopped(self) -> bool:
        return self._emergency_stop
    
    @property
    def motor_speeds(self) -> Dict[int, float]:
        return self._motor_speeds.copy()

# ============================================================================
# Mecanum Wheel Kinematics
# ============================================================================

class MecanumKinematics:
    """
    Mecanum wheel kinematics for omnidirectional movement.
    
    Wheel arrangement (top view):
        FL [1] \\  / [2] FR
        BL [3] /  \\ [4] BR
    """
    
    @staticmethod
    def compute_wheel_speeds(
        vx: float,      # Forward/backward (-1 to 1)
        vy: float,      # Left/right strafe (-1 to 1)
        omega: float    # Rotation (-1 to 1, positive = clockwise)
    ) -> Dict[int, float]:
        """
        Convert desired robot velocity to individual wheel speeds.
        
        Args:
            vx: Forward velocity (-1 to 1, positive = forward)
            vy: Lateral velocity (-1 to 1, positive = right)
            omega: Angular velocity (-1 to 1, positive = clockwise)
        
        Returns:
            Dictionary of motor_id -> speed for all 4 wheels
        """
        # Mecanum wheel mixing
        # FL = vx + vy + omega
        # FR = vx - vy - omega
        # BL = vx - vy + omega
        # BR = vx + vy - omega
        
        fl = vx + vy + omega  # Motor 1 - Front Left
        fr = vx - vy - omega  # Motor 2 - Front Right
        rr = vx + vy - omega  # Motor 3 - Rear Right
        rl = vx - vy + omega  # Motor 4 - Rear Left
        
        speeds = {1: fl, 2: fr, 3: rr, 4: rl}
        
        # Normalize if any speed exceeds 1.0
        max_speed = max(abs(s) for s in speeds.values())
        if max_speed > 1.0:
            speeds = {k: v / max_speed for k, v in speeds.items()}
        
        return speeds

# ============================================================================
# Connection Manager
# ============================================================================

class ConnectionManager:
    """Manages WebSocket connections with heartbeat"""
    
    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info(f"Client connected. Total connections: {len(self._connections)}")
    
    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self._connections.discard(websocket)
        logger.info(f"Client disconnected. Total connections: {len(self._connections)}")
    
    @property
    def connection_count(self) -> int:
        return len(self._connections)

# ============================================================================
# System Metrics (Non-blocking)
# ============================================================================

_cached_cpu_percent: float = 0.0
_cpu_update_task: Optional[asyncio.Task] = None

async def _update_cpu_percent():
    """Background task to update CPU percent without blocking"""
    global _cached_cpu_percent
    while True:
        # Run in executor to avoid blocking
        loop = asyncio.get_event_loop()
        _cached_cpu_percent = await loop.run_in_executor(
            None, lambda: psutil.cpu_percent(interval=1)
        )

def get_system_metrics() -> Dict[str, Any]:
    """Get system metrics without blocking"""
    memory_info = psutil.virtual_memory()
    net_info = psutil.net_if_addrs()
    
    return {
        "cpu_usage": _cached_cpu_percent,
        "memory": {
            "total": memory_info.total,
            "available": memory_info.available,
            "used": memory_info.used,
            "percent": memory_info.percent
        },
        "network": {
            iface: [{"ip": addr.address, "netmask": addr.netmask} 
                    for addr in addrs if addr.family == 2]
            for iface, addrs in net_info.items()
        }
    }

# ============================================================================
# Global State
# ============================================================================

robot_lib: Optional[RobotControlLib] = None
motor_controller: Optional[MotorController] = None
connection_manager = ConnectionManager()
mpu_config = MPUConfig()
mpu_data = MPUData()
imu_data = mpu_data  # Alias for compatibility

# ============================================================================
# Application Lifecycle
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle manager"""
    global robot_lib, motor_controller, _cpu_update_task
    
    # Startup
    logger.info("Starting BeagleBone Blue Robot Control Server")
    
    # Check for root privileges
    if os.geteuid() != 0:
        logger.error("You need to run this script as root (use sudo)")
        raise RuntimeError("Root privileges required")
    
    # Initialize robot control library
    robot_lib = RobotControlLib()
    
    if not robot_lib.initialize():
        raise RuntimeError("Failed to initialize robot control")
    logger.info("Robot control initialized")
    
    if not robot_lib.init_encoders():
        raise RuntimeError("Failed to initialize PRU for encoders")
    logger.info("Encoder PRU initialized")
    
    # Initialize ADC for battery monitoring
    if not robot_lib.init_adc():
        logger.warning("Failed to initialize ADC - battery monitoring may not work")
    else:
        logger.info("ADC initialized")
    
    # Initialize motors
    if not robot_lib.init_motors():
        logger.warning("Failed to initialize motors")
    else:
        logger.info("Motors initialized")
    
    # Initialize MPU/IMU
    if not robot_lib.init_mpu(mpu_config, mpu_data):
        logger.warning("Failed to initialize MPU - IMU data may not work")
    else:
        logger.info("MPU initialized")
    
    # Initialize motor controller with watchdog
    motor_controller = MotorController(robot_lib)
    await motor_controller.start_watchdog()
    
    # Start CPU monitoring background task
    _cpu_update_task = asyncio.create_task(_update_cpu_percent())
    
    logger.info("Server ready")
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    
    # Stop all motors
    if motor_controller:
        await motor_controller.stop_all()
        await motor_controller.stop_watchdog()
    
    # Cancel CPU update task
    if _cpu_update_task:
        _cpu_update_task.cancel()
        try:
            await _cpu_update_task
        except asyncio.CancelledError:
            pass
    
    # Cleanup robot control
    if robot_lib:
        robot_lib.power_off_mpu()
        robot_lib.cleanup_adc()
        robot_lib.cleanup_motors()
        robot_lib.cleanup()
    
    logger.info("Cleanup complete")

# FastAPI setup
app = FastAPI(
    title="BeagleBone Blue Robot Control",
    description="WebSocket server for mecanum wheel robot control",
    version="2.0.0",
    lifespan=lifespan
)

# ============================================================================
# REST Endpoints
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "ok",
        "connections": connection_manager.connection_count,
        "emergency_stop": motor_controller.is_emergency_stopped if motor_controller else None
    }

@app.post("/emergency_stop")
async def trigger_emergency_stop():
    """Trigger emergency stop"""
    if motor_controller:
        await motor_controller.emergency_stop()
        return {"status": "emergency_stop_activated"}
    return JSONResponse(status_code=503, content={"error": "Motor controller not initialized"})

@app.post("/reset_emergency_stop")
async def reset_estop():
    """Reset emergency stop"""
    if motor_controller:
        await motor_controller.reset_emergency_stop()
        return {"status": "emergency_stop_reset"}
    return JSONResponse(status_code=503, content={"error": "Motor controller not initialized"})

# ============================================================================
# WebSocket Endpoints
# ============================================================================

@app.websocket("/ws/control")
async def control_endpoint(websocket: WebSocket):
    """
    Main control WebSocket endpoint.
    
    Accepts commands:
    - {"type": "motor", "motor_1": 0.5, "motor_2": 0.5, ...}
    - {"type": "mecanum", "vx": 0.5, "vy": 0.0, "omega": 0.0}
    - {"type": "servo", "servo_1": 1500, ...}
    - {"type": "stop"}
    - {"type": "emergency_stop"}
    - {"type": "reset_emergency_stop"}
    - {"type": "ping"} -> responds with {"type": "pong"}
    """
    await connection_manager.connect(websocket)
    
    try:
        while True:
            data = await websocket.receive_json()
            cmd_type = data.get("type", "")
            
            if cmd_type == "ping":
                await websocket.send_json({"type": "pong", "timestamp": time.time()})
            
            elif cmd_type == "motor":
                speeds = {i: data.get(f"motor_{i}", 0.0) for i in range(1, config.motor_count + 1)}
                success = await motor_controller.set_all_motors(speeds)
                await websocket.send_json({
                    "type": "motor_response",
                    "success": success,
                    "speeds": motor_controller.motor_speeds
                })
            
            elif cmd_type == "mecanum":
                vx = data.get("vx", 0.0)
                vy = data.get("vy", 0.0)
                omega = data.get("omega", 0.0)
                logger.info(f"Received mecanum command: vx={vx}, vy={vy}, omega={omega}")
                speeds = MecanumKinematics.compute_wheel_speeds(vx, vy, omega)
                logger.info(f"Computed wheel speeds: {speeds}")
                success = await motor_controller.set_all_motors(speeds)
                await websocket.send_json({
                    "type": "mecanum_response",
                    "success": success,
                    "input": {"vx": vx, "vy": vy, "omega": omega},
                    "wheel_speeds": speeds
                })
            
            elif cmd_type == "servo":
                for i in range(1, config.servo_count + 1):
                    pulse = data.get(f"servo_{i}")
                    if pulse is not None:
                        robot_lib.set_servo(i, int(pulse))
                await websocket.send_json({"type": "servo_response", "success": True})
            
            elif cmd_type == "stop":
                await motor_controller.stop_all()
                await websocket.send_json({"type": "stop_response", "success": True})
            
            elif cmd_type == "emergency_stop":
                await motor_controller.emergency_stop()
                await websocket.send_json({"type": "emergency_stop_response", "success": True})
            
            elif cmd_type == "reset_emergency_stop":
                await motor_controller.reset_emergency_stop()
                await websocket.send_json({"type": "reset_emergency_stop_response", "success": True})
            
            else:
                await websocket.send_json({"type": "error", "message": f"Unknown command type: {cmd_type}"})
    
    except WebSocketDisconnect:
        logger.info("Control client disconnected - stopping motors for safety")
        await motor_controller.stop_all()
    except Exception as e:
        logger.error(f"Error in control WebSocket: {e}")
        await motor_controller.stop_all()
    finally:
        await connection_manager.disconnect(websocket)


@app.websocket("/ws/telemetry")
async def telemetry_endpoint(websocket: WebSocket):
    """
    Consolidated telemetry WebSocket endpoint.
    
    Streams all sensor data at configured rates:
    - IMU (50 Hz)
    - Encoders (50 Hz)
    - Battery (1 Hz)
    - System metrics (1 Hz)
    - Motor status (with each update)
    """
    await connection_manager.connect(websocket)
    
    last_imu = 0.0
    last_encoder = 0.0
    last_battery = 0.0
    last_system = 0.0
    
    imu_interval = 1.0 / config.imu_rate
    encoder_interval = 1.0 / config.encoder_rate
    battery_interval = 1.0 / config.battery_rate
    system_interval = 1.0 / config.system_metrics_rate
    
    try:
        while True:
            now = time.time()
            telemetry = {"timestamp": now}
            
            # IMU data
            if now - last_imu >= imu_interval:
                # Read all MPU sensors
                robot_lib.read_accel(mpu_data)
                robot_lib.read_gyro(mpu_data)
                robot_lib.read_temp(mpu_data)
                telemetry["imu"] = mpu_data.to_dict()
                last_imu = now
            
            # Encoder data
            if now - last_encoder >= encoder_interval:
                enc_data = {}
                for i in range(1, config.motor_count + 1):
                    value = robot_lib.read_encoder(i)
                    if value is not None:
                        enc_data[f"encoder_{i}"] = value
                telemetry["encoders"] = enc_data
                last_encoder = now
            
            # Battery data
            if now - last_battery >= battery_interval:
                voltage = robot_lib.read_battery_voltage()
                if voltage is not None:
                    telemetry["battery"] = {"voltage": voltage}
                last_battery = now
            
            # System metrics
            if now - last_system >= system_interval:
                telemetry["system"] = get_system_metrics()
                last_system = now
            
            # Motor status (always include)
            telemetry["motors"] = {
                "speeds": motor_controller.motor_speeds,
                "emergency_stop": motor_controller.is_emergency_stopped
            }
            
            await websocket.send_json(telemetry)
            
            # Sleep for the fastest update rate
            await asyncio.sleep(min(imu_interval, encoder_interval) / 2)
    
    except WebSocketDisconnect:
        logger.info("Telemetry client disconnected")
    except Exception as e:
        logger.error(f"Error in telemetry WebSocket: {e}")
    finally:
        await connection_manager.disconnect(websocket)


# Legacy endpoints for backward compatibility
@app.websocket("/ws/motors")
async def motors_endpoint(websocket: WebSocket):
    """Legacy motor control endpoint"""
    await connection_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            speeds = {i: data.get(f"motor_{i}", 0.0) for i in range(1, config.motor_count + 1)}
            await motor_controller.set_all_motors(speeds)
            await websocket.send_json({"status": "ok", "speeds": motor_controller.motor_speeds})
    except WebSocketDisconnect:
        await motor_controller.stop_all()
    except Exception as e:
        logger.error(f"Error in motor WebSocket: {e}")
        await motor_controller.stop_all()
    finally:
        await connection_manager.disconnect(websocket)


@app.websocket("/ws/imu")
async def imu_data_stream(websocket: WebSocket):
    """Legacy IMU data endpoint"""
    await connection_manager.connect(websocket)
    try:
        while True:
            robot_lib.read_accel(mpu_data)
            robot_lib.read_gyro(mpu_data)
            robot_lib.read_temp(mpu_data)
            await websocket.send_json(mpu_data.to_dict())
            await asyncio.sleep(1.0 / config.imu_rate)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Error in IMU WebSocket: {e}")
    finally:
        await connection_manager.disconnect(websocket)


@app.websocket("/ws/encoder")
async def encoder_data_stream(websocket: WebSocket):
    """Legacy encoder data endpoint"""
    await connection_manager.connect(websocket)
    try:
        while True:
            enc_data = {f"encoder_{i}": robot_lib.read_encoder(i) for i in range(1, config.motor_count + 1)}
            await websocket.send_json(enc_data)
            await asyncio.sleep(1.0 / config.encoder_rate)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Error in encoder WebSocket: {e}")
    finally:
        await connection_manager.disconnect(websocket)


@app.websocket("/ws/battery")
async def battery_monitoring(websocket: WebSocket):
    """Legacy battery monitoring endpoint"""
    await connection_manager.connect(websocket)
    try:
        while True:
            voltage = robot_lib.read_battery_voltage()
            await websocket.send_json({"voltage": voltage})
            await asyncio.sleep(1.0 / config.battery_rate)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Error in battery WebSocket: {e}")
    finally:
        await connection_manager.disconnect(websocket)


@app.websocket("/ws/system_metrics")
async def system_metrics_stream(websocket: WebSocket):
    """Legacy system metrics endpoint"""
    await connection_manager.connect(websocket)
    try:
        while True:
            await websocket.send_json(get_system_metrics())
            await asyncio.sleep(1.0 / config.system_metrics_rate)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Error in system metrics WebSocket: {e}")
    finally:
        await connection_manager.disconnect(websocket)


@app.websocket("/ws/servo")
async def servo_control(websocket: WebSocket):
    """Legacy servo control endpoint"""
    await connection_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            for i in range(1, config.servo_count + 1):
                pulse = data.get(f"servo_{i}", config.servo_default_pulse)
                robot_lib.set_servo(i, int(pulse))
            await websocket.send_json({"status": "ok"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Error in servo WebSocket: {e}")
    finally:
        await connection_manager.disconnect(websocket)


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)