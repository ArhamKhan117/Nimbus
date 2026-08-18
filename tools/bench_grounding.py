"""Grounding-accuracy harness (T1-8).

Answers the question every later model decision depends on: **when Nimbus points, how
far off is it?** Scores each provider/model/strategy against hand-labelled ground truth
so `T1-3` (Agentic Vision), `T1-9` tuning, and `T4-6` (4K capture resolutions) become
evidence-based instead of guesses.

Reuses ``tools/bench.py``'s Mann-Whitney U and bootstrap-CI reporting rather than
reimplementing statistics.

Workflow
--------
1. Label fixtures (once per screen you care about)::

       py -3.13 tools/label_fixtures.py --capture

2. Score a provider against every fixture::

       py -3.13 tools/bench_grounding.py --provider gemini-native

3. Compare two runs for significance::

       py -3.13 tools/bench_grounding.py --compare gemini-native openai

Metrics
-------
* **hit-rate** — did the point land inside the ground-truth box? The metric that
  actually matters: a click either works or it doesn't.
* **pixel error** — Euclidean distance from the box centre, in Space C pixels. Useful
  for ranking near-misses.
* **latency** — wall-clock per call.

Script-only. Never imported by a runtime module, and ``nimbus.spec`` excludes this tree
(it also pulls in scipy, which is deliberately not bundled).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FIXTURE_DIR = Path(__file__).parent / "grounding_fixtures"
RESULTS_DIR = Path.home() / ".nimbus" / "bench"


# --- Scoring (pure, unit-tested) --------------------------------------------

def box_centre(box) -> tuple[float, float]:
    """Centre of an ``[x0, y0, x1, y1]`` box."""
    x0, y0, x1, y1 = box
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def is_hit(point, box) -> bool:
    """Whether ``point`` falls inside ``box``.

    Boundaries count as hits: a click exactly on a button's edge activates it, so
    scoring it a miss would misrepresent real behaviour.
    """
    if point is None:
        return False
    x, y = point
    x0, y0, x1, y1 = box
    return x0 <= x <= x1 and y0 <= y <= y1


def pixel_error(point, box) -> float:
    """Euclidean distance from ``point`` to the box centre.

    ``inf`` when no point was produced, so a provider that declines to point is
    ranked worse than one that points badly — declining is still a failure to help.
    """
    if point is None:
        return math.inf
    cx, cy = box_centre(box)
    return math.hypot(point[0] - cx, point[1] - cy)


@dataclass
class TargetResult:
    fixture: str
    query: str
    box: list
    point: tuple | None
    hit: bool
    error_px: float
    latency_s: float
    label: str | None = None
    error: str | None = None


@dataclass
class RunSummary:
    provider: str
    model: str
    strategy: str
    results: list[TargetResult] = field(default_factory=list)

    @property
    def attempted(self) -> int:
        return len(self.results)

    @property
    def errored(self) -> int:
        return sum(1 for r in self.results if r.error)

    @property
    def hit_rate(self) -> float:
        scored = [r for r in self.results if not r.error]
        if not scored:
            return 0.0
        return sum(r.hit for r in scored) / len(scored)

    def finite_errors(self) -> list[float]:
        return [r.error_px for r in self.results
                if not r.error and math.isfinite(r.error_px)]

    def latencies(self) -> list[float]:
        return [r.latency_s for r in self.results if not r.error]

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "strategy": self.strategy,
            "hit_rate": self.hit_rate,
            "attempted": self.attempted,
            "errored": self.errored,
            "pixel_errors": self.finite_errors(),
            "latencies": self.latencies(),
            "results": [
                {
                    "fixture": r.fixture, "query": r.query, "box": r.box,
                    "point": list(r.point) if r.point else None,
                    "hit": r.hit,
                    "error_px": None if math.isinf(r.error_px) else r.error_px,
                    "latency_s": r.latency_s, "label": r.label, "error": r.error,
                }
                for r in self.results
            ],
        }


# --- Fixture loading ---------------------------------------------------------

def load_fixtures(fixture_dir: Path = FIXTURE_DIR) -> list[dict]:
    """Load and validate every sidecar. Malformed fixtures are skipped, not fatal.

    One bad file must not waste a whole benchmark run, so problems are reported and
    the run continues.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from label_fixtures import validate_sidecar

    if not fixture_dir.is_dir():
        return []
    loaded = []
    for path in sorted(fixture_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  skipping {path.name}: unreadable ({exc})")
            continue
        problems = validate_sidecar(data)
        if problems:
            print(f"  skipping {path.name}: {'; '.join(problems)}")
            continue
        image_path = fixture_dir / data["image"]
        if not image_path.is_file():
            print(f"  skipping {path.name}: image {data['image']} missing")
            continue
        data["_path"] = path
        data["_image_path"] = image_path
        loaded.append(data)
    return loaded


# --- Provider construction ---------------------------------------------------

def build_client(provider: str):
    """Build a live client for ``provider``. Returns ``(client, model_name)``."""
    import config
    from ai import create_ai_client

    if provider == "gemini-native":
        model = config.resolve_setting(
            "GEMINI_NATIVE_MODEL", config.DEFAULT_GEMINI_NATIVE_MODEL)
        key = config.GEMINI_API_KEY or ""
        if not key:
            raise SystemExit("GEMINI_API_KEY is not set (check .env)")
        return create_ai_client(model_id=f"gemini/{model}", api_key=key), model
    if provider == "openai":
        model = config.OPENAI_MODEL_VISION
        key = config.OPENAI_API_KEY or ""
        if not key:
            raise SystemExit("OPENAI_API_KEY is not set (check .env)")
        return create_ai_client(model_id=f"openai/{model}", api_key=key), model
    if provider == "anthropic":
        model = config.DEFAULT_ANTHROPIC_MODEL
        key = config.ANTHROPIC_API_KEY or ""
        if not key:
            raise SystemExit("ANTHROPIC_API_KEY is not set (check .env)")
        return create_ai_client(
            model_id=f"anthropic/{model}", api_key=key), model
    if provider == "ollama":
        import config as cfg
        return create_ai_client(
            model_id=f"ollama/{cfg.OLLAMA_MODEL_VISION}", api_key="",
            ollama_host=cfg.OLLAMA_HOST), cfg.OLLAMA_MODEL_VISION
    raise SystemExit(f"unknown provider: {provider!r}")


def as_locate_query(query: str) -> str:
    """Phrase a fixture query as an explicit locate request.

    Load-bearing, not cosmetic. A benchmark target is by definition something to point
    at, but a bare noun phrase like "the save icon" contains no directional word, so
    ``ai.classify_query`` marks it *conceptual* and the native client skips the geometry
    call entirely — scoring a miss on every fixture and measuring the classifier instead
    of the grounding.

    Caught by the harness self-test: a target covering half the screen still scored 0%
    because no point was ever requested.

    Already-directional queries are passed through so a fixture author who writes
    "where is the save icon" is not mangled into "where is where is...".
    """
    lowered = query.strip().lower()
    directional = ("where", "point", "click", "find", "show", "locate", "which")
    if lowered.startswith(directional):
        return query.strip()
    return f"where is {query.strip()}? point at it."


def ask_for_point(client, image, query: str, width: int, height: int):
    """Ask one grounding question. Returns ``(point | None, label | None)``.

    Deliberately goes through the same public ``ask_stream`` contract ``app.py`` uses,
    so the benchmark measures the real code path rather than a special-cased one.
    """
    label = f"primary focus (image dimensions: {width}x{height} pixels)"
    with client.ask_stream(
        images=[(image, label)], transcript=as_locate_query(query), history=[],
    ) as s:
        for _ in s.text_deltas():
            pass
        result = s.final_result()
    return result.coordinate, result.element_label


# --- Run ---------------------------------------------------------------------

def run(provider: str, strategy: str, limit: int | None) -> RunSummary:
    from PIL import Image

    fixtures = load_fixtures()
    if not fixtures:
        raise SystemExit(
            f"no valid fixtures in {FIXTURE_DIR}\n"
            f"Create some first:  py -3.13 tools/label_fixtures.py --capture"
        )

    client, model = build_client(provider)
    summary = RunSummary(provider=provider, model=model, strategy=strategy)

    total = sum(len(f["targets"]) for f in fixtures)
    if limit:
        total = min(total, limit)
    print(f"\nScoring {provider} ({model}) over {total} target(s) "
          f"across {len(fixtures)} fixture(s)\n")

    done = 0
    for fixture in fixtures:
        image = Image.open(fixture["_image_path"]).convert("RGB")
        w = fixture["capture"]["width"]
        h = fixture["capture"]["height"]
        for target in fixture["targets"]:
            if limit and done >= limit:
                break
            query, box = target["query"], target["box"]
            t0 = time.time()
            point, label, err = None, None, None
            try:
                point, label = ask_for_point(client, image, query, w, h)
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
            latency = time.time() - t0

            res = TargetResult(
                fixture=fixture["image"], query=query, box=box, point=point,
                hit=is_hit(point, box), error_px=pixel_error(point, box),
                latency_s=latency, label=label, error=err,
            )
            summary.results.append(res)
            done += 1

            if err:
                mark = "ERR "
                detail = err[:60]
            else:
                mark = "HIT " if res.hit else "miss"
                detail = (f"{res.error_px:6.1f}px  {latency:4.1f}s  "
                          f"{(label or '')[:24]}")
            print(f"  [{mark}] {query[:38]:<40} {detail}")

    return summary


def report(summary: RunSummary) -> None:
    import numpy as np
    sys.path.insert(0, str(Path(__file__).parent))
    from bench import bootstrap_median_ci

    errors = summary.finite_errors()
    latencies = summary.latencies()

    print("\n" + "=" * 72)
    print(f"{summary.provider}  /  {summary.model}  /  strategy={summary.strategy}")
    print("=" * 72)
    print(f"  targets attempted : {summary.attempted}")
    print(f"  api errors        : {summary.errored}")
    print(f"  HIT RATE          : {summary.hit_rate * 100:.1f}%")
    if errors:
        print(f"  median px error   : {float(np.median(errors)):.1f}")
        if len(errors) >= 3:
            lo, hi = bootstrap_median_ci(errors)
            print(f"  95% CI on median  : [{lo:.1f}, {hi:.1f}]")
    else:
        print("  median px error   : n/a (no successful points)")
    if latencies:
        print(f"  median latency    : {float(np.median(latencies)):.2f}s")

    no_point = sum(1 for r in summary.results
                   if not r.error and r.point is None)
    if no_point:
        print(f"  declined to point : {no_point} "
              f"(counted as misses - failing to help is a failure)")


def save(summary: RunSummary) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"grounding_{summary.provider}.json"
    path.write_text(json.dumps(summary.to_dict(), indent=2), encoding="utf-8")
    return path


def compare(a: str, b: str) -> None:
    """Compare two saved runs for statistical significance."""
    import numpy as np
    sys.path.insert(0, str(Path(__file__).parent))
    from bench import bootstrap_median_ci, mann_whitney_less

    pa = RESULTS_DIR / f"grounding_{a}.json"
    pb = RESULTS_DIR / f"grounding_{b}.json"
    for p in (pa, pb):
        if not p.is_file():
            raise SystemExit(f"missing {p} - run the harness for that provider first")
    da, db = (json.loads(p.read_text(encoding="utf-8")) for p in (pa, pb))

    print("\n" + "=" * 72)
    print(f"{a}  vs  {b}")
    print("=" * 72)
    print(f"{'metric':<22}{a:>18}{b:>18}")
    print("-" * 58)
    print(f"{'hit rate':<22}{da['hit_rate'] * 100:>17.1f}%{db['hit_rate'] * 100:>17.1f}%")

    ea, eb = da["pixel_errors"], db["pixel_errors"]
    if ea and eb:
        print(f"{'median px error':<22}{np.median(ea):>18.1f}{np.median(eb):>18.1f}")
        _, p = mann_whitney_less(ea, eb)
        print(f"\n  Mann-Whitney one-sided p (H1: {b} more accurate than {a}): {p:.4f}")
        print(f"  {'SIGNIFICANT at 0.05' if p < 0.05 else 'not significant'}")
        lo, hi = bootstrap_median_ci(eb)
        print(f"  95% CI on {b} median error: [{lo:.1f}, {hi:.1f}]")

    la, lb = da["latencies"], db["latencies"]
    if la and lb:
        print(f"\n{'median latency':<22}{np.median(la):>17.2f}s{np.median(lb):>17.2f}s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--provider",
                        choices=["gemini-native", "openai", "anthropic", "ollama"],
                        help="provider to score")
    parser.add_argument("--strategy", default="default",
                        help="label for this configuration, recorded in the results")
    parser.add_argument("--limit", type=int,
                        help="stop after N targets (quick smoke run)")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"),
                        help="compare two previously saved runs")
    parser.add_argument("--list", action="store_true",
                        help="list available fixtures and exit")
    args = parser.parse_args()

    if args.list:
        fixtures = load_fixtures()
        if not fixtures:
            print(f"no valid fixtures in {FIXTURE_DIR}")
            return 1
        for f in fixtures:
            print(f"  {f['image']:<40} "
                  f"{f['capture']['width']}x{f['capture']['height']}  "
                  f"{len(f['targets'])} target(s)")
            for t in f["targets"]:
                print(f"      - {t['query']}")
        return 0

    if args.compare:
        compare(*args.compare)
        return 0

    if not args.provider:
        parser.error("--provider is required (or use --list / --compare)")

    import dotenv
    dotenv.load_dotenv()

    summary = run(args.provider, args.strategy, args.limit)
    report(summary)
    path = save(summary)
    print(f"\nSaved: {path}")
    print("Record the headline numbers in IMPROVEMENTS.md section 11.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
