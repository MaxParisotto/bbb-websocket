#!/usr/bin/env python3
"""Direct motor test on BeagleBone Blue - run this ON the BBB"""

import ctypes
from ctypes.util import find_library
import time

def test_motors():
    lib_path = find_library("robotcontrol")
    if not lib_path:
        print("ERROR: librobotcontrol not found!")
        return
    
    lib = ctypes.CDLL(lib_path)
    
    # Set up function signatures
    lib.rc_motor_init.argtypes = []
    lib.rc_motor_init.restype = ctypes.c_int
    
    lib.rc_motor_set.argtypes = [ctypes.c_int, ctypes.c_float]
    lib.rc_motor_set.restype = ctypes.c_int
    
    lib.rc_motor_cleanup.argtypes = []
    lib.rc_motor_cleanup.restype = ctypes.c_int
    
    print("Initializing motors...")
    ret = lib.rc_motor_init()
    print(f"  rc_motor_init() returned: {ret} ({'OK' if ret == 0 else 'FAILED'})")
    
    if ret != 0:
        print("Motor init failed! Exiting.")
        return
    
    # Test each motor
    for motor_id in range(1, 5):
        print(f"\nTesting motor {motor_id}...")
        
        # Set to 40% speed
        speed = 0.4
        ret = lib.rc_motor_set(motor_id, ctypes.c_float(speed))
        print(f"  rc_motor_set({motor_id}, {speed}) returned: {ret} ({'OK' if ret == 0 else 'FAILED'})")
        
        time.sleep(1)
        
        # Stop
        ret = lib.rc_motor_set(motor_id, ctypes.c_float(0.0))
        print(f"  rc_motor_set({motor_id}, 0.0) returned: {ret} ({'OK' if ret == 0 else 'FAILED'})")
        
        time.sleep(0.5)
    
    print("\nCleaning up...")
    lib.rc_motor_cleanup()
    print("Done!")

if __name__ == "__main__":
    test_motors()
