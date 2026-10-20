# rvpqc results: C-DAC ARIES ECO (hardware)

Measured on a C-DAC ARIES ECO board: THEJAS32 SoC, VEGA ET1031 RV32IM core at 100 MHz, 250 KB of usable SRAM.
Cycles and instructions come from the core's 64-bit `mcycle` and `minstret` counters. vegamon verified both high halves by forcing a wrap. The measurement overhead is calibrated and subtracted.

## Environment

| Item | Value |
|---|---|
| date (UTC) | 2026-10-20 00:26 |
| board | C-DAC ARIES ECO (THEJAS32, VEGA ET1031), boot ROM 1.0.0 (2020) |
| board clock | 100 MHz |
| RISC-V compiler | riscv64-unknown-elf-gcc 10.2.0 |
| host compiler (differential check) | cc 11.4.0 (Ubuntu 22.04) |
| PQClean commit | 0586a82 |
| slhdsa-c commit | 174c02e |
| configuration | rv32im -O2 |
| runs per operation | ML-KEM/ML-DSA 5, SLH-DSA 1 |
| heap for hash contexts | 24,576 B (peak use 416 B) |

## Results

| Algorithm | Operation | Time (median) | Cycles (median) | Min cycles | Max cycles | Instructions | CPI | Peak stack (B) | Code (B) | Check |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ML-KEM-512 | keypair | 11.7 ms | 1,171,185 | 1,171,067 | 1,171,253 | 994,262 | 1.178 | 6,024 | 22,596 | PASS |
| ML-KEM-512 | encaps | 13.1 ms | 1,310,239 | 1,310,132 | 1,310,318 | 1,065,577 | 1.230 | 8,664 | 22,596 | PASS |
| ML-KEM-512 | decaps | 15.7 ms | 1,570,151 | 1,570,044 | 1,570,230 | 1,232,199 | 1.274 | 9,416 | 22,596 | PASS |
| ML-KEM-768 | keypair | 19.1 ms | 1,912,420 | 1,912,045 | 1,912,478 | 1,610,321 | 1.188 | 10,152 | 22,852 | PASS |
| ML-KEM-768 | encaps | 21.5 ms | 2,148,192 | 2,147,817 | 2,148,201 | 1,758,633 | 1.222 | 13,288 | 22,852 | PASS |
| ML-KEM-768 | decaps | 24.9 ms | 2,491,322 | 2,490,947 | 2,491,331 | 1,979,267 | 1.259 | 14,376 | 22,852 | PASS |
| ML-KEM-1024 | keypair | 29.9 ms | 2,992,722 | 2,992,042 | 3,019,962 | 2,529,238 | 1.183 | 15,288 | 23,500 | PASS |
| ML-KEM-1024 | encaps | 32.8 ms | 3,279,674 | 3,278,945 | 3,306,914 | 2,707,561 | 1.211 | 18,904 | 23,500 | PASS |
| ML-KEM-1024 | decaps | 37.1 ms | 3,714,685 | 3,713,956 | 3,741,925 | 2,988,881 | 1.243 | 20,472 | 23,500 | PASS |
| ML-DSA-44 | keypair | 39.1 ms | 3,908,477 | 3,853,488 | 3,963,312 | 3,513,455 | 1.112 | 38,184 | 30,096 | PASS |
| ML-DSA-44 | sign | 85.2 ms | 8,524,042 | 6,044,779 | 10,250,978 | 7,054,549 | 1.208 | 51,640 | 30,096 | PASS |
| ML-DSA-44 | verify | 41.1 ms | 4,113,116 | 4,113,089 | 4,113,241 | 3,620,723 | 1.136 | 35,880 | 30,096 | PASS |
| ML-DSA-65 | keypair | 68.3 ms | 6,826,656 | 6,826,368 | 6,827,082 | 6,188,439 | 1.103 | 60,712 | 29,708 | PASS |
| ML-DSA-65 | sign | 217.3 ms | 21,729,862 | 9,472,121 | 42,912,410 | 17,778,076 | 1.222 | 79,288 | 29,708 | PASS |
| ML-DSA-65 | verify | 68.9 ms | 6,889,824 | 6,889,737 | 6,889,910 | 6,124,363 | 1.125 | 57,432 | 29,708 | PASS |
| ML-DSA-87 | keypair | 115.8 ms | 11,581,808 | 11,527,069 | 11,636,757 | 10,536,611 | 1.099 | 97,560 | 29,848 | PASS |
| ML-DSA-87 | sign | 152.7 ms | 15,271,084 | 15,270,948 | 33,633,943 | 13,199,758 | 1.157 | 122,280 | 29,848 | PASS |
| ML-DSA-87 | verify | 117.4 ms | 11,737,322 | 11,737,065 | 11,737,620 | 10,533,627 | 1.114 | 92,520 | 29,848 | PASS |
| SLH-DSA-SHA2-128f | keypair | 238.6 ms | 23,858,233 | 23,858,233 | 23,858,233 | 23,177,516 | 1.029 | 3,804 | 52,347 | PASS |
| SLH-DSA-SHA2-128f | sign | 5.59 s | 558,546,789 | 558,546,789 | 558,546,789 | 542,297,113 | 1.030 | 4,620 | 52,347 | PASS |
| SLH-DSA-SHA2-128f | verify | 331.9 ms | 33,193,385 | 33,193,385 | 33,193,385 | 32,202,317 | 1.031 | 3,964 | 52,347 | PASS |
| SLH-DSA-SHAKE-128f | keypair | 1.12 s | 111,699,793 | 111,699,793 | 111,699,793 | 106,362,242 | 1.050 | 3,520 | 52,347 | PASS |
| SLH-DSA-SHAKE-128f | sign | 26.12 s | 2,612,411,119 | 2,612,411,119 | 2,612,411,119 | 2,486,646,742 | 1.051 | 4,336 | 52,347 | PASS |
| SLH-DSA-SHAKE-128f | verify | 1.58 s | 158,258,276 | 158,258,276 | 158,258,276 | 150,700,793 | 1.050 | 3,664 | 52,347 | PASS |

Every key, ciphertext, shared secret and signature is byte-identical to a native build of the same code (differential check).
