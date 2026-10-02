# apb_timer — specification

A 32-bit periodic timer with a prescaler and a compare-match interrupt, on an
APB slave interface. Written for this project (not from PULP) to check that
the pipeline works on a design it was not built around.

## Interface

| Signal | Dir | Width | Description |
|---|---|---|---|
| HCLK | in | 1 | Clock. All registers update on its rising edge. |
| HRESETn | in | 1 | Asynchronous active-low reset. |
| PADDR | in | 12 | APB address (byte offset). |
| PWDATA | in | 32 | APB write data. |
| PWRITE, PSEL, PENABLE | in | 1 | APB control. A write takes effect when PSEL, PENABLE and PWRITE are all high. |
| PRDATA | out | 32 | APB read data. |
| PREADY | out | 1 | Always 1 (no wait states). |
| PSLVERR | out | 1 | Always 0. |
| irq_o | out | 1 | Interrupt output. |

## Register map

Every register resets to 0x0. Offsets not listed read as 0 and ignore writes.

| Offset | Name | Access | Description |
|---|---|---|---|
| 0x00 | CTRL | R/W | bit[0] EN: 1 = the timer counts. bit[1] IRQ_EN: 1 = MATCH drives irq_o. Other bits read 0. |
| 0x04 | PRESCALE | R/W | bit[15:0]. The counter advances once every PRESCALE+1 clock cycles. Other bits read 0. |
| 0x08 | COMPARE | R/W | bit[31:0]. Compare value. |
| 0x0C | COUNT | R | bit[31:0]. Current counter value. Writes are ignored. |
| 0x10 | STATUS | R/W1C | bit[0] MATCH. Set when the counter matches COMPARE. Writing 1 clears it; writing 0 has no effect. |

## Behaviour

- While CTRL.EN is 1, a prescaler counts clock cycles. Every PRESCALE+1
  cycles it produces one tick.
- On a tick, if COUNT equals COMPARE, COUNT returns to 0 and STATUS.MATCH is
  set to 1. Otherwise COUNT increases by 1. The timer therefore repeats every
  COMPARE+1 ticks.
- While CTRL.EN is 0, COUNT and the prescaler hold their values. Clearing EN
  does not reset COUNT.
- irq_o is high while STATUS.MATCH is 1 and CTRL.IRQ_EN is 1. It is a level:
  it stays high until software clears MATCH by writing 1 to STATUS bit 0.
- If a tick sets MATCH in the same cycle that software writes 1 to clear it,
  MATCH stays set (the new event is not lost).
