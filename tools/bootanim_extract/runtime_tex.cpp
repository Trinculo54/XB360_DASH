// ---------------------------------------------------------------------------
//  Minimal headless runtime for the recompiled bootanim.xex.
//
//  This is NOT an emulator and does not try to be one. It provides the four
//  things the recompiled code needs in order to run pure-data functions:
//    1. a 4 GiB guest address space with the XEX image mapped at 0x98000000,
//    2. the out-of-line register save/restore helpers XenonRecomp leaves to
//       the host (implemented from the actual bytes in the image),
//    3. stubs for the 101 xboxkrnl imports, none of which the data paths call,
//    4. an override of the title's operator new so allocation never has to go
//       through the guest CRT (which would need heap and TLS init first).
//
//  With those in place, Curves_Init runs exactly as it does on hardware and
//  leaves the whole animation dataset sitting in guest memory to be read out.
// ---------------------------------------------------------------------------
#include "ppc_config.h"
#include "ppc_context.h"
#include <sys/mman.h>
#include <cstdio>
#include <cstdlib>

uint8_t* g_base = nullptr;

// ---- register save/restore helpers ---------------------------------------
// savegprlr_N: std r(N..31) at -8*(33-N)... -16(r1), then stw lr(r12) at -8(r1)
// restgprlr_N: the inverse, and it restores LR so the caller can just return.
#include "savehelpers.inc"

// ---- kernel import stubs --------------------------------------------------
// Every one of these traps if it is ever reached: the whole point of the
// exercise is that the data paths touch none of them, so a hit means the
// entry point chosen was not as self-contained as the static analysis said.
static void Trap(const char* name) { std::fprintf(stderr, "\n!! guest called import %s\n", name); std::exit(3); }
#define IMPORT_STUB(name) void __imp__##name(PPCContext& ctx, uint8_t* base) { (void)ctx; (void)base; Trap(#name); }
#include IMPORT_STUBS_FILE

void _xstart(PPCContext& ctx, uint8_t* base) { (void)ctx; (void)base; Trap("_xstart"); }

// ---- guest heap -----------------------------------------------------------
// A bump allocator in a region of guest memory the image does not use.
// operator new is the title's sub_98021018; overriding the weak symbol the
// recompiler emits keeps every caller inside the guest none the wiser.
bool g_logAllocs = false;
static uint32_t g_heapPtr  = 0x40000000;
static uint32_t g_heapEnd  = 0x50000000;
uint32_t GuestAlloc(uint32_t size)
{
    size = (size + 31u) & ~31u;
    if (g_heapPtr + size > g_heapEnd) { std::fprintf(stderr, "guest heap exhausted\n"); std::exit(4); }
    uint32_t p = g_heapPtr; g_heapPtr += size;
    std::memset(g_base + p, 0, size);
    if (g_logAllocs) std::fprintf(stderr, "  alloc %8u bytes at %08X\n", size, p);
    return p;
}
uint32_t GuestHeapUsed() { return g_heapPtr - 0x40000000; }

void sub_98021018(PPCContext& ctx, uint8_t* base)   // operator new(size_t)
{
    (void)base;
    ctx.r3.u64 = GuestAlloc(ctx.r3.u32);
}
void sub_98021050(PPCContext& ctx, uint8_t* base)   // operator delete(void*)
{
    (void)ctx; (void)base;                          // bump allocator: nothing to do
}

// ---- image loading --------------------------------------------------------
bool RuntimeInit(const char* xexPath)
{
    void* mem = mmap(nullptr, PPC_MEMORY_SIZE, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (mem == MAP_FAILED) { std::perror("mmap"); return false; }
    g_base = (uint8_t*)mem;

    // This XEX is stored uncompressed and unencrypted, so the basefile that
    // begins at 0x2000 is already in image layout -- it maps to the image
    // base one-for-one, which is exactly why "file = guest - base + 0x2000"
    // held for every address in the static analysis.
    FILE* f = std::fopen(xexPath, "rb");
    if (!f) { std::perror(xexPath); return false; }
    std::fseek(f, 0, SEEK_END); long sz = std::ftell(f); std::fseek(f, 0x2000, SEEK_SET);
    long imageSize = sz - 0x2000;
    if (std::fread(g_base + PPC_IMAGE_BASE, 1, imageSize, f) != (size_t)imageSize)
    { std::fprintf(stderr, "short read\n"); std::fclose(f); return false; }
    std::fclose(f);
    std::printf("mapped %ld bytes at guest %08llX\n", imageSize, (unsigned long long)PPC_IMAGE_BASE);
    return true;
}

void SetupContext(PPCContext& ctx, uint32_t stackTop)
{
    std::memset(&ctx, 0, sizeof(ctx));
    ctx.r1.u64 = stackTop;      // guest stack pointer, grows down
    ctx.r13.u64 = 0;
    ctx.fpscr.loadFromHost();
}
