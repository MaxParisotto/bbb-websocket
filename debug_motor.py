#!/usr/bin/env python3
"""
Debug test for motor control on BeagleBone Blue
Run this ON the BBB to test motor functionality
"""

import ctypes
from ctypes.util import find_library
import time

def main():
    lib_path = find_library("robotcontrol")
    if not lib_path:
        print("ERROR: librobotcontrol not found!")
        return
    
    print(f"Loading library: {lib_path}")
    lib = ctypes.CDLL(lib_path)
    
    # Set up function signatures exactly as in bbb-server.py
    lib.rc_motor_init.argtypes = []
    lib.rc_motor_init.restype = ctypes.c_int
    
    lib.rc_motor_set.argtypes = [ctypes.c_int, ctypes.c_float]
    lib.rc_motor_set.restype = ctypes.c_int
    
    lib.rc_motor_cleanup.argtypes = []
    lib.rc_motor_cleanup.restype = ctypes.c_int
    
    # Initialize
    print("\n=== Initializing motors ===")
    ret = lib.rc_motor_init()
    print(f"rc_motor_init() = {ret}")
    if ret != 0:
        print("FAILED to initialize motors!")
        return
    
    # Test setting motors - EXACTLY like bbb-server.py does
    print("\n=== Testing motor 1 at 50% for 3 seconds ===")
    speed = 0.5
    motor_id = 1
    
    # This is EXACTLY what bbb-server.py does:
    ret = lib.rc_motor_set(motor_id, ctypes.c_float(speed))
    print(f"rc_motor_set({motor_id}, c_float({speed})) = {ret}")
    
    if ret != 0:
        print("MOTOR SET FAILED!")
    else:
        print("Motor should be running now...")
    
    time.sleep(3)
    
    # Stop
    print("\n=== Stopping motor ===")
    ret = lib.rc_motor_set(motor_id, ctypes.c_float(0.0))
    print(f"rc_motor_set({motor_id}, c_float(0.0)) = {ret}")
    
    # Cleanup
    print("\n=== Cleanup ===")
    lib.rc_motor_cleanup()
    print("Done!")

if __name__ == "__main__":
    main()
