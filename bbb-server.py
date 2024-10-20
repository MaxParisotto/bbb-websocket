import ctypes
from fastapi import FastAPI, WebSocket
import asyncio
import psutil
import json
from ctypes.util import find_library

# Attempt to find and load the shared library
lib_path = find_library("robotcontrol")
if lib_path is None:
    print("Error: librobotcontrol.so not found")
    exit(1)
robotcontrol_lib = ctypes.CDLL(lib_path)

# Initialize robot control
if robotcontrol_lib.rc_initialize() != 0:
    print("Error: Failed to initialize robot control")
    exit(1)
    
# Initialize PRU for encoders (after initializing robot control)
if robotcontrol_lib.rc_encoder_pru_init() != 0:
    print("Error: Failed to initialize PRU for encoders")
    exit(1)

# IMU data structure
class IMUData(ctypes.Structure):
    _fields_ = [
        ("gyro", ctypes.c_float * 3),
        ("accel", ctypes.c_float * 3),
        ("mag", ctypes.c_float * 3),
        ("temp", ctypes.c_float)
    ]

imu_data = IMUData()

# FastAPI setup
app = FastAPI()

# Motor control function
def set_motor(motor_id, speed):
    if robotcontrol_lib.rc_motor_set(motor_id, ctypes.c_float(speed)) != 0:
        print(f"Error: Failed to set motor {motor_id} speed")

# WebSocket handler for motor control
@app.websocket("/ws/motors")
async def motors_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            # Control all four motors, assuming keys are 'motor_1', 'motor_2', etc.
            for i in range(1, 5):
                set_motor(i, data.get(f"motor_{i}", 0.0))
            await websocket.send_text("Motor speeds updated")
    except Exception as e:
        print(f"Error in motor WebSocket control: {e}")

# Servo control function (example usage)
def set_servo(channel, position):
    robotcontrol_lib.rc_servo_send_pulse_us(channel, ctypes.c_int(position))

# Function to read IMU data with error checking
def read_imu_data():
    if robotcontrol_lib.rc_imu_read(ctypes.byref(imu_data)) != 0:
        print("Error: Failed to read IMU data")
        return None
    else:
        return {
            "gyro": list(imu_data.gyro),
            "accel": list(imu_data.accel),
            "mag": list(imu_data.mag),
            "temp": imu_data.temp
        }

# WebSocket endpoint for IMU data
@app.websocket("/ws/imu")
async def imu_data_stream(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            imu_data_read = read_imu_data()
            if imu_data_read:
                await websocket.send_json(imu_data_read)
            await asyncio.sleep(0.1)  # Send updates every 100ms
    except Exception as e:
        print(f"Error in IMU WebSocket stream: {e}")

# Function to get encoder data using real library call
def get_encoder(encoder_id):
    # Call the actual encoder reading function from the library
    # Assuming encoder_id ranges from 1 to 4 based on your motor setup
    encoder_value = robotcontrol_lib.rc_encoder_read(ctypes.c_int(encoder_id))
    if encoder_value == -1:
        print(f"Error: Failed to read encoder {encoder_id}")
        return None
    return encoder_value

# Example usage in WebSocket endpoint
@app.websocket("/ws/encoder")
async def encoder_data_stream(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            enc_data = {f"encoder_{i}": get_encoder(i) for i in range(1, 5)}
            await websocket.send_json(enc_data)
            await asyncio.sleep(0.1)  # Send updates every 100ms
    except Exception as e:
        print(f"Error in encoder WebSocket stream: {e}")

# Function to get battery voltage
def get_battery_voltage():
    voltage = robotcontrol_lib.rc_battery_voltage()
    if voltage == -1:
        print("Error: Failed to read battery voltage")
    return voltage

# WebSocket endpoint for battery monitoring
@app.websocket("/ws/battery")
async def battery_monitoring(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            voltage = get_battery_voltage()
            await websocket.send_json({"voltage": voltage})
            await asyncio.sleep(1)  # Send updates every 1s
    except Exception as e:
        print(f"Error in battery WebSocket stream: {e}")

# CPU, Memory, and Network monitoring function
def get_system_metrics():
    cpu_usage = psutil.cpu_percent(interval=1)
    memory_info = psutil.virtual_memory()
    net_info = psutil.net_if_addrs()
    return {
        "cpu_usage": cpu_usage,
        "memory": {
            "total": memory_info.total,
            "available": memory_info.available,
            "used": memory_info.used,
            "percent": memory_info.percent
        },
        "network": {
            iface: [{"ip": addr.address, "netmask": addr.netmask} for addr in addrs if addr.family == 2]
            for iface, addrs in net_info.items()
        }
    }

# WebSocket endpoint for system metrics
@app.websocket("/ws/system_metrics")
async def system_metrics_stream(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            sys_metrics = get_system_metrics()
            await websocket.send_json(sys_metrics)
            await asyncio.sleep(1)  # Send updates every 1s
    except Exception as e:
        print(f"Error in system metrics WebSocket stream: {e}")

# WebSocket endpoint for servo control (preparing for future use)
@app.websocket("/ws/servo")
async def servo_control(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            # Control all servos, assuming keys are 'servo_1', 'servo_2', etc.
            for i in range(1, 9):  # Example with 8 servo channels
                set_servo(i, data.get(f"servo_{i}", 1500))  # Default position: 1500 us pulse width
            await websocket.send_text("Servo positions updated")
    except Exception as e:
        print(f"Error in servo WebSocket control: {e}")

# Start the server
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)