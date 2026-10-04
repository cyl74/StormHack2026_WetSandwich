from __future__ import annotations
from typing import Dict, Any
from StormHack2026_WetSandwich.Code.src.char_sim import Character, Stats


def build_character(id: int, image_path: str, data: Dict[str, Any]) -> Character:
    """
    Build a Character object from extracted trait data.
    """

    return Character(
        id=id,
        image=image_path,
        emotion=data["emotion"],
        size=data["size"],
        passivity=data["passivity"],
        laziness=data["laziness"],
        base=data["base"],
        traits=data["traits"],
    )
