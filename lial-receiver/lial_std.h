#ifndef LIAL_STD_H
#define LIAL_STD_H

#include <stdint.h>

/**
 * LIAL-Std: The Atomic Alphabet
 * These are the ONLY functions manually linked to the Wasm environment.
 * The LLM combines these letters to create complex hardware logic.
 */

extern "C" {
    // --- GPIO Primitives ---
    void lial_gpio_set(uint32_t pin, uint32_t state);
    uint32_t lial_gpio_get(uint32_t pin);

    // --- Time Primitives ---
    void lial_delay_ms(uint32_t ms);
    uint64_t lial_get_uptime_us();

    // --- Communication Primitives (Generic Bus Access) ---
    // This allows the LLM to talk to ANY I2C device without a pre-coded driver.
    int lial_i2c_transfer(uint8_t addr, uint8_t* tx_buf, uint32_t tx_len, uint8_t* rx_buf, uint32_t rx_len);
    
    // --- Logging ---
    // For the LLM-generated logic to send status back to the Host.
    void lial_log(const char* message);
}

#endif // LIAL_STD_H