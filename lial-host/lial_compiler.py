import subprocess
import os

class LIALCompiler:
    def __init__(self, wasi_sdk_path):
        self.clang_path = os.path.join(wasi_sdk_path, 'bin', 'clang')
        
    def compile_string(self, c_code, output_name="logic.wasm"):
        """
        Takes a string of C code and compiles it to a WASM binary.
        """
        # 1. Save the string to a temporary file
        temp_c_file = "temp_driver.c"
        with open(temp_c_file, "w") as f:
            f.write(c_code)

        # 2. Construct the Clang command
        # -O3: Optimize for speed/size
        # --target=wasm32: Target WebAssembly
        # --no-standard-libraries: Keep it ultra-light for IoT
        # -Wl,--export-all: Ensure the LLM functions are visible to the Receiver
        cmd = [
            self.clang_path,
            "--target=wasm32",
            "-O3",
            "-nostdlib", 
            "-Wl,--no-entry",
            "-Wl,--export-all",
            "-Wl,--allow-undefined", # Allows LIAL-Std functions to be linked later
            "-o", output_name,
            temp_c_file
        ]

        try:
            print(f"🚀 Compiling LIAL Driver to {output_name}...")
            subprocess.run(cmd, check=True)
            print("✅ Compilation Successful.")
            return output_name
        except subprocess.CalledProcessError as e:
            print(f"❌ Compilation Failed: {e}")
            return None
        finally:
            if os.path.exists(temp_c_file):
                os.remove(temp_c_file)

# --- TEST RUN ---
if __name__ == "__main__":
    # Update this path to your local WASI SDK location
    WASI_PATH = "/path/to/wasi-sdk-20.0" 
    
    compiler = LIALCompiler(WASI_PATH)

    # Example: A 'Sentence' the LLM might write using your 'Alphabet'
    llm_generated_code = """
    // These are provided by the LIAL Receiver
    extern void lial_gpio_set(int pin, int state);
    extern void lial_delay_ms(int ms);

    void run_logic() {
        for(int i=0; i<5; i++) {
            lial_gpio_set(5, 1); // High
            lial_delay_ms(200);
            lial_gpio_set(5, 0); // Low
            lial_delay_ms(200);
        }
    }
    """

    compiler.compile_string(llm_generated_code, "blink_driver.wasm")
    