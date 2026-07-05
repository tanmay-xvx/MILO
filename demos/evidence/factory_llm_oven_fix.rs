// Oven closed-loop controller — generated_by: llm
#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    // Constants
    const DEFAULT_TARGET: i32 = 1500; // Default target temperature in tenths of °C
    const ADC_CHANNEL: u32 = 0; // ADC channel for temperature reading
    const PWM_CHANNEL: u32 = 0; // PWM channel for heater control
    const ITERATIONS: u32 = 15; // Number of control iterations
    const DELAY_MS: u32 = 200; // Delay between iterations in milliseconds

    // Initial setup
    let target_temp = unsafe { get_param(2) as i32 };
    let target = if target_temp == 0 { DEFAULT_TARGET } else { target_temp };
    let mut accumulator: i32 = 3000;

    // Log controller engagement
    let msg = b"Controller engaged";
    unsafe { log_msg(msg.as_ptr() as u32, msg.len() as u32); }

    // Control loop
    for _ in 0..ITERATIONS {
        // Read current temperature
        let temperature = unsafe { adc_read(ADC_CHANNEL) as i32 };

        // Calculate error and update accumulator
        let error = target - temperature;
        accumulator += error / 4;
        if accumulator < 0 {
            accumulator = 0;
        } else if accumulator > 10000 {
            accumulator = 10000;
        }

        // Apply PWM duty cycle
        unsafe { pwm_set(PWM_CHANNEL, accumulator as u32); }

        // Log current temperature
        let mut temp_log = [b'T', b'=', b'0', b'0', b'0', b'0', b'\0'];
        let mut temp = temperature;
        for i in (2..6).rev() {
            temp_log[i] = b'0' + (temp % 10) as u8;
            temp /= 10;
        }
        unsafe { log_msg(temp_log.as_ptr() as u32, 6); }

        // Delay
        unsafe { delay_ms(DELAY_MS); }
    }
}
