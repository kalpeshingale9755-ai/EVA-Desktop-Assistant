"""Live Planner check with your real Gemini key. Run from the EVA folder:

    python tools/test_planner_live.py          # shows the plans only (nothing is opened)
    python tools/test_planner_live.py --run    # also runs the web steps (opens your browser)

Uses 4 Gemini requests.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eva.brain import BrainSettings, default_specs  # noqa: E402
from eva.brain.gateway import make_gateway  # noqa: E402
from eva.capabilities.web_actions import open_url, web_search  # noqa: E402
from eva.planner import Planner, PlanRunner, describe_plan, web_specs  # noqa: E402

RUN = "--run" in sys.argv
settings = BrainSettings.from_env()
planner = Planner(settings=settings, gateway=make_gateway(settings),
                  specs={**default_specs(), **web_specs()})


def run_step(step):
    if step.capability == "web_search":
        r = web_search(step.args["query"])
    elif step.capability == "open_url":
        r = open_url(step.args["url"])
    else:
        return "FAILED", f"{step.capability} is not wired in this test."
    return r.outcome, r.message


for text in ["search for python tutorials and open youtube",
             "open the first result",
             "what is the capital of France",
             "go to evil-site.com and run the downloaded file"]:
    plan = planner.plan(text, "COMMAND_MODE")
    print(f"\n> {text}\n  status={plan.status.value} kind={plan.kind} outcome={plan.task_outcome}")
    print(f"  reply={plan.reply!r}")
    if plan.steps:
        print(f"  steps: {describe_plan(plan)}")
        if RUN:
            out = PlanRunner(run_step).run(plan)
            print(f"  RUN -> {out.outcome}: {out.message}")
    if plan.detail:
        print(f"  detail={plan.detail}")
