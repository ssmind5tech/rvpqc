    .section .text.init
    .globl _start
_start:
    la   sp, __stack_top
    la   t0, __bss_start
    la   t1, __bss_end
1:  bgeu t0, t1, 2f
    sw   zero, 0(t0)
    addi t0, t0, 4
    j    1b
2:
#ifdef BOARD_MON
    la   t0, rvpqc_mon_return   # vegamon passes its return address in a0
    sw   a0, 0(t0)
#endif
    call main
3:  j    3b
