# vegamon: a tiny resident loader for VEGA / THEJAS32 boards (ARIES v2/v3/ECO), also built for QEMU 'virt'.
#
# Uploaded once through the boot ROM's XMODEM loader, it stays in the bottom 8 KB of SRAM and loads
# further programs over the same UART, so a test run needs only one reset.
    .section .text.init
    .globl _start
_start:                         # cold entry (boot ROM, or QEMU -kernel)
    csrci mstatus, 8            # no interrupts: the boot ROM may leave some enabled
    csrw mie, zero
    la   sp, __mon_stack_top
    la   t0, __bss_start         # the boot ROM loads only the image: clear .bss ourselves
    la   t1, __bss_end
0:  bgeu t0, t1, 3f
    sw   zero, 0(t0)
    addi t0, t0, 4
    j    0b
3:  la   t0, mon_trap
    csrw mtvec, t0
    call mon_main
1:  j    1b

# Programs return here (a0 = exit code).  Everything the program set up is discarded.
    .globl mon_reenter
mon_reenter:
    la   sp, __mon_stack_top
    la   t0, mon_trap
    csrw mtvec, t0
    call mon_resume
2:  j    2b

# mon_jump(entry, reenter, caps): start a loaded program with a0 = return address, a1 = capabilities.
    .globl mon_jump
mon_jump:
    mv   t0, a0
    mv   a0, a1
    mv   a1, a2
    fence.i                     # the program was written through the data path
    jr   t0

# Trap entry: frame[i] = x_i, call mon_trap_c(frame), restore, mret.
    .balign 4
    .globl mon_trap
mon_trap:
    addi sp, sp, -128
    .irp r,1,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
    sw   x\r, (\r*4)(sp)
    .endr
    addi t0, sp, 128
    sw   t0, 8(sp)
    mv   a0, sp
    call mon_trap_c
    .irp r,1,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
    lw   x\r, (\r*4)(sp)
    .endr
    addi sp, sp, 128
    mret
