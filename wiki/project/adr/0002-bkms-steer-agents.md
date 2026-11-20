# BKMs steer the agents; skills stay generic

Project-specific method lives in BKMs under `wiki/`, not in skills. Skills stay generic and rarely change; they point at the BKMs, which humans tune over time to change how agents behave. The alternative, encoding our method directly into skills, would fork every skill from upstream and hide the human-facing controls inside agent plumbing.
