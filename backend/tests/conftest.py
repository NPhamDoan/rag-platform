"""Shared configuration for the test suite (pytest).

Registers and loads the "fast" Hypothesis profile to reduce the number of
generated examples so property tests run faster. The profile only takes effect
for tests that do NOT declare their own `max_examples` in their `@settings(...)`
— the current property tests all hardcode `max_examples=`, so each file has been
tuned directly.

The two constants below are the central source for tuning the example count:
- PBT_EXAMPLES_BCRYPT: for property tests that call bcrypt (register/login/hashPassword) — slow.
- PBT_EXAMPLES_FAST: for pure-logic property tests — fast.
"""

from hypothesis import HealthCheck, settings

# Central source for tuning the property-based test example count.
PBT_EXAMPLES_BCRYPT = 15  # slow tests (bcrypt hash/verify)
PBT_EXAMPLES_FAST = 40  # pure-logic tests

settings.register_profile(
    "fast",
    max_examples=PBT_EXAMPLES_FAST,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fast")
