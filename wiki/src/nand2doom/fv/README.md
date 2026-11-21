# nand2doom FV

The `fv` discipline of nand2doom: property-checker proofs of the blocks where formal pays ([formal verification](../../../bkm/fv.md)). Code: `src/nand2doom/fv/<block>/`, one SymbiYosys file per target, run with `tools/fv` ([formal runner](../../../tools/fv/README.md)).

| Block | FV plan | Target | Level |
|---|---|---|---|
| memory (`n2d_memory_arbiter`) | [fv-plan](memory/fv-plan.md) | `n2d_memory_arbiter` | L0 |
| io (`n2d_ps2_keyboard` event FIFO) | [fv-plan](io/fv-plan.md) | `n2d_ps2_keyboard` | L0 |

```sh
python3 tools/fv/fv.py run --project nand2doom
```
