/* Minimal freestanding stdlib.h for rvpqc (implementations in support.c). */
#ifndef RVPQC_STDLIB_H
#define RVPQC_STDLIB_H
#include <stddef.h>
void *malloc(size_t n);
void *calloc(size_t a, size_t b);
void free(void *p);
void abort(void);
void exit(int code);
#endif
