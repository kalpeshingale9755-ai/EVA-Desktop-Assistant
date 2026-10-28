from dataclasses import dataclass


@dataclass
class PlanSettings:
    max_steps: int = 5               # longest plan EVA will accept
    step_delay_seconds: float = 0.8  # pause between steps (lets the browser open)
