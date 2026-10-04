from __future__ import annotations
from typing import Dict, Any
from src.character_sim import SpawnQueue, Stats


def enqueue_character(queue: SpawnQueue, image_path: str, data: Dict[str, Any]) -> int:
    """
    Insert a character into the simulation spawn queue.
    """

    return queue.enqueue(
        image=image_path,
        emotion=data["emotion"],
        size=data["size"],
        passivity=data["passivity"],
        laziness=data["laziness"],
        base=data["base"],
        traits=data["traits"],
    )
