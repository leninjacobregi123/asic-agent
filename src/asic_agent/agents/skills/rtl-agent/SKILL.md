# RTL agent skill — SystemVerilog edit rules and idioms

Loaded into the RTL agent's system prompt by `src/asic_agent/agents/rtl.py`.

Provenance: written 1 Oct 2026 after pilot runs showed the 7B local model
repeatedly proposing `interrupt = r_status[0]` (one pad only) and per-bit
loops driving a 1-bit output. The idioms are generic SystemVerilog, not
answers for one test; the change is reported as a prompt intervention.

## Edit rules

- Change the fewest lines that make the requirement true. Do not reformat,
  rename, or edit comments.
- Keep every port and every existing signal name.
- A 1-bit output can only be driven by a 1-bit expression. Never index or loop
  over a 1-bit output.
- Do not add a second driver: if a signal already has an `assign` or is set in
  an `always_ff`, change that driver instead of adding another.
- Check widths: a per-pad vector is `[PAD_NUM-1:0]`; using only bit `[0]`
  handles pad 0 alone.

## Idioms

| Need | Write |
|---|---|
| 1-bit flag: any bit of a vector is set | `|vec` (reduction OR) |
| 1-bit flag: all bits set | `&vec` |
| level that stays high until cleared | drive the output from the *register* that holds the state, not from the one-cycle event that sets it |
| one-cycle pulse on a rising edge of `x` | `x & ~x_q` where `x_q` is `x` delayed one clock |
| rising vs falling edge of synchronised `s` with previous `p` | rise: `s & ~p`  fall: `~s & p` |
| select by a 2-bit type field `t` | `t == 2'b01`, or `~t[1] & t[0]` |
