"""Live LLM code generation for the demo scenarios.

Uses the same system prompt and extraction path as the interactive CLI, so
the corrective drivers in the demos are generated exactly the way a user's
task would be. Falls back to a scripted driver if the API is unavailable so
the scenarios stay reproducible; the evidence log records which path ran.
"""

import os
import re
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HOST_DIR = os.path.join(_REPO_ROOT, "host")
if _HOST_DIR not in sys.path:
    sys.path.insert(0, _HOST_DIR)

from core.compiler import compile_rust_to_wasm  # noqa: E402

_PROMPT_PATH = os.path.join(_REPO_ROOT, "prompts", "system_prompt.txt")
with open(_PROMPT_PATH) as f:
    SYSTEM_PROMPT = f.read()


def _extract_rust(text: str) -> str:
    for pat in [r"```rust\s*\n(.*?)```", r"```\s*\n(.*?)```"]:
        m = re.search(pat, text, re.DOTALL)
        if m:
            return m.group(1).strip()
    return text.strip()


def llm_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def generate_driver(
    task: str,
    device_manifest: dict,
    fallback_body: str,
    max_retries: int = 2,
) -> tuple[bytes, str, str]:
    """Ask GPT-4o to write a driver for `task`; compile; retry on errors.

    Returns (wasm_bytes, source_body, mode) where mode is "llm" or
    "fallback". Never raises on LLM failure — the scenario must complete.
    """
    import json

    if llm_available():
        try:
            from openai import OpenAI

            client = OpenAI()
            system = SYSTEM_PROMPT.format(
                device_info=json.dumps(device_manifest, indent=2)
            )
            history = [
                {"role": "system", "content": system},
                {"role": "user", "content": task},
            ]
            resp = client.chat.completions.create(
                model="gpt-4o", messages=history, temperature=0.2
            )
            body = _extract_rust(resp.choices[0].message.content)
            history.append({"role": "assistant", "content": body})

            for attempt in range(1 + max_retries):
                try:
                    wasm = compile_rust_to_wasm(body)
                    return wasm, body, "llm"
                except RuntimeError as e:
                    if attempt == max_retries:
                        break
                    history.append(
                        {
                            "role": "user",
                            "content": (
                                "The code failed to compile. Here is the error:\n\n"
                                f"```\n{e}\n```\n\n"
                                "Please output the corrected complete function body."
                            ),
                        }
                    )
                    resp = client.chat.completions.create(
                        model="gpt-4o", messages=history, temperature=0.2
                    )
                    body = _extract_rust(resp.choices[0].message.content)
                    history.append({"role": "assistant", "content": body})
        except Exception as e:  # API/network failure — fall through
            print(f"  [llm] falling back to scripted driver: {e}")

    wasm = compile_rust_to_wasm(fallback_body)
    return wasm, fallback_body, "fallback"


def llm_diagnose(situation: str, context: dict) -> tuple[str, str]:
    """Ask GPT-4o to diagnose an anomaly and pick an action.

    Returns (diagnosis_text, mode). Purely advisory — the control station
    validates the chosen action against its own allowlist.
    """
    import json

    if llm_available():
        try:
            from openai import OpenAI

            client = OpenAI()
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are the supervisor of an autonomous factory floor "
                            "controlled through the MILO framework. Given telemetry, "
                            "diagnose the anomaly in 2-3 sentences and end with a line "
                            "'ACTION: <one of: halt_and_clear, retune_controller, "
                            "no_action>'."
                        ),
                    },
                    {
                        "role": "user",
                        "content": situation + "\n\nTelemetry:\n" + json.dumps(context, indent=2),
                    },
                ],
                temperature=0.2,
            )
            return resp.choices[0].message.content.strip(), "llm"
        except Exception as e:
            print(f"  [llm] diagnosis fallback: {e}")

    return (
        "Scripted policy: anomaly matches a known signature; applying the "
        "predefined corrective action.\nACTION: halt_and_clear",
        "fallback",
    )
