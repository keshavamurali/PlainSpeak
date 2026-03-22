"""Robust JSON parsing from LLM output."""

import json
import re

try:
    from json_repair import repair_json
    HAS_REPAIR = True
except ImportError:
    HAS_REPAIR = False


class JsonParsingError(Exception):
    """Raised when JSON cannot be parsed from LLM output."""


def extract_json_block_fenced(text: str) -> str | None:
    """Extract content from ```json fenced code block."""
    match = re.search(r"(?i)```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    return match.group(1) if match else None


def extract_json_block_balanced(text: str) -> str | None:
    """Find largest balanced JSON block from first '{' to last '}'."""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return None


def validate_required_keys(obj: dict, required_keys: list[str]) -> None:
    """Ensure all required keys exist in parsed dictionary."""
    for key in required_keys:
        if key not in obj:
            raise JsonParsingError(f"Missing required key: {key}")


def parse_llm_json(
    text: str, required_keys: list[str] | None = None, debug: bool = False
) -> dict:
    """
    Robustly parse a JSON object from LLM output.
    Tries: fenced JSON block, balanced braces, then repair.
    """
    if not text or not isinstance(text, str):
        raise JsonParsingError("Input must be a non-empty string")

    extractors = [
        ("fenced", extract_json_block_fenced),
        ("balanced", extract_json_block_balanced),
    ]

    for name, extractor in extractors:
        raw_json = extractor(text)
        if raw_json:
            try:
                if debug:
                    print(f"[DEBUG] Attempting {name} extraction...")
                parsed = json.loads(raw_json)
                if required_keys:
                    validate_required_keys(parsed, required_keys)
                return parsed
            except json.JSONDecodeError:
                if debug:
                    print(f"[DEBUG] JSON decode failed for {name}.")
                continue
            except JsonParsingError:
                raise

    # Final attempt: repair
    raw_json = extract_json_block_balanced(text)
    if raw_json and HAS_REPAIR:
        try:
            if debug:
                print("[DEBUG] Attempting auto-repair...")
            repaired = repair_json(raw_json)
            if isinstance(repaired, dict):
                if required_keys:
                    validate_required_keys(repaired, required_keys)
                return repaired
            if isinstance(repaired, list) and len(repaired) > 0:
                return repaired[0] if isinstance(repaired[0], dict) else {"output": repaired}
        except Exception:
            if debug:
                print("[DEBUG] Repair attempt failed.")

    raise JsonParsingError("All attempts to parse JSON from LLM output failed.")
