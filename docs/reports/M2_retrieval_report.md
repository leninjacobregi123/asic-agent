# Retrieval accuracy report

- Generated: 2026-09-25T08:26:15+00:00
- Embedder: **minilm**  (dim 384)
- Corpus: `designs/apb_gpio` (then `pulp-fork/`) — 63 chunks
- Index built: 2026-09-25T08:26:10+00:00
- Queries: 12, graded at k=5

## Summary

| Metric | Value |
|---|---|
| hit@1 | 50% |
| hit@3 | 58% |
| hit@5 | 67% |
| MRR | 0.562 |

## By query kind

| Kind | Queries | hit@1 | hit@3 | MRR |
|---|---|---|---|---|
| crossref | 3 | 67% | 67% | 0.750 |
| identifier | 4 | 100% | 100% | 1.000 |
| semantic | 5 | 0% | 20% | 0.100 |

## Per-query results

| ID | Kind | Query | Expected | Rank | Top result |
|---|---|---|---|---|---|
| Q01 | semantic | how do I configure a pin to drive a value out instead of r | apb_gpio_regs.md / PADDIR_00_31 | miss (>5) | apb_gpio_regs.md / APB GPIO register reference > Regi |
| Q02 | semantic | how many clock cycles pass before a change on a pad can be | apb_gpio.sv / body part 6 | miss (>5) | apb_gpio_regs.md / APB GPIO register reference > Regi |
| Q03 | semantic | does reading the interrupt status register clear it | apb_gpio_regs.md / INTSTATUS_00_31 | 2 | apb_gpio_regs.md / APB GPIO register reference > Regi |
| Q04 | semantic | why would an input pin never update even though it is conf | apb_gpio_regs.md / GPIOEN_00_31 | miss (>5) | apb_gpio.sv / apb_gpio (ports/declarations) |
| Q05 | semantic | how can software raise one output pin without disturbing t | apb_gpio_regs.md / PADOUTSET_00_31 | miss (>5) | apb_gpio.sv / apb_gpio (ports/declarations) |
| Q06 | identifier | s_clk_en | apb_gpio.sv / body part 5 | 1 | apb_gpio.sv / apb_gpio (body part 5) |
| Q07 | identifier | s_is_int_rifa | apb_gpio.sv / body part 2 | 1 | apb_gpio.sv / apb_gpio (body part 2) |
| Q08 | identifier | r_status cleared on INTSTATUS read | apb_gpio.sv / body part 3 | 1 | apb_gpio.sv / apb_gpio (body part 3) |
| Q09 | identifier | is PSLVERR ever driven high | apb_gpio.sv / body part 9 + lines 780-786 | 1 | apb_gpio.sv / apb_gpio (body part 9, lines 780-7 |
| Q10 | crossref | INTTYPE_00_15 write decoding | apb_gpio.sv / body part 8 + REG_INTTYPE_00_15 | 1 | apb_gpio.sv / apb_gpio (body part 8, lines 330-3 |
| Q11 | crossref | r_gpio_inttype encoding for falling rising and both edges | apb_gpio_regs.md / INTTYPE_00_15 | 4 | apb_gpio.sv / apb_gpio (body part 7) |
| Q12 | crossref | gpio_padcfg bit meaning pull enable and drive strength | apb_gpio_regs.md / PADCFG_00_07 | 1 | apb_gpio_regs.md / APB GPIO register reference > Regi |

## Misses

4 of 12 queries did not retrieve the expected chunk within k=5:

- **Q01** (semantic) `how do I configure a pin to drive a value out instead of reading it`  
  expected `apb_gpio_regs.md / PADDIR_00_31`, got `apb_gpio_regs.md / APB GPIO register reference > Register map > PADOUT_00_31`
- **Q02** (semantic) `how many clock cycles pass before a change on a pad can be read by software`  
  expected `apb_gpio.sv / body part 6`, got `apb_gpio_regs.md / APB GPIO register reference > Register map > GPIOEN_00_31`
- **Q04** (semantic) `why would an input pin never update even though it is configured as an input`  
  expected `apb_gpio_regs.md / GPIOEN_00_31`, got `apb_gpio.sv / apb_gpio (ports/declarations)`
- **Q05** (semantic) `how can software raise one output pin without disturbing the other pins`  
  expected `apb_gpio_regs.md / PADOUTSET_00_31`, got `apb_gpio.sv / apb_gpio (ports/declarations)`

Where to look, by kind: `semantic` misses point at chunking or the embedding model; `identifier` misses point at the keyword index (check the identifier was extracted at all); `crossref` misses point at the merge weights in `query_tool.py`.
