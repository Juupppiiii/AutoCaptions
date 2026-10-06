from dataclasses import dataclass


@dataclass
class Word:
    text: str
    start: float
    end: float
