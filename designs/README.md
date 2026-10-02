# Designs

Each design lives in its own folder with a `project.json` describing it. To
work on one, make its `project.json` the active one (`config/project.json`, or
the app's **Project** page).

| Design | What it is |
|---|---|
| `apb_gpio/` | PULP Platform 32-pad GPIO controller with interrupts (Solderpad licence). Includes test metadata and fault scenarios for verification runs. |
| `apb_timer/` | A 32-bit periodic timer with prescaler and compare-match interrupt, written for this project. |

## Adding your own design

```
designs/my_block/
  rtl/my_block.sv          design source (.sv / .v)
  docs/my_block_spec.md    the specification, as text or Markdown
  tb/tb_my_block.sv        the testbench (contract below)
  project.json             copy one of the examples and edit the paths
```

Spreadsheet register maps can be converted first:
`python3 -m asic_agent.knowledge.xlsx_to_md spec.xlsx --out docs/spec.md`.

### Testbench contract

- One SystemVerilog task per test, `task automatic t_<name>();`, selected at run
  time with `+TEST=<name>` from a dispatcher `case` statement.
- Each test prints exactly one line: `RESULT <name> PASS` or
  `RESULT <name> FAIL <first failing check>`.
- Two marker comments: `// @agent-tests` (new test tasks are inserted above it)
  and `// @agent-dispatch` (new dispatcher entries are inserted above it).
- List the helper tasks a new test may use in `project.json`
  (`testbench.helper_tasks`) and describe the testbench signals in
  `testbench.signals_note`.

`designs/apb_timer/tb/tb_apb_timer.sv` is a compact example.

### project.json fields

| Field | Meaning |
|---|---|
| `design.top`, `design.rtl`, `design.spec` | top module, RTL file, specification file |
| `design.clock` | `{"port": "HCLK", "period_ns": 10.0}` for timing constraints |
| `knowledge_sources` | folders indexed into the knowledge base, each with `kind`: `spec`, `rtl` or `testbench` |
| `testbench.file`, `.smoke`, `.regression_exclude`, `.helper_tasks`, `.signals_note` | the testbench and how to use it |
| `testbench.metadata` (optional) | per-test spec references used by rule-based diagnosis |
| `fault_scenarios` (optional) | faults the app can inject for verification runs |

Paths are relative to the repository root.
