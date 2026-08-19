"""Local Simplified/Traditional Chinese conversion for subtitle text."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Final

from maw.project_preview import JsonDict


class TextConversion(StrEnum):
    OFF = "off"
    TO_SIMPLIFIED = "to_simplified"
    TO_TRADITIONAL = "to_traditional"


TEXT_CONVERSION_MODES: Final[frozenset[str]] = frozenset(item.value for item in TextConversion)
_OPENCC_CONFIGS: Final[dict[TextConversion, str]] = {
    TextConversion.TO_SIMPLIFIED: "t2s",
    TextConversion.TO_TRADITIONAL: "s2t",
}


class TextConversionUnavailable(RuntimeError):
    """Raised when the optional conversion engine cannot be loaded."""


def normalize_text_conversion_mode(value: object) -> TextConversion:
    """Normalize untrusted plan/UI data without enabling conversion by accident."""

    if isinstance(value, TextConversion):
        return value
    try:
        return TextConversion(str(value or "").strip().lower())
    except ValueError:
        return TextConversion.OFF


def convert_text(text: str, mode: object) -> str:
    """Convert one text value with the requested OpenCC direction."""

    conversion = normalize_text_conversion_mode(mode)
    if conversion is TextConversion.OFF or not text:
        return text
    config = _OPENCC_CONFIGS[conversion]
    try:
        return _converter(config).convert(text)
    except ImportError as error:
        raise TextConversionUnavailable(
            "简繁转换需要 OpenCC 支持，请重新安装 MAW 或运行 `uv sync`。"
        ) from error


def apply_text_conversion(segments: list[JsonDict], mode: object) -> bool:
    """Convert segment text and preserve item timing when the mapping is safe."""

    conversion = normalize_text_conversion_mode(mode)
    if conversion is TextConversion.OFF:
        return False
    changed = False
    for segment in segments:
        original = segment.get("text")
        if not isinstance(original, str):
            continue
        converted = convert_text(original, conversion)
        if converted == original:
            continue
        segment["text"] = converted
        if not _convert_items(segment, conversion, converted):
            segment.pop("items", None)
        changed = True
    return changed


def convert_segments_to_traditional(segments: list[dict], mode: str) -> None:
    """Convert transcription output and preserve word timing by safe alignment.

    Taiwan terminology can change text length. In that case, fall back to glyph-only
    conversion so each word item keeps its original character span and timestamp.
    """

    config = {"taiwan": "s2twp", "standard": "s2t"}.get(mode)
    if config is None:
        return
    converter = _converter(config)
    glyph_converter = _converter("s2t")

    def convert_glyphs(text: str) -> str:
        return "".join(glyph_converter.convert(char) for char in text)

    for index, segment in enumerate(segments, 1):
        source = str(segment.get("text") or "")
        items = segment.get("items") or []
        item_source = "".join(str(item.get("text") or "") for item in items)
        converted = converter.convert(source)
        aligned = len(converted) == len(source) and (not items or item_source == source)
        if not aligned:
            reasons = []
            if len(converted) != len(source):
                reasons.append(f"词汇转换字数由 {len(source)} 变为 {len(converted)}")
            if items and item_source != source:
                reasons.append("items 文字无法拼回 segment")
            print(f"[警告] 第 {index} 条字幕无法逐字对齐（{'；'.join(reasons)}）；已回退为单字简繁转换。")
            converted = convert_glyphs(source)

        segment["text"] = converted
        if not items:
            continue
        offset = 0
        for item in items:
            length = len(str(item.get("text") or ""))
            item["text"] = converted[offset : offset + length]
            offset += length


def _convert_items(segment: JsonDict, mode: TextConversion, expected_text: str) -> bool:
    raw_items = segment.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return False
    converted_items: list[JsonDict] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict) or not isinstance(raw_item.get("text"), str):
            return False
        item = dict(raw_item)
        item["text"] = convert_text(str(raw_item["text"]), mode)
        converted_items.append(item)
    if "".join(str(item["text"]) for item in converted_items) != expected_text:
        return False
    segment["items"] = converted_items
    return True


@lru_cache(maxsize=2)
def _converter(config: str):
    try:
        from opencc import OpenCC
    except ImportError:
        raise
    return OpenCC(config)
