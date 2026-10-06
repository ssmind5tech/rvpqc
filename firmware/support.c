/* Minimal C runtime for bare-metal benchmarking: memory functions and a bump allocator.
 * PQClean's fips202 allocates its hash contexts with malloc.  The allocator never reuses
 * memory, which keeps it tiny and deterministic; the heap is large enough for a full run.
 * Note: these simple byte-wise routines are slower than an optimized libc, so the counts are
 * slightly conservative for code that copies large buffers. */
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

#define HEAP_BYTES (8u * 1024u * 1024u)
static uint8_t heap[HEAP_BYTES] __attribute__((aligned(16)));
static size_t heap_used;
void *malloc(size_t n) {
    n = (n + 15u) & ~(size_t)15u;
    if (heap_used + n > HEAP_BYTES) return NULL;
    void *p = heap + heap_used;
    heap_used += n;
    return p;
}
void free(void *p) { (void)p; }
void *calloc(size_t a, size_t b) { void *p = malloc(a * b); if (p) memset(p, 0, a * b); return p; }
void abort(void) { *(volatile uint32_t *)0x100000u = 0x13333u | (2u << 16); for (;;) {} }
void exit(int code) { (void)code; abort(); }
