"""
SLOT E2 ML challenger -- an opt-in alternative to
datainsights.ml.slots.DeterministicBaseline.

What this challenger actually improves, stated plainly so it can be
checked rather than taken on trust: a plain median/MAD baseline includes
a client's own past spikes in their "normal". One large one-off payment
last quarter widens the tolerance band and hides the next genuine one.
This fits scikit-learn's IsolationForest on the client's own history,
drops the points it marks as outliers, and computes median/MAD on what
remains -- so past anomalies stop contaminating the definition of normal.

That is a narrow, explainable use of ML (outlier-robust baselining), not
a black box scoring clients. It trains per client on that client's own
unlabelled history -- it never sees ground-truth labels, never reads
protected_evaluator_only/, and never ranks clients against each other
(arbitration is Pega's job -- see datainsights/ml/__init__.py).

Whether it is actually better than the deterministic benchmark is an
empirical question answered by datainsights/ml/evaluate_baselines.py, not
by this docstring. The deterministic baseline stays the production default
until that comparison says otherwise on real data.
"""

from __future__ import annotations

import statistics
from datetime import date

from datainsights.ml.slots import InsufficientHistory


class IsolationForestBaseline:
    def __init__(self, history: dict[tuple[str, str], list[tuple[date, float]]],
                 min_observations: int = 8, contamination: float = 0.1,
                 random_state: int = 42, max_history: int = 30, n_estimators: int = 20):
        """min_observations is higher than DeterministicBaseline's on
        purpose: an isolation forest on a handful of points is noise, and
        pretending otherwise would be the fabricated-precision failure this
        project explicitly guards against. Below the floor this raises
        InsufficientHistory rather than degrading silently.

        max_history bounds each fit to the `max_history` observations
        immediately before `as_at` (trailing window), not the entire
        history -- keeps as-of correctness (still only ever observations
        <= as_at) while preventing the amount of data considered from
        growing unboundedly as a client's history accumulates.

        n_estimators is the actual measured cost driver, and the real fix
        for the ~4s-per-agreement figure docs/current_state.md recorded:
        a direct micro-benchmark showed sklearn's IsolationForest fit
        cost is dominated by tree COUNT (default 100), not sample size --
        90 vs 30 samples cost the same (~3.3s for 92 fits either way);
        cutting n_estimators 100 -> 20 cut that to ~0.7s, a ~4.7x
        speedup, for the exact same 92 fits. (max_history alone, without
        this, barely moved the number -- corrected here rather than left
        as an unverified claim.) 20 trees is fewer than sklearn's default
        but still enough for a robust median on a handful of points --
        this is univariate outlier detection on one measure, not a
        high-dimensional problem that needs a large, diverse forest."""
        self.history = history
        self.min_observations = min_observations
        self.contamination = contamination
        self.random_state = random_state
        self.max_history = max_history
        self.n_estimators = n_estimators

    def expected(self, prty_id: str, metric: str, as_at: date) -> tuple[float, float]:
        from sklearn.ensemble import IsolationForest  # optional dependency, imported lazily

        observations = [
            value for obs_date, value in self.history.get((prty_id, metric), [])
            if obs_date <= as_at  # as-at: never fit on the future
        ][-self.max_history:]  # trailing window only -- see max_history docstring above
        if len(observations) < self.min_observations:
            raise InsufficientHistory(
                f"{prty_id}/{metric}: {len(observations)} observation(s) at {as_at}, "
                f"IsolationForestBaseline needs {self.min_observations}."
            )

        forest = IsolationForest(contamination=self.contamination, random_state=self.random_state,
                                  n_estimators=self.n_estimators)
        matrix = [[v] for v in observations]
        labels = forest.fit_predict(matrix)  # 1 = inlier, -1 = outlier
        inliers = [v for v, label in zip(observations, labels) if label == 1]
        if len(inliers) < 3:
            inliers = observations  # forest flagged nearly everything -- fall back, don't invent

        median = statistics.median(inliers)
        mad = statistics.median([abs(v - median) for v in inliers])
        return float(median), float(mad)
