# rvpqc results (hardware)

Cycles per operation measured on the board with the core's 64-bit cycle counter (measurement overhead calibrated and subtracted); time assumes the board clock below. Instructions and CPI are shown when the core also counts retired instructions.

## Environment

| Item | Value |
|---|---|
| date (UTC) | 2026-10-21 18:38 |
| RISC-V compiler | riscv64-unknown-elf-gcc () 10.2.0 |
| QEMU | not used |
| board | C-DAC ARIES (THEJAS32, VEGA ET1031) |
| board clock | 100 MHz |
| core capabilities (probed) | mcycle=counts, mcycleh=present, minstret=counts, minstreth=present, cycle=present, cycleh=present, instret=present, instreth=present, time=present, custom-0 traps=NO (ignored), ecall traps=yes, ebreak traps=no, illegal all-zero word traps=no, mcycleh verified, minstreth verified, mtime verified, 2001-instruction loop = 3010 cycles, CPI 1.50 |
| counter | cycles + instructions (64-bit) |
| heap for hash contexts | 24576 B |
| host compiler (differential check) | cc (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0 |
| host | Linux x86_64 |
| PQClean commit | 0586a82 |
| slhdsa-c commit | 174c02e |
| configurations | rv32im -O2 |
| extra compiler flags | -fno-schedule-insns |
| runs per operation | ML-KEM/ML-DSA 5, SLH-DSA 2 |

## Results: rv32im -O2

| Algorithm | Operation | Cycles (median) | Time (ms) | Min cycles | Max cycles | Instructions (median) | CPI | Peak stack (B) | Peak heap (B) | Code (B) | Check |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ML-KEM-768 | keypair | 1,405,059 | 14.05 | 1,404,663 | 1,405,125 | 1,149,048 | 1.223 | 10,052 | 416 | 19,572 | PASS |
| ML-KEM-768 | encaps | 1,640,566 | 16.41 | 1,640,170 | 1,640,575 | 1,296,648 | 1.265 | 13,188 | 416 | 19,572 | PASS |
| ML-KEM-768 | decaps | 1,985,231 | 19.85 | 1,984,835 | 1,985,240 | 1,517,209 | 1.308 | 14,276 | 416 | 19,572 | PASS |
| ML-DSA-44 | keypair | 2,724,544 | 27.25 | 2,692,398 | 2,756,615 | 2,440,376 | 1.116 | 38,072 | 416 | 26,824 | PASS |
| ML-DSA-44 | sign | 6,955,198 | 69.55 | 4,793,182 | 8,364,106 | 5,628,254 | 1.236 | 51,528 | 416 | 26,824 | PASS |
| ML-DSA-44 | verify | 2,984,524 | 29.85 | 2,984,498 | 2,984,647 | 2,597,909 | 1.149 | 35,768 | 416 | 26,824 | PASS |

Cycle counts are from the board's own counter, so they include pipeline stalls and memory effects. Code size covers the algorithm and its hash code, excluding the benchmark harness. For SLH-DSA it covers the whole slhdsa-c library (all 12 parameter sets and both hash families), so it is the same for every SLH-DSA variant.
