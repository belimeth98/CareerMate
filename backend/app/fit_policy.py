"""Reference scores support a preparation conversation, never a hiring verdict."""

LOW_REFERENCE_RATIO = 0.70


def needs_experience(fit):
    current = fit.get("current", {})
    weight = current.get("assessed_weight", 0)
    return bool(current.get("gaps")) or weight == 0 or current.get("score", 0) / weight < LOW_REFERENCE_RATIO
