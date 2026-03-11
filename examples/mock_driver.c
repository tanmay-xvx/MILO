// These are the "Letters" we defined in our Rust Receiver
extern void lial_gpio_set(int pin, int state);
extern void lial_delay_ms(int ms);

// This is the "Sentence" an LLM would generate
void run_logic() {
    for(int i = 0; i < 3; i++) {
        lial_gpio_set(5, 1); // High
        lial_delay_ms(500);
        lial_gpio_set(5, 0); // Low
        lial_delay_ms(500);
    }
}