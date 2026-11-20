# Merge fast, recover fast: cumulative regression levels

Pull requests into `main` gate only on L0, a fast check; slower regression runs on `main` after the merge: L1 post-merge, L2 hourly, L3 every 6 hours, L4 nightly, each level including all lower ones. We accept that `main` can be broken for up to a few hours in exchange for fast merges, because the scheduled levels catch failures soon and the fix goes in through an issue at once. The alternative, running long regressions before every merge, would make each pull request wait hours.
