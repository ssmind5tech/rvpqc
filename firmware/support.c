/* Minimal C runtime for bare-metal benchmarking: memory functions and a small allocator.
 *
 * PQClean's fips202 allocates every hash context with malloc and frees it after use.  The
 * allocator keeps freed blocks on exact-size free lists and hands them out again, so the heap
 * stays small (a few KB) even across thousands of hash calls - this matters on boards with
 * 256 KB of SRAM.  It also records the peak number of bytes in use, which the benchmark reports.
 * Note: these simple byte-wise memory routines are slower than an optimized libc, so the counts
 * are slightly conservative for code that copies large buffers. */
#include <stddef.h>
#include <stdint.h>

void *memcpy(void *d, const void *s, size_t n) { uint8_t *a = d; const uint8_t *b = s; while (n--) *a++ = *b++; return d; }
void *memmove(void *d, const void *s, size_t n) {
    uint8_t *a = d; const uint8_t *b = s;
    if (a < b) { while (n--) *a++ = *b++; } else { a += n; b += n; while (n--) *--a = *--b; }
    return d;
}
void *memset(void *d, int c, size_t n) { uint8_t *a = d; while (n--) *a++ = (uint8_t)c; return d; }
int memcmp(const void *x, const void *y, size_t n) {
    const uint8_t *a = x, *b = y;
    for (; n; n--, a++, b++) if (*a != *b) return *a - *b;
    return 0;
}

#ifndef HEAP_BYTES
#define HEAP_BYTES (8u * 1024u * 1024u)
#endif
#define HDR 16u                        /* block header: size (keeps 16-byte alignment) */
#define NCLASS 8
static uint8_t heap[HEAP_BYTES] __attribute__((aligned(16)));
static size_t heap_top, in_use, peak;
static struct { size_t size; void *head; } classes[NCLASS];

static void **free_list(size_t size, int create) {
    for (int i = 0; i < NCLASS; i++) {
        if (classes[i].size == size) return &classes[i].head;
        if (classes[i].size == 0 && create) { classes[i].size = size; return &classes[i].head; }
    }
    return NULL;
}

void *malloc(size_t n) {
    n = (n + 15u) & ~(size_t)15u;
    uint8_t *blk = NULL;
    void **fl = free_list(n, 0);
    if (fl && *fl) {                   /* reuse a freed block of exactly this size */
        blk = (uint8_t *)*fl - HDR;
        *fl = *(void **)*fl;
    } else {
        if (heap_top + HDR + n > HEAP_BYTES) return NULL;
        blk = heap + heap_top;
        heap_top += HDR + n;
        *(size_t *)blk = n;
    }
    in_use += n;
    if (in_use > peak) peak = in_use;
    return blk + HDR;
}
void free(void *p) {
    if (!p) return;
    size_t n = *(size_t *)((uint8_t *)p - HDR);
    void **fl = free_list(n, 1);
    in_use -= n;
    if (!fl) return;                   /* more sizes than classes: leak (never happens with fips202) */
    *(void **)p = *fl;
    *fl = p;
}
void *calloc(size_t a, size_t b) { void *p = malloc(a * b); if (p) memset(p, 0, a * b); return p; }
size_t rvpqc_heap_peak(void) { return peak; }

void rvpqc_fatal(void);                /* bench.c: report and stop */
void abort(void) { rvpqc_fatal(); for (;;) {} }
void exit(int code) { (void)code; abort(); }
