// Repair controller for drone-3 — generated_by: llm
#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    // Log the controller engagement message
    let msg = b"Altitude controller engaged";
    unsafe { log_msg(msg.as_ptr() as u32, msg.len() as u32); }

    // Initialize variables
    let mut duty_accumulator: i32 = 5000;
    let mut target_cm: u32 = unsafe { get_param(0) };
    if target_cm == 0 {
        target_cm = 200;
    }

    // Control loop
    for _ in 0..1200 {
        // Check for exit condition
        if unsafe { get_param(6) } == 9999 {
            break;
        }

        // Read altitude from IMU
        let mut tx_buf = [0u8; 1];
        let mut rx_buf = [0u8; 3];
        unsafe {
            i2c_transfer(0x68, tx_buf.as_ptr() as u32, 1, rx_buf.as_mut_ptr() as u32, 3);
        }
        let altitude_cm = ((rx_buf[0] as u32) << 8) | (rx_buf[1] as u32);

        // Calculate error
        let err = target_cm as i32 - altitude_cm as i32;

        // Update integral term
        duty_accumulator += err / 24;
        if duty_accumulator < 2500 {
            duty_accumulator = 2500;
        } else if duty_accumulator > 8500 {
            duty_accumulator = 8500;
        }

        // Calculate command
        let mut command = duty_accumulator + err * 3;
        if command < 2500 {
            command = 2500;
        } else if command > 9500 {
            command = 9500;
        }

        // Apply command to all motors
        for channel in 0..4 {
            unsafe { pwm_set(channel, command as u32); }
        }

        // Delay for 50 ms
        unsafe { delay_ms(50); }
    }

    // Set all motor duties to 0
    for channel in 0..4 {
        unsafe { pwm_set(channel, 0); }
    }

    // Log completion message
    let msg = b"Altitude control completed";
    unsafe { log_msg(msg.as_ptr() as u32, msg.len() as u32); }
}
