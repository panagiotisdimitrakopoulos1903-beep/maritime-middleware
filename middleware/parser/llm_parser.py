"""
parser/llm_parser.py — LLM-based structured extraction of shipping telexes.

Uses Claude to extract cargo fields from free-text broker messages.
Returns a ParsedOrder with per-field confidence scores.
"""
import json
import re
import structlog
from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel, Field
from anthropic import Anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings
from core.ports import normalise_port

log = structlog.get_logger()

client = Anthropic(api_key=settings.anthropic_api_key)


# ── Output schema ─────────────────────────────────────────────────────────────

class FieldWithConfidence(BaseModel):
    value: Optional[str | float | int] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)


class ParsedOrder(BaseModel):
    """
    Structured representation of an inbound cargo order.
    Every field carries a confidence score 0–1.
    """
    # Core cargo fields
    cargo_type: FieldWithConfidence = FieldWithConfidence()
    quantity_mt: FieldWithConfidence = FieldWithConfidence()
    quantity_min_mt: FieldWithConfidence = FieldWithConfidence()
    quantity_max_mt: FieldWithConfidence = FieldWithConfidence()
    load_port: FieldWithConfidence = FieldWithConfidence()
    discharge_port: FieldWithConfidence = FieldWithConfidence()
    laycan_start: FieldWithConfidence = FieldWithConfidence()
    laycan_end: FieldWithConfidence = FieldWithConfidence()
    vessel_size_dwt: FieldWithConfidence = FieldWithConfidence()
    vessel_type: FieldWithConfidence = FieldWithConfidence()
    freight_rate: FieldWithConfidence = FieldWithConfidence()
    charterer: FieldWithConfidence = FieldWithConfidence()

    # Normalised ports (populated after alias lookup)
    load_port_canonical: Optional[str] = None
    discharge_port_canonical: Optional[str] = None

    # Overall quality
    parse_confidence: float = 0.0
    has_low_confidence_fields: bool = False
    low_confidence_field_names: list[str] = []
    raw_llm_response: Optional[str] = None
    error: Optional[str] = None


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert maritime shipping broker with 20+ years of experience reading 
cargo enquiry messages. You extract structured data from broker telexes and emails.

Common abbreviations you will encounter:
- ANT/AMS/RTM = Antwerp/Amsterdam/Rotterdam
- GRAIN = dry bulk grain cargo
- WS = Worldscale freight rate
- LC/LAYCAN = laycan (loading window dates)
- MT/T = metric tons
- ABT = about (approximate)
- DISCH = discharge port
- PLS = please
- HV/HVE = have
- SPORE = Singapore
- JPN/JAP = Japan
- KOR = Korea
- CHN = China
- USG = US Gulf
- NOVO = Novorossiysk
- NVS = Novorossiysk
- AFRAMAX = tanker 80,000-120,000 DWT
- SUEZMAX = tanker 120,000-200,000 DWT
- VLCC = tanker 200,000-320,000 DWT
- PANAMAX = dry bulk 60,000-80,000 DWT
- CAPESIZE = dry bulk 100,000+ DWT
- HANDYMAX = dry bulk 40,000-60,000 DWT

Date formats you may see: Aug 10-20, 10/20 Aug, 10th-20th August, aug 10/20, 10-20/08

Return ONLY valid JSON. No preamble, no explanation, no markdown backticks.
If a field cannot be determined, set its value to null and confidence to 0.0.
Confidence 1.0 = completely certain, 0.0 = not present or completely uncertain."""

EXTRACTION_PROMPT = """Extract all cargo order fields from this broker message.

Message:
{message}

Return this exact JSON structure:
{{
  "cargo_type": {{"value": "grain", "confidence": 0.98}},
  "quantity_mt": {{"value": 55000, "confidence": 0.95}},
  "quantity_min_mt": {{"value": 50000, "confidence": 0.90}},
  "quantity_max_mt": {{"value": 60000, "confidence": 0.90}},
  "load_port": {{"value": "Antwerp", "confidence": 0.97}},
  "discharge_port": {{"value": "Japan", "confidence": 0.92}},
  "laycan_start": {{"value": "2024-08-10", "confidence": 0.95}},
  "laycan_end": {{"value": "2024-08-20", "confidence": 0.95}},
  "vessel_size_dwt": {{"value": 55000, "confidence": 0.80}},
  "vessel_type": {{"value": "Panamax", "confidence": 0.85}},
  "freight_rate": {{"value": "WS 32", "confidence": 0.88}},
  "charterer": {{"value": null, "confidence": 0.0}}
}}

Today's date for relative date resolution: {today}"""


# ── Parser ────────────────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10)
)
def _call_llm(message: str) -> str:
    """Call Claude API with retry logic."""
    today = date.today().strftime("%Y-%m-%d")
    response = client.messages.create(
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": EXTRACTION_PROMPT.format(message=message, today=today)
        }]
    )
    return response.content[0].text


def _clean_llm_response(raw: str) -> str:
    """Strip any accidental markdown fences."""
    raw = raw.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _preprocess(raw_body: str) -> str:
    """
    Strip email headers, signatures, and quoted replies.
    Returns just the message body the broker wrote.
    """
    lines = raw_body.split("\n")
    cleaned = []
    for line in lines:
        # Stop at quoted reply markers
        if re.match(r"^(>|On .* wrote:|From:|-----Original)", line.strip()):
            break
        # Skip typical signature patterns
        if re.match(r"^(--|Best regards|Kind regards|Thanks|Sent from)", line.strip(), re.I):
            break
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _compute_confidence(parsed: dict) -> tuple[float, list[str]]:
    """
    Compute mean confidence and identify low-confidence fields.
    """
    core_fields = [
        "cargo_type", "quantity_mt", "load_port",
        "discharge_port", "laycan_start", "laycan_end"
    ]
    scores = []
    low_fields = []
    for field in core_fields:
        fdata = parsed.get(field, {})
        conf = fdata.get("confidence", 0.0) if isinstance(fdata, dict) else 0.0
        scores.append(conf)
        if conf < settings.parser_confidence_threshold:
            low_fields.append(field)
    mean = sum(scores) / len(scores) if scores else 0.0
    return round(mean, 3), low_fields


def parse_message(raw_body: str) -> ParsedOrder:
    """
    Main entry point. Takes a raw email body and returns a ParsedOrder.
    """
    log.info("parse_message.start", body_length=len(raw_body))

    order = ParsedOrder()

    try:
        clean_body = _preprocess(raw_body)
        raw_response = _call_llm(clean_body)
        order.raw_llm_response = raw_response

        cleaned = _clean_llm_response(raw_response)
        data = json.loads(cleaned)

        # Map LLM response into ParsedOrder fields
        for field_name in [
            "cargo_type", "quantity_mt", "quantity_min_mt", "quantity_max_mt",
            "load_port", "discharge_port", "laycan_start", "laycan_end",
            "vessel_size_dwt", "vessel_type", "freight_rate", "charterer"
        ]:
            if field_name in data:
                fdata = data[field_name]
                if isinstance(fdata, dict):
                    setattr(order, field_name, FieldWithConfidence(
                        value=fdata.get("value"),
                        confidence=float(fdata.get("confidence", 0.0))
                    ))

        # Normalise port names
        if order.load_port.value:
            order.load_port_canonical = normalise_port(str(order.load_port.value))
        if order.discharge_port.value:
            order.discharge_port_canonical = normalise_port(str(order.discharge_port.value))

        # Compute overall confidence
        order.parse_confidence, order.low_confidence_field_names = _compute_confidence(data)
        order.has_low_confidence_fields = len(order.low_confidence_field_names) > 0

        log.info(
            "parse_message.success",
            confidence=order.parse_confidence,
            low_fields=order.low_confidence_field_names,
            cargo=order.cargo_type.value,
            load=order.load_port_canonical,
            disch=order.discharge_port_canonical,
        )

    except json.JSONDecodeError as e:
        log.error("parse_message.json_error", error=str(e))
        order.error = f"JSON decode error: {e}"
        order.parse_confidence = 0.0

    except Exception as e:
        log.error("parse_message.error", error=str(e))
        order.error = str(e)
        order.parse_confidence = 0.0

    return order
