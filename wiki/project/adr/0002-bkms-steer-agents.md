# BKMs steer the agents; skills only route

All method lives in BKMs under `wiki/bkm/`. Skills are thin entry points: `bkm-router` lists every BKM and when to follow it so agents always have that list in context, `flow` gives the steps of a change, and `first-mate` and `grill` start a role or session; each points at BKMs instead of chaining into other skills. Humans change how agents work by tuning BKMs. We rejected the upstream style of many small skills invoking each other: it hides the method inside agent plumbing, and every local tweak would fork a skill.
