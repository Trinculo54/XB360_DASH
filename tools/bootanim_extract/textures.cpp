// ---------------------------------------------------------------------------
//  Runs the five procedural texture generators headless and dumps their
//  pixels. CreateTexture and LockRect are replaced with versions that hand
//  back plain linear memory, which is all the generators actually use them
//  for -- they compute every address themselves and only ever read pBits out
//  of the locked rect, never the pitch.
//
//  Call order and arguments are copied exactly from BootAnim_Init, including
//  the two RNG re-seeds, because the seed feeds the Perlin permutation table
//  and therefore the output.
// ---------------------------------------------------------------------------
#include "ppc_config.h"
#include "ppc_context.h"
#include <cstdio>
#include <cstring>
#include <vector>
#include <string>

extern uint8_t* g_base;
bool RuntimeInit(const char* xexPath);
void SetupContext(PPCContext& ctx, uint32_t stackTop);
uint32_t GuestAlloc(uint32_t);

PPC_EXTERN_FUNC(sub_9801ADA8);   // RandTable_Build(obj, seed)
PPC_EXTERN_FUNC(sub_9801B000);   // reset rng object
PPC_EXTERN_FUNC(sub_980200D8);   // MakeTexture_Gradient  (256x128)
PPC_EXTERN_FUNC(sub_980204E0);   // MakeTexture_Radial    (256x256)
PPC_EXTERN_FUNC(sub_980209C8);   // MakeTexture_Noise     (256x256)

static uint32_t GU32(uint32_t a){ return __builtin_bswap32(*(uint32_t*)(g_base+a)); }
static void     SU32(uint32_t a, uint32_t v){ *(uint32_t*)(g_base+a) = __builtin_bswap32(v); }

// ---- fake texture objects -------------------------------------------------
struct FakeTex { uint32_t guestObj, bits, w, h; };
static std::vector<FakeTex> g_textures;

// D3DDevice_CreateTexture(device, w, h, levels, usage, format, ?, ?)
// r3=w r4=h r5=levels r6=? r7=? r8=format r9=? r10=?  (the device is implicit
// in the calling convention the title uses for these inline helpers)
void sub_980268E0(PPCContext& ctx, uint8_t* base)
{
    (void)base;
    uint32_t w = ctx.r3.u32, h = ctx.r4.u32;
    if (w == 0 || w > 4096) w = 256;
    if (h == 0 || h > 4096) h = 256;
    uint32_t obj  = GuestAlloc(64);
    uint32_t bits = GuestAlloc(w * h * 4 + 4096);
    SU32(obj + 0, bits); SU32(obj + 4, w); SU32(obj + 8, h);
    g_textures.push_back({ obj, bits, w, h });
    std::printf("  CreateTexture %ux%u  fmt=%08X -> obj %08X bits %08X\n",
                w, h, ctx.r8.u32, obj, bits);
    ctx.r3.u64 = obj;
}

// LockRect(texture, level, &LOCKED_RECT{pitch, pBits}, pRect, flags)
void sub_980268C0(PPCContext& ctx, uint8_t* base)
{
    (void)base;
    uint32_t tex = ctx.r3.u32, out = ctx.r5.u32;
    uint32_t bits = GU32(tex + 0), w = GU32(tex + 4);
    SU32(out + 0, w * 4);      // pitch
    SU32(out + 4, bits);       // pBits
}

// The title seeds its Perlin permutation from the hardware RNG, so the noise
// texture genuinely differs every boot. A fixed stream here just makes the
// dump reproducible; any stream is as faithful as any other.
void __imp__XeCryptRandom(PPCContext& ctx, uint8_t* base)
{
    (void)base;
    static uint32_t s = 0x12345678u;
    uint32_t p = ctx.r3.u32, n = ctx.r4.u32;
    for (uint32_t i = 0; i < n; ++i) { s = s * 1664525u + 1013904223u; g_base[p + i] = (uint8_t)(s >> 24); }
    ctx.r3.u64 = 0;
}

static void Call(PPCFunc* fn, PPCContext& c) { fn(c, g_base); }

static void WritePPM(const char* path, uint32_t bits, uint32_t w, uint32_t h)
{
    // Xbox 360 surface words are big-endian ARGB, so the byte order in guest
    // memory is A,R,G,B -- read straight through as bytes.
    std::string s = "P6\n" + std::to_string(w) + " " + std::to_string(h) + "\n255\n";
    for (uint32_t i = 0; i < w*h; ++i) {
        const uint8_t* p = g_base + bits + i*4;
        s.push_back((char)p[1]); s.push_back((char)p[2]); s.push_back((char)p[3]);
    }
    FILE* f = std::fopen(path, "wb"); std::fwrite(s.data(),1,s.size(),f); std::fclose(f);
}
static void WriteAlpha(const char* path, uint32_t bits, uint32_t w, uint32_t h)
{
    std::string s = "P5\n" + std::to_string(w) + " " + std::to_string(h) + "\n255\n";
    for (uint32_t i = 0; i < w*h; ++i) s.push_back((char)g_base[bits + i*4 + 0]);
    FILE* f = std::fopen(path, "wb"); std::fwrite(s.data(),1,s.size(),f); std::fclose(f);
}

int main(int argc, char** argv)
{
    const char* xex = argc > 1 ? argv[1] : "bootanim_noaudio.xex";
    if (!RuntimeInit(xex)) return 1;

    const uint32_t STACK  = 0x70100000;
    const uint32_t device = 0x60000000;     // never dereferenced: the two
    uint32_t rnd = GuestAlloc(64);          // functions that would are ours
    uint32_t outSlots = GuestAlloc(64);

    PPCContext c;
    #define CALL0(fn) do { Call(fn, c); } while (0)

    std::printf("seeding RNG table with 315...\n");
    SetupContext(c, STACK); c.r3.u64 = rnd; c.r4.u64 = 315; CALL0(sub_9801ADA8);

    std::printf("MakeTexture_Gradient 256x128 (A)\n");
    SetupContext(c, STACK);
    c.r3.u64 = device; c.r4.u64 = rnd; c.r5.u64 = 256; c.r6.u64 = 128; c.r7.u64 = outSlots + 0;
    CALL0(sub_980200D8);

    std::printf("MakeTexture_Radial 256x256 (B, variant 0)\n");
    SetupContext(c, STACK);
    c.r3.u64 = device; c.r4.u64 = rnd; c.r5.u64 = 256; c.r6.u64 = 256; c.r7.u64 = outSlots + 4; c.r8.u64 = 0;
    CALL0(sub_980204E0);

    std::printf("re-seeding RNG table with 716...\n");
    SetupContext(c, STACK); c.r3.u64 = rnd; CALL0(sub_9801B000);
    SetupContext(c, STACK); c.r3.u64 = rnd; c.r4.u64 = 716; CALL0(sub_9801ADA8);

    std::printf("MakeTexture_Gradient 256x128 (C)\n");
    SetupContext(c, STACK);
    c.r3.u64 = device; c.r4.u64 = rnd; c.r5.u64 = 256; c.r6.u64 = 128; c.r7.u64 = outSlots + 8;
    CALL0(sub_980200D8);

    std::printf("MakeTexture_Radial 256x256 (D, variant 1)\n");
    SetupContext(c, STACK);
    c.r3.u64 = device; c.r4.u64 = rnd; c.r5.u64 = 256; c.r6.u64 = 256; c.r7.u64 = outSlots + 12; c.r8.u64 = 1;
    CALL0(sub_980204E0);

    std::printf("MakeTexture_Noise 256x256 (E)\n");
    SetupContext(c, STACK);
    c.r3.u64 = device; c.r4.u64 = rnd; c.r5.u64 = 256; c.r6.u64 = 256; c.r7.u64 = outSlots + 16;
    CALL0(sub_980209C8);

    const char* names[] = { "tex0_gradient_a", "tex1_radial_0", "tex2_gradient_c", "tex3_radial_1", "tex4_noise" };
    std::printf("\n%zu textures generated\n", g_textures.size());
    for (size_t i = 0; i < g_textures.size() && i < 5; ++i) {
        const FakeTex& t = g_textures[i];
        char p[256];
        std::snprintf(p, sizeof p, "%s.ppm", names[i]);      WritePPM  (p, t.bits, t.w, t.h);
        std::snprintf(p, sizeof p, "%s_alpha.pgm", names[i]); WriteAlpha(p, t.bits, t.w, t.h);
        // quick content report so a blank or garbage buffer is obvious
        uint32_t nonzero = 0; uint32_t distinct = 0; uint32_t prev = 0xFFFFFFFF;
        for (uint32_t k = 0; k < t.w*t.h; ++k) { uint32_t v = GU32(t.bits + k*4); if (v) ++nonzero; if (v != prev) { ++distinct; prev = v; } }
        std::printf("  %-16s %ux%u  nonzero %u/%u  runs %u  first px %08X\n",
                    names[i], t.w, t.h, nonzero, t.w*t.h, distinct, GU32(t.bits));
    }
    return 0;
}
