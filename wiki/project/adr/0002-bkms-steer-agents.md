# BKMs steer the agents; skills only route

All method lives in BKMs under `wiki/bkm/`. Skills are thin entry points that route an agent to the right BKM; they do not chain into other skills and rarely change. Humans change how agents work by tuning BKMs. We rejected the upstream style of many small skills invoking each other: it hides the method inside agent plumbing, and every local tweak would fork a skill.
