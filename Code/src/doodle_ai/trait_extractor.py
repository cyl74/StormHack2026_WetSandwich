from __future__ import annotations

from typing import Any, Dict

try:
    from src.char_sim import Stats, valid_traits
except ImportError:  # pragma: no cover
    from char_sim import Stats, valid_traits


def extract_traits(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert Gemini analysis JSON into the fields required by character_sim.py.
    """

    # 1. Emotion (user-set or inferred)
    mood = analysis.get("mood", "").lower()
    if mood not in ("happiness", "sadness", "fear", "anger", "anxiety"):
        mood = "happiness"

    # 2. Size (user-set or inferred from composition)
    size = 1.0
    if "big" in analysis.get("composition", "").lower():
        size = 1.4
    if "small" in analysis.get("composition", "").lower():
        size = 0.8

    # 3. Passivity / laziness (user-set or inferred)
    energy = analysis.get("energy", "").lower()
    passivity = 0.5
    laziness = 0.3

    if "calm" in energy or "slow" in energy:
        passivity = 0.7
        laziness = 0.6
    if "fast" in energy or "sharp" in energy:
        passivity = 0.2
        laziness = 0.1

    # 4. Base stats (AI-determined)
    base = Stats(
        speed=5,
        damage=5,
        health=60,
    )

    # 5. Trait list (AI-determined)
    visual_features = analysis.get("visual_features", [])
    traits = valid_traits(visual_features)

    return dict(
        emotion=mood,
        size=size,
        passivity=passivity,
        laziness=laziness,
        base=base,
        traits=traits,
    )
