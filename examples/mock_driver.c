// These are the "Letters" we defined in our Rust Receiver
extern void gpio_set(int pin, int state);
extern void delay_ms(int ms);

// This is the "Sentence" an LLM would generate
void run_logic() {
    for(int i = 0; i < 3; i++) {
        gpio_set(5, 1); // High
        delay_ms(500);
        gpio_set(5, 0); // Low
        delay_ms(500);
    }
}