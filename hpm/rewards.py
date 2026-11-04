"""Une destination positive par couple type/difficulté, sans multiplicateur de scores."""

from dataclasses import dataclass

POLICY = "typed-difficulty-v1"
KINDS = {"habit": "Habitude", "daily": "Quotidienne", "todo": "À faire"}
DIFFICULTIES = {
    "trivial": (0.1, "Triviale"),
    "easy": (1.0, "Facile"),
    "medium": (1.5, "Moyenne"),
    "hard": (2.0, "Difficile"),
}


@dataclass(frozen=True)
class Reward:
    key: str
    source_type: str
    priority: float
    type_label: str
    difficulty_label: str

    def alias(self, cfg):
        return cfg.technical_alias + "-" + self.key

    def title(self, cfg):
        return f"{cfg.technical_title} · {self.type_label} · {self.difficulty_label}"


REWARDS = {
    f"{kind}-{level}": Reward(f"{kind}-{level}", kind, priority, label, difficulty)
    for kind, label in KINDS.items()
    for level, (priority, difficulty) in DIFFICULTIES.items()
}


def reward_for(kind, priority):
    return next((r for r in REWARDS.values() if r.source_type == kind and r.priority == priority), None)
