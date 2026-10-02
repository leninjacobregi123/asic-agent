# APB GPIO register reference

Generated from `APB_GPIO_reference.xlsx` by `rag/xlsx_to_md.py`. Text is verbatim from the spreadsheet, including its errors.

## Overview

GPIO component manages the following features:
- 32 general purpose input/ouput pads
- configurable pull and drive strength on each GPIOs
- configurable event trigger on GPIO event

## Register map

| Register | Offset | Access | Reset | Description |
|---|---|---|---|---|
| PADDIR_00_31 | 0x0 | R/W | 0x0 | GPIO pad direction configuration register. |
| GPIOEN_00_31 | 0x4 | R/W | 0x0 | GPIO enable register. |
| PADIN_00_31 | 0x8 | R | 0x0 | GPIO pad input value register. |
| PADOUT_00_31 | 0xC | R/W | 0x0 | GPIO pad output value register. |
| PADOUTSET_00_31 | 0x10 | R/W | 0x0 | GPIO pad output set register. |
| PADOUTCLR_00_31 | 0x14 | R/W | 0x0 | GPIO pad output clear register. |
| INTEN_00_31 | 0x18 | R/W | 0x0 | GPIO pad interrupt enable configuration register. |
| INTTYPE_00_15 | 0x1C | R/W | 0x0 | GPIO pad interrupt type gpio 0 to 15 register. |
| INTTYPE_16_31 | 0x20 | R/W | 0x0 | GPIO pad interrupt type gpio 16 to 31 register. |
| INTSTATUS_00_31 | 0x24 | R | 0x0 | GPIO pad interrupt status register. |
| PADCFG_00_07 | 0x28 | R/W | 0x0 | GPIO pad pin 0 to 7 configuration register. |
| PADCFG_08_15 | 0x2C | R/W | 0x0 | GPIO pad pin 8 to 15 configuration register. |
| PADCFG_16_23 | 0x30 | R/W | 0x0 | GPIO pad pin 16 to 23 configuration register. |
| PADCFG_24_31 | 0x34 | R/W | 0x0 | GPIO pad pin 24 to 31 configuration register. |
| PADDIR_32_63 | 0x38 | R/W | 0x0 | GPIO pad direction configuration register. |
| GPIOEN_32_63 | 0x3C | R/W | 0x0 | GPIO enable register. |
| PADIN_32_63 | 0x40 | R | 0x0 | GPIO pad input value register. |
| PADOUT_32_63 | 0x44 | R/W | 0x0 | GPIO pad output value register. |
| PADOUTSET_32_63 | 0x48 | R/W | 0x0 | GPIO pad output set register. |
| PADOUTCLR_32_63 | 0x4C | R/W | 0x0 | GPIO pad output clear register. |
| INTEN_32_63 | 0x50 | R/W | 0x0 | GPIO pad interrupt enable configuration register. |
| INTTYPE_32_47 | 0x54 | R/W | 0x0 | GPIO pad interrupt type gpio 32 to 47 register. |
| INTTYPE_48_63 | 0x58 | R/W | 0x0 | GPIO pad interrupt type gpio 48 to 63 register. |
| INTSTATUS_32_63 | 0x5C | R | 0x0 | GPIO pad interrupt status register. |
| PADCFG_32_39 | 0x60 | R/W | 0x0 | GPIO pad pin 32 to 39 configuration register. |
| PADCFG_40_47 | 0x64 | R/W | 0x0 | GPIO pad pin 40 to 47 configuration register. |
| PADCFG_48_55 | 0x68 | R/W | 0x0 | GPIO pad pin 48 to 55 configuration register. |
| PADCFG_56_63 | 0x6C | R/W | 0x0 | GPIO pad pin 56 to 63 configuration register. |

### PADDIR_00_31

Offset 0x0, 32 bits, host access R/W, reset value 0x0. GPIO pad direction configuration register.

- **DIR** (register `PADDIR_00_31`, bit 0, width 32, host R/W, reset 0x0):
  GPIO[31:0] direction configuration bitfield:
  - bit[i]=1'b0: Input mode for GPIO[i]
  - bit[i]=1'b1: Output mode for GPIO[i]

### GPIOEN_00_31

Offset 0x4, 32 bits, host access R/W, reset value 0x0. GPIO enable register.

- **GPIOEN** (register `GPIOEN_00_31`, bit 0, width 32, host R/W, reset 0x0):
  GPIO[31:0] clock enable configuration bitfield:
  - bit[i]=1'b0: disable clock for GPIO[i]
  - bit[i]=1'b1: enable clock for GPIO[i]
  GPIOs are gathered by groups of 4. The clock gating of one group is done only if all 4 GPIOs are disabled. 
  Clock must be enabled for a GPIO if it's direction is configured in input mode.

### PADIN_00_31

Offset 0x8, 32 bits, host access R, reset value 0x0. GPIO pad input value register.

- **DATA_IN** (register `PADIN_00_31`, bit 0, width 32, host R, reset 0x0):
  GPIO[31:0] input data read bitfield. DATA_IN[i] corresponds to input data of GPIO[i].

### PADOUT_00_31

Offset 0xC, 32 bits, host access R/W, reset value 0x0. GPIO pad output value register.

- **DATA_OUT** (register `PADOUT_00_31`, bit 0, width 32, host R/W, reset 0x0):
  GPIO[31:0] output data read bitfield. DATA_OUT[i] corresponds to output data set on GPIO[i].

### PADOUTSET_00_31

Offset 0x10, 32 bits, host access R/W, reset value 0x0. GPIO pad output set register.

- **DATA_SET** (register `PADOUTSET_00_31`, bit 0, width 32, host W, reset -):
  GPIO[31:0] set bitfield:
  - bit[i]=1'b0: No change for GPIO[i]
  - bit[i]=1'b1: Sets GPIO[i] to 1

### PADOUTCLR_00_31

Offset 0x14, 32 bits, host access R/W, reset value 0x0. GPIO pad output clear register.

- **DATA_CLEAR** (register `PADOUTCLR_00_31`, bit 0, width 32, host W, reset -):
  GPIO[31:0] clear bitfield:
  - bit[i]=1'b0: No change for GPIO[i]
  - bit[i]=1'b1: Clears GPIO[i]

### INTEN_00_31

Offset 0x18, 32 bits, host access R/W, reset value 0x0. GPIO pad interrupt enable configuration register.

- **INTEN** (register `INTEN`, bit 0, width 32, host R/W, reset 0x0):
  GPIO[31:0] interrupt enable configuration bitfield:
  - bit[i]=1'b0: disable interrupt for GPIO[i]
  - bit[i]=1'b1: enable interrupt for GPIO[i]
  _(The spreadsheet names this register `INTEN`; placed under INTEN_00_31 by name prefix and the GPIO range above.)_

### INTTYPE_00_15

Offset 0x1C, 32 bits, host access R/W, reset value 0x0. GPIO pad interrupt type gpio 0 to 15 register.

- **INTTYPE0** (register `INTTYPE0`, bit 0, width 32, host R/W, reset 0x0):
  GPIO[15:0] interrupt type configuration bitfield:
  - bit[2*i+1:2*i]=2'b00: interrupt on falling edge for GPIO[i]
  - bit[2*i+1:2*i]=2'b01: interrupt on rising edge for GPIO[i]
  - bit[2*i+1:2*i]=2'b10: interrupt on rising and falling edge for GPIO[i]
  - bit[2*i+1:2*i]=2'b11: RFU
  _(The spreadsheet names this register `INTTYPE0`; placed under INTTYPE_00_15 by name prefix and the GPIO range above.)_

### INTTYPE_16_31

Offset 0x20, 32 bits, host access R/W, reset value 0x0. GPIO pad interrupt type gpio 16 to 31 register.

- **INTTYPE1** (register `INTTYPE1`, bit 0, width 32, host R/W, reset 0x0):
  GPIO[31:16] interrupt type configuration bitfield:
  - bit[2*i+1:2*i]=2'b00: interrupt on falling edge for GPIO[16+i]
  - bit[2*i+1:2*i]=2'b01: interrupt on rising edge for GPIO[16+i]
  - bit[2*i+1:2*i]=2'b10: interrupt on rising and falling edge for GPIO[16+i]
  - bit[2*i+1:2*i]=2'b11: RFU
  _(The spreadsheet names this register `INTTYPE1`; placed under INTTYPE_16_31 by name prefix and the GPIO range above.)_

### INTSTATUS_00_31

Offset 0x24, 32 bits, host access R, reset value 0x0. GPIO pad interrupt status register.

- **INTSTATUS** (register `INTSTATUS`, bit 0, width 32, host R, reset 0x0):
  GPIO[31:0] Interrupt status flags bitfield. INTSTATUS[i]=1 when interrupt received on GPIO[i]. INTSTATUS is cleared when it is red. GPIO interrupt line is also cleared when INTSTATUS register is red.
  _(The spreadsheet names this register `INTSTATUS`; placed under INTSTATUS_00_31 by name prefix and the GPIO range above.)_

### PADCFG_00_07

Offset 0x28, 32 bits, host access R/W, reset value 0x0. GPIO pad pin 0 to 7 configuration register.

- **GPIO0_CFG** (register `PADCFG_00_07`, bit 0, width 4, host R/W, reset 0x0):
  GPIO[0] pull activation configuration bitfield:
  - 1'b0: pull disabled
  - 1'b1: pull enabled

- **GPIO1_CFG** (register `PADCFG_00_07`, bit 4, width 4, host R/W, reset 0x0):
  GPIO[0] drive strength configuration bitfield:
  - 1'b0: low drive strength
  - 1'b1: high drive strength

- **GPIO2_CFG** (register `PADCFG_00_07`, bit 8, width 4, host R/W, reset 0x0):
  GPIO[1] pull activation configuration bitfield:
  - 1'b0: pull disabled
  - 1'b1: pull enabled

- **GPIO3_CFG** (register `PADCFG_00_07`, bit 12, width 4, host R/W, reset 0x0):
  GPIO[1] drive strength configuration bitfield:
  - 1'b0: low drive strength
  - 1'b1: high drive strength

- **GPIO4_CFG** (register `PADCFG_00_07`, bit 16, width 4, host R/W, reset 0x0):
  GPIO[2] pull activation configuration bitfield:
  - 1'b0: pull disabled
  - 1'b1: pull enabled

- **GPIO5_CFG** (register `PADCFG_00_07`, bit 20, width 4, host R/W, reset 0x0):
  GPIO[2] drive strength configuration bitfield:
  - 1'b0: low drive strength
  - 1'b1: high drive strength

- **GPIO6_CFG** (register `PADCFG_00_07`, bit 24, width 4, host R/W, reset 0x0):
  GPIO[3] pull activation configuration bitfield:
  - 1'b0: pull disabled
  - 1'b1: pull enabled

- **GPIO7_CFG** (register `PADCFG_00_07`, bit 28, width 4, host R/W, reset 0x0):
  GPIO[3] drive strength configuration bitfield:
  - 1'b0: low drive strength
  - 1'b1: high drive strength

### PADCFG_08_15

Offset 0x2C, 32 bits, host access R/W, reset value 0x0. GPIO pad pin 8 to 15 configuration register.

### PADCFG_16_23

Offset 0x30, 32 bits, host access R/W, reset value 0x0. GPIO pad pin 16 to 23 configuration register.

### PADCFG_24_31

Offset 0x34, 32 bits, host access R/W, reset value 0x0. GPIO pad pin 24 to 31 configuration register.

### PADDIR_32_63

Offset 0x38, 32 bits, host access R/W, reset value 0x0. GPIO pad direction configuration register.

- **DIR** (register `PADDIR_32_63`, bit 0, width 32, host R/W, reset 0x0):
  GPIO[63:32] direction configuration bitfield:
  - bit[i]=1'b0: Input mode for GPIO[i]
  - bit[i]=1'b1: Output mode for GPIO[i]

### GPIOEN_32_63

Offset 0x3C, 32 bits, host access R/W, reset value 0x0. GPIO enable register.

- **GPIOEN** (register `GPIOEN_32_63`, bit 0, width 32, host R/W, reset 0x0):
  GPIO[63:32] clock enable configuration bitfield:
  - bit[i]=1'b0: disable clock for GPIO[i]
  - bit[i]=1'b1: enable clock for GPIO[i]
  GPIOs are gathered by groups of 4. The clock gating of one group is done only if all 4 GPIOs are disabled. 
  Clock must be enabled for a GPIO if it's direction is configured in input mode.

### PADIN_32_63

Offset 0x40, 32 bits, host access R, reset value 0x0. GPIO pad input value register.

- **DATA_IN** (register `PADIN_32_63`, bit 0, width 32, host R, reset 0x0):
  GPIO[63:32] input data read bitfield. DATA_IN[i] corresponds to input data of GPIO[i].

### PADOUT_32_63

Offset 0x44, 32 bits, host access R/W, reset value 0x0. GPIO pad output value register.

- **DATA_OUT** (register `PADOUT_32_63`, bit 0, width 32, host R/W, reset 0x0):
  GPIO[63:32] output data read bitfield. DATA_OUT[i] corresponds to output data set on GPIO[i].

### PADOUTSET_32_63

Offset 0x48, 32 bits, host access R/W, reset value 0x0. GPIO pad output set register.

- **DATA_SET** (register `PADOUTSET_32_63`, bit 0, width 32, host W, reset -):
  GPIO[63:32] set bitfield:
  - bit[i]=1'b0: No change for GPIO[i]
  - bit[i]=1'b1: Sets GPIO[i] to 1

### PADOUTCLR_32_63

Offset 0x4C, 32 bits, host access R/W, reset value 0x0. GPIO pad output clear register.

- **DATA_CLEAR** (register `PADOUTCLR_32_63`, bit 0, width 32, host W, reset -):
  GPIO[63:32] clear bitfield:
  - bit[i]=1'b0: No change for GPIO[i]
  - bit[i]=1'b1: Clears GPIO[i]

### INTEN_32_63

Offset 0x50, 32 bits, host access R/W, reset value 0x0. GPIO pad interrupt enable configuration register.

- **INTEN** (register `INTEN_32_63`, bit 0, width 32, host R/W, reset 0x0):
  GPIO[63:32] interrupt enable configuration bitfield:
  - bit[i]=1'b0: disable interrupt for GPIO[i]
  - bit[i]=1'b1: enable interrupt for GPIO[i]

### INTTYPE_32_47

Offset 0x54, 32 bits, host access R/W, reset value 0x0. GPIO pad interrupt type gpio 32 to 47 register.

- **INTTYPE0** (register `INTTYPE_32_47`, bit 0, width 32, host R/W, reset 0x0):
  GPIO[47:32] interrupt type configuration bitfield:
  - bit[2*i+1:2*i]=2'b00: interrupt on falling edge for GPIO[i]
  - bit[2*i+1:2*i]=2'b01: interrupt on rising edge for GPIO[i]
  - bit[2*i+1:2*i]=2'b10: interrupt on rising and falling edge for GPIO[i]
  - bit[2*i+1:2*i]=2'b11: RFU

### INTTYPE_48_63

Offset 0x58, 32 bits, host access R/W, reset value 0x0. GPIO pad interrupt type gpio 48 to 63 register.

- **INTTYPE1** (register `INTTYPE_48_63`, bit 0, width 32, host R/W, reset 0x0):
  GPIO[63:48] interrupt type configuration bitfield:
  - bit[2*i+1:2*i]=2'b00: interrupt on falling edge for GPIO[16+i]
  - bit[2*i+1:2*i]=2'b01: interrupt on rising edge for GPIO[16+i]
  - bit[2*i+1:2*i]=2'b10: interrupt on rising and falling edge for GPIO[16+i]
  - bit[2*i+1:2*i]=2'b11: RFU

### INTSTATUS_32_63

Offset 0x5C, 32 bits, host access R, reset value 0x0. GPIO pad interrupt status register.

- **INTSTATUS** (register `INTSTATUS_32_63`, bit 0, width 32, host R, reset 0x0):
  GPIO[63:32] Interrupt status flags bitfield. INTSTATUS[i]=1 when interrupt received on GPIO[i]. INTSTATUS is cleared when it is red. GPIO interrupt line is also cleared when INTSTATUS register is red.

### PADCFG_32_39

Offset 0x60, 32 bits, host access R/W, reset value 0x0. GPIO pad pin 32 to 39 configuration register.

- **** (register `PADCFG_32_39`, bit , width , host -, reset -):
  

### PADCFG_40_47

Offset 0x64, 32 bits, host access R/W, reset value 0x0. GPIO pad pin 40 to 47 configuration register.

- **** (register `PADCFG_40_47`, bit , width , host -, reset -):
  

### PADCFG_48_55

Offset 0x68, 32 bits, host access R/W, reset value 0x0. GPIO pad pin 48 to 55 configuration register.

- **** (register `PADCFG_48_55`, bit , width , host -, reset -):
  

### PADCFG_56_63

Offset 0x6C, 32 bits, host access R/W, reset value 0x0. GPIO pad pin 56 to 63 configuration register.

- **** (register `PADCFG_56_63`, bit , width , host -, reset -):
  

### Bit fields with no matching register name

These rows in the spreadsheet name a register that does not appear in the register list, so which offset they describe is not stated.

- **GPIO4_PE** (register `PADCFG1`, bit 0, width 1, host R/W, reset 0x0):
  GPIO[4] pull activation configuration bitfield:
  - 1'b0: pull disabled
  - 1'b1: pull enabled

- **GPIO4_DS** (register `PADCFG1`, bit 1, width 1, host R/W, reset 0x0):
  GPIO[4] drive strength configuration bitfield:
  - 1'b0: low drive strength
  - 1'b1: high drive strength
