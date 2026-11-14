// ---------------------------------------------------------------------------
//  Records everything the boot animation hands to Direct3D, frame by frame.
//
//  frames.cpp kept two matrices per frame. This keeps the lot: every draw the
//  title issues, with the vertices and indices it draws, the shaders, every
//  shader constant, the textures, the render target and the render states --
//  so a renderer can replay the title's own frames rather than imitate them.
//
//  How: the title's calls into its D3D are ordinary functions, and XenonRecomp
//  emits each as a weak `sub_X`, so defining `sub_X` here takes the call. The
//  draws are three of them (trace.cpp took them for inlined; one had been
//  read as SetBlend):
//
//    sub_9802C190  DrawIndexedVertices(dev, prim, baseVertex, startIndex, numIndices)
//    sub_9802BD78  DrawVertices(dev, prim, startVertex, numVertices)
//    sub_9802BD30  DrawVerticesUP(dev, prim, numVertices, data, stride)
//
//  and the buffers they draw from are created and locked through four more,
//  which the other tools stubbed to return 0 -- every buffer then "locked" to
//  guest address 0. Here each gets memory of its own:
//
//    sub_9802E738  CreateVertexBuffer(size)      sub_9802F918  VertexBuffer::Lock
//    sub_9802E810  CreateIndexBuffer(size)       sub_9802F968  IndexBuffer::Lock
//
//  Render states are recorded as the values the title asks for (its setters
//  are wrapped, then run): see kState.
//
//    capture <xex> <out.bin> [fps=60] [seconds=6.5]     the recording
//    capture <xex> --text <t> [fps]                     one frame, as text
//
//  Output (little-endian): see WriteFile at the bottom, and BootTrace in
//  src\BootAnimAssets.h, which reads it.
// ---------------------------------------------------------------------------
#include "ppc_config.h"
#include "ppc_context.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>

extern uint8_t* g_base;
bool RuntimeInit(const char*);
void SetupContext(PPCContext&, uint32_t);
uint32_t GuestAlloc(uint32_t);

PPC_EXTERN_FUNC(sub_9800F5A8);   // BootAnim_Init
PPC_EXTERN_FUNC(sub_9800FB30);   // BootAnim_RenderFrame
PPC_EXTERN_FUNC(sub_9800EA10);   // BootAnim_Tick(dt)

static uint32_t GU32(uint32_t a) { return __builtin_bswap32(*(uint32_t*)(g_base + a)); }
static void     SU32(uint32_t a, uint32_t v) { *(uint32_t*)(g_base + a) = __builtin_bswap32(v); }
static float    GF32(uint32_t a) { uint32_t v = GU32(a); float f; std::memcpy(&f, &v, 4); return f; }

static const uint32_t FAKE_DEVICE = 0x60000000;
static float g_time = 0.0f;
static bool  g_recording = false;

// ---- what the title has created ---------------------------------------------
static const struct { uint32_t addr; const char* name; } kShaders[] = {
    {0x98000690,"vs00_Basic"},      {0x980007D8,"ps01_Solid"},
    {0x980008E0,"vs02_Textured"},   {0x98000A08,"ps03_MatClr"},
    {0x98000AD0,"ps04_TwoTexUOfs"}, {0x98000D60,"vs05_ShadowProj"},
    {0x98001018,"ps06_Texture"},    {0x98001128,"ps07_TexMatClr"},
    {0x98001248,"ps08_GlowComposite"},{0x98001528,"vs09_LitWorld"},
    {0x98001740,"ps10_LitMaterial"},{0x98001C40,"ps11_BlurA"},
    {0x98001EF0,"ps12_BlurB"},      {0x98002338,"vs13_Overlay"},
    {0x98002418,"ps14_ClrAdd"},     {0x98002658,"vs15_Quad"},
    {0x980027A0,"ps16_Copy"},
};
struct Texture { uint32_t bits, w, h; bool target; };     // target: something is resolved into it
struct Buffer  { uint32_t data, size; };
struct Decl    { std::vector<uint32_t> words; };          // (stream<<16 | offset, type/usage) pairs
static std::map<uint32_t, int> g_shaderOf;                // handle -> index into kShaders
static std::map<uint32_t, int> g_textureOf, g_surfaceOf, g_bufferOf, g_declOf;   // handle -> index
static std::vector<Texture> g_textures;
static std::vector<std::pair<uint32_t, uint32_t>> g_surfaces;   // w, h
static std::vector<Buffer> g_buffers;
static std::vector<Decl> g_decls;

static int Lookup(const std::map<uint32_t, int>& m, uint32_t h) { auto it = m.find(h); return it == m.end() ? -1 : it->second; }

static void CreateShader(PPCContext& ctx)
{
    int index = -1;
    for (int i = 0; i < 17; ++i) if (kShaders[i].addr == ctx.r3.u32) index = i;
    uint32_t h = GuestAlloc(64); g_shaderOf[h] = index; ctx.r3.u64 = h;
}
void sub_98028120(PPCContext& ctx, uint8_t*) { CreateShader(ctx); }   // CreateVertexShader
void sub_98027F38(PPCContext& ctx, uint8_t*) { CreateShader(ctx); }   // CreatePixelShader
void sub_980268E0(PPCContext& ctx, uint8_t*)                          // CreateTexture
{
    uint32_t w = ctx.r3.u32, h = ctx.r4.u32;
    if (!w || w > 8192) w = 256;
    if (!h || h > 8192) h = 256;
    uint32_t obj = GuestAlloc(128), bits = GuestAlloc(w * h * 4 + 4096);
    SU32(obj + 0, bits); SU32(obj + 4, w); SU32(obj + 8, h);
    g_textureOf[obj] = (int)g_textures.size(); g_textures.push_back({ bits, w, h, false });
    ctx.r3.u64 = obj;
}
void sub_980268C0(PPCContext& ctx, uint8_t*)                          // LockRect
{
    uint32_t tex = ctx.r3.u32, out = ctx.r5.u32;
    SU32(out + 0, GU32(tex + 4) * 4); SU32(out + 4, GU32(tex + 0));
}
void sub_98026A00(PPCContext& ctx, uint8_t*)                          // CreateSurface
{
    uint32_t h = GuestAlloc(128);
    g_surfaceOf[h] = (int)g_surfaces.size(); g_surfaces.push_back({ ctx.r3.u32, ctx.r4.u32 });
    ctx.r3.u64 = h;
}
void sub_98027C70(PPCContext& ctx, uint8_t*)                          // CreateVertexDeclaration
{
    Decl d;
    for (int i = 0; i < 8; ++i) {
        uint32_t p = ctx.r3.u32 + i * 8;
        if ((GU32(p) >> 16) == 0xFFFF) break;
        d.words.push_back(GU32(p)); d.words.push_back(GU32(p + 4));
    }
    uint32_t h = GuestAlloc(128);
    g_declOf[h] = (int)g_decls.size(); g_decls.push_back(d);
    ctx.r3.u64 = h;
}
static void CreateBuffer(PPCContext& ctx)
{
    uint32_t size = ctx.r3.u32, obj = GuestAlloc(64), data = GuestAlloc(size + 64);
    SU32(obj + 24, data); SU32(obj + 28, size);
    g_bufferOf[obj] = (int)g_buffers.size(); g_buffers.push_back({ data, size });
    ctx.r3.u64 = obj;
}
void sub_9802E738(PPCContext& ctx, uint8_t*) { CreateBuffer(ctx); }   // CreateVertexBuffer
void sub_9802E810(PPCContext& ctx, uint8_t*) { CreateBuffer(ctx); }   // CreateIndexBuffer
void sub_9802F918(PPCContext& ctx, uint8_t*) { ctx.r3.u64 = GU32(ctx.r3.u32 + 24) + ctx.r4.u32; }   // VertexBuffer::Lock
void sub_9802F968(PPCContext& ctx, uint8_t*) { ctx.r3.u64 = GU32(ctx.r3.u32 + 24) + ctx.r4.u32; }   // IndexBuffer::Lock
void sub_9802E800(PPCContext& ctx, uint8_t*) { ctx.r3.u64 = 0; }      // the unlocks
void sub_9802E8C0(PPCContext& ctx, uint8_t*) { ctx.r3.u64 = 0; }
void sub_980250F0(PPCContext& ctx, uint8_t*) { ctx.r3.u64 = 0; }

// ---- the state a draw is made with --------------------------------------------
// Render states, by the setter that takes each (device in r3, value in r4).
enum { kZEnable, kZWrite, kZFunc, kCull, kBlendEnable, kSrcBlend, kDestBlend, kAlphaTest, kAlphaFunc, kAlphaRef,
       kColorWrite, kStateCount };
static uint32_t g_state[kStateCount];
#define STATE(addr, index) \
    extern "C" void __imp__sub_##addr(PPCContext&, uint8_t*); \
    void sub_##addr(PPCContext& ctx, uint8_t* base) { g_state[index] = ctx.r4.u32; __imp__sub_##addr(ctx, base); }
STATE(98022448, kZEnable)   STATE(98022488, kZWrite)     STATE(980224B8, kZFunc)     STATE(98021D20, kCull)
STATE(98021DB8, kBlendEnable) STATE(98021ED8, kSrcBlend) STATE(98021F68, kDestBlend)
STATE(98021D80, kAlphaTest) STATE(98022240, kAlphaFunc)  STATE(980221E0, kAlphaRef)  STATE(980229E8, kColorWrite)

static int g_vs = -1, g_ps = -1, g_decl = -1, g_vb = -1, g_ib = -1, g_target = -1;
static uint32_t g_stride = 0;
static int g_tex[8] = { -1, -1, -1, -1, -1, -1, -1, -1 };
static const int kVsRegs = 16, kPsRegs = 40;
static float g_vsC[kVsRegs * 4], g_psC[kPsRegs * 4];

void sub_98027950(PPCContext& ctx, uint8_t*) { g_vs = Lookup(g_shaderOf, ctx.r4.u32); }       // SetVertexShader
void sub_98027748(PPCContext& ctx, uint8_t*) { g_ps = Lookup(g_shaderOf, ctx.r4.u32); }       // SetPixelShader
void sub_98027B68(PPCContext& ctx, uint8_t*) { g_decl = Lookup(g_declOf, ctx.r4.u32); }       // SetVertexDeclaration
void sub_98026B38(PPCContext& ctx, uint8_t*)                                                  // SetTexture(stage, texture)
{ if (ctx.r4.u32 < 8) g_tex[ctx.r4.u32] = Lookup(g_textureOf, ctx.r5.u32); }
void sub_980240E0(PPCContext& ctx, uint8_t*)                                                  // SetStreamSource(stream, vb, offset, stride)
{ if (ctx.r4.u32 == 0) { g_vb = Lookup(g_bufferOf, ctx.r5.u32); g_stride = ctx.r7.u32; } }
void sub_98024200(PPCContext& ctx, uint8_t*) { g_ib = Lookup(g_bufferOf, ctx.r4.u32); }       // SetIndices
static void SetConstants(float* dst, int regs, PPCContext& ctx)
{
    uint32_t start = ctx.r4.u32, data = ctx.r5.u32, count = ctx.r6.u32;
    for (uint32_t i = 0; i < count * 4; ++i)
        if (start * 4 + i < (uint32_t)regs * 4) dst[start * 4 + i] = GF32(data + i * 4);
}
void sub_98027560(PPCContext& ctx, uint8_t*) { SetConstants(g_vsC, kVsRegs, ctx); }           // SetVertexShaderConstantF
void sub_98027638(PPCContext& ctx, uint8_t*) { SetConstants(g_psC, kPsRegs, ctx); }           // SetPixelShaderConstantF
void sub_980235C0(PPCContext&, uint8_t*) {}                                                   // sampler min filter
void sub_98023768(PPCContext&, uint8_t*) {}                                                   // sampler mag filter
void sub_98024948(PPCContext&, uint8_t*) {}                                                   // (viewport / scissor)

// ---- the recording ---------------------------------------------------------------
// One command stream per frame. Vertex and index data go to a pool, each
// distinct run of bytes once: the meshes are the same bytes every frame, and
// a ribbon's are new ones.
enum : uint32_t { kCmdTarget = 1, kCmdClear = 2, kCmdResolve = 3, kCmdDraw = 4 };
static std::vector<std::vector<uint32_t>> g_frames;
static std::vector<uint32_t>* g_out = nullptr;
static std::vector<uint8_t> g_pool;
static std::map<uint64_t, std::vector<std::pair<uint32_t, uint32_t>>> g_pooled;   // hash -> (offset, size)
static bool g_text = false;

static uint32_t Pool(uint32_t guest, uint32_t size)
{
    const uint8_t* p = g_base + guest;
    uint64_t hash = 1469598103934665603ull;
    for (uint32_t i = 0; i < size; ++i) hash = (hash ^ p[i]) * 1099511628211ull;
    for (auto& seen : g_pooled[hash])
        if (seen.second == size && !std::memcmp(g_pool.data() + seen.first, p, size)) return seen.first;
    uint32_t at = (uint32_t)g_pool.size();
    g_pool.insert(g_pool.end(), p, p + size);
    while (g_pool.size() & 3) g_pool.push_back(0);
    g_pooled[hash].push_back({ at, size });
    return at;
}
static void Put(uint32_t v) { g_out->push_back(v); }
static void PutF(float f) { uint32_t v; std::memcpy(&v, &f, 4); Put(v); }

// prim: Xenos' own numbers -- 4 list, 5 fan, 6 strip, 13 quad list.
static void Draw(uint32_t prim, uint32_t baseVertex, uint32_t start, uint32_t count, uint32_t upData, uint32_t upStride)
{
    if (!g_recording) return;
    const bool up = upData != 0;
    const bool indexed = !up && start != 0xFFFFFFFFu && g_ib >= 0 && baseVertex != 0xFFFFFFFFu;
    uint32_t stride = up ? upStride : g_stride, vOff = 0, vSize = 0, iOff = 0, iSize = 0;
    if (up) { vSize = count * stride; vOff = Pool(upData, vSize); }
    else if (g_vb >= 0) { vSize = g_buffers[g_vb].size; vOff = Pool(g_buffers[g_vb].data, vSize); }
    if (indexed) { iSize = g_buffers[g_ib].size; iOff = Pool(g_buffers[g_ib].data, iSize); }
    if (g_text) {
        std::printf("  draw prim=%u %s count=%u start=%u  %s + %s  decl=%d stride=%u  vb=%d(%u B) ib=%d  target=%d  tex=[%d %d %d %d %d %d %d]\n"
                    "       z=%u/%u/%u cull=%u blend=%u(%u,%u) atest=%u(%u,%.3f) cw=%u\n",
                    prim, up ? "UP" : indexed ? "indexed" : "plain", count, start,
                    g_vs >= 0 ? kShaders[g_vs].name : "?", g_ps >= 0 ? kShaders[g_ps].name : "?", g_decl, stride,
                    g_vb, vSize, g_ib, g_target, g_tex[0], g_tex[1], g_tex[2], g_tex[3], g_tex[4], g_tex[5], g_tex[6],
                    g_state[kZEnable], g_state[kZWrite], g_state[kZFunc], g_state[kCull], g_state[kBlendEnable],
                    g_state[kSrcBlend], g_state[kDestBlend], g_state[kAlphaTest], g_state[kAlphaFunc],
                    (double)g_state[kAlphaRef], g_state[kColorWrite]);
        std::printf("       vs c0-7:");
        for (int i = 0; i < 32; ++i) std::printf("%s%.4g", i % 4 ? " " : "  ", (double)g_vsC[i]);
        std::printf("\n       ps c0-3:");
        for (int i = 0; i < 16; ++i) std::printf("%s%.4g", i % 4 ? " " : "  ", (double)g_psC[i]);
        std::printf("\n");
    }
    Put(kCmdDraw); Put(prim); Put(indexed ? 1u : 0u); Put(indexed ? start : baseVertex); Put(count);
    Put((uint32_t)g_vs); Put((uint32_t)g_ps); Put((uint32_t)g_decl); Put(stride);
    Put(vOff); Put(vSize); Put(iOff); Put(iSize);
    for (int i = 0; i < kStateCount; ++i) Put(g_state[i]);
    for (int i = 0; i < 8; ++i) Put((uint32_t)g_tex[i]);
    for (int i = 0; i < kVsRegs * 4; ++i) PutF(g_vsC[i]);
    for (int i = 0; i < kPsRegs * 4; ++i) PutF(g_psC[i]);
}
void sub_9802C190(PPCContext& ctx, uint8_t*) { Draw(ctx.r4.u32, ctx.r5.u32, ctx.r6.u32, ctx.r7.u32, 0, 0); }
void sub_9802BD78(PPCContext& ctx, uint8_t*) { Draw(ctx.r4.u32, ctx.r5.u32, 0xFFFFFFFFu, ctx.r6.u32, 0, 0); }
void sub_9802BD30(PPCContext& ctx, uint8_t*) { Draw(ctx.r4.u32, 0, 0, ctx.r5.u32, ctx.r6.u32, ctx.r7.u32); }

// ---- targets -------------------------------------------------------------------------
void sub_98024290(PPCContext& ctx, uint8_t*)                          // GetRenderTarget: the back buffer
{
    uint32_t h = GuestAlloc(64);
    g_surfaceOf[h] = (int)g_surfaces.size(); g_surfaces.push_back({ 1280, 720 });
    ctx.r3.u64 = h;
}
void sub_980242D8(PPCContext& ctx, uint8_t*) { ctx.r3.u64 = GuestAlloc(64); }       // GetDepthStencilSurface
void sub_98024FB0(PPCContext& ctx, uint8_t*)                          // SetRenderTarget(index, surface)
{
    g_target = Lookup(g_surfaceOf, ctx.r5.u32);
    if (!g_recording) return;
    if (g_text) std::printf("  target surface %d (%ux%u)\n", g_target, g_target >= 0 ? g_surfaces[g_target].first : 0,
                            g_target >= 0 ? g_surfaces[g_target].second : 0);
    Put(kCmdTarget); Put((uint32_t)g_target);
}
void sub_98024CD8(PPCContext&, uint8_t*) {}                           // SetDepthStencilSurface
static void Clear(uint32_t flags, uint32_t color)
{
    if (!g_recording) return;
    if (g_text) std::printf("  clear flags=%u color=%08X\n", flags, color);
    Put(kCmdClear); Put(flags); Put(color);
}
void sub_9802ACE8(PPCContext& ctx, uint8_t*) { Clear(ctx.r6.u32, ctx.r7.u32); }               // Clear
void sub_9802B038(PPCContext& ctx, uint8_t*) { Clear(ctx.r5.u32, ctx.r6.u32); }               // ClearRects (flags, colour)
static void Resolve(PPCContext& ctx, const char* which)
{
    int tex = Lookup(g_textureOf, ctx.r6.u32);
    if (tex >= 0) g_textures[tex].target = true;
    if (!g_recording) return;
    if (g_text) std::printf("  resolve (%s) -> texture %d   r4=%08X r5=%08X r7=%08X r8=%08X\n", which, tex, ctx.r4.u32,
                            ctx.r5.u32, ctx.r7.u32, ctx.r8.u32);
    Put(kCmdResolve); Put((uint32_t)tex);
}
void sub_9802B3A8(PPCContext& ctx, uint8_t*) { Resolve(ctx, "rect"); }
void sub_9802E190(PPCContext& ctx, uint8_t*) { Resolve(ctx, "whole"); }
void sub_9802E1E0(PPCContext&, uint8_t*) {}
void sub_98029958(PPCContext&, uint8_t*) {}                           // Present
void sub_980291E8(PPCContext&, uint8_t*) {}
void sub_980291F0(PPCContext&, uint8_t*) {}
void sub_9802F3B8(PPCContext&, uint8_t*) {}                           // Release
void sub_980268A8(PPCContext& ctx, uint8_t*) { ctx.r3.u64 = 0; }
void sub_980268B8(PPCContext& ctx, uint8_t*) { ctx.r3.u64 = 0; }

// ---- the rest of the host, as trace_stubs.inc has it --------------------------------
void sub_9800F040(PPCContext& ctx, uint8_t*) { SU32(0x98077954, FAKE_DEVICE); ctx.r3.u64 = 0; }   // CreateDevice, skipped
void sub_98045A00(PPCContext& ctx, uint8_t*) { ctx.r3.u64 = (uint32_t)(g_time * 1000.0f); }       // the clock
void sub_98045A60(PPCContext& ctx, uint8_t*) { ctx.r3.u64 = 0; }
void sub_98045A58(PPCContext& ctx, uint8_t*) { ctx.r3.u64 = 0; }
void sub_98045A10(PPCContext& ctx, uint8_t*) { ctx.r3.u64 = 0; }
void __imp__XeCryptRandom(PPCContext& ctx, uint8_t*)
{
    static uint32_t s = 0x12345678u;      // fixed, so the three generated textures come out the same every run
    for (uint32_t i = 0; i < ctx.r4.u32; ++i) { s = s * 1664525u + 1013904223u; g_base[ctx.r3.u32 + i] = (uint8_t)(s >> 24); }
    ctx.r3.u64 = 0;
}
void __imp__KeSetEvent(PPCContext& ctx, uint8_t*) { ctx.r3.u64 = 0; }
void __imp__RtlEnterCriticalSection(PPCContext&, uint8_t*) {}
void __imp__RtlLeaveCriticalSection(PPCContext&, uint8_t*) {}
void __imp__RtlInitializeCriticalSection(PPCContext&, uint8_t*) {}

// ---- main --------------------------------------------------------------------------------
static void WriteFile(const char* path, float fps, float duration);

int main(int argc, char** argv)
{
    const char* xex = argc > 1 ? argv[1] : "bootanim_noaudio.xex";
    g_text = argc > 2 && !std::strcmp(argv[2], "--text");
    const float textAt = g_text && argc > 3 ? (float)atof(argv[3]) : -1.0f;
    const char* out = g_text ? nullptr : (argc > 2 ? argv[2] : "trace.bin");
    const float fps = argc > 4 ? (float)atof(argv[4]) : (!g_text && argc > 3 ? (float)atof(argv[3]) : 60.0f);
    const float dur = !g_text && argc > 4 ? (float)atof(argv[4]) : 6.5f;
    const int   nf  = (int)(dur * fps) + 1;
    if (!RuntimeInit(xex)) return 1;

    for (uint32_t i = 0; i < 0x40000; i += 4) SU32(FAKE_DEVICE + i, 0);
    SU32(0x98077768, 1280); SU32(0x9807776C, 720);
    uint32_t app = GuestAlloc(4096);
    SU32(0x98059080, app);

    PPCContext ctx; SetupContext(ctx, 0x70100000);
    ctx.r3.u64 = app; ctx.r4.u64 = 1280; ctx.r5.u64 = 720;
    ctx.r6.u64 = 0x9800222C; ctx.r7.u64 = 0; ctx.r8.u64 = 0;
    sub_9800F5A8(ctx, g_base);
    std::printf("init: %zu textures, %zu surfaces, %zu buffers, %zu declarations\n", g_textures.size(), g_surfaces.size(),
                g_buffers.size(), g_decls.size());

    g_frames.resize(nf);
    for (int f = 0; f < nf; ++f) {
        g_time = (float)f / fps;
        g_out = &g_frames[f];
        const bool show = g_text && f == (int)(textAt * fps + 0.5f);
        g_recording = true;
        const bool text = g_text; g_text = show;
        if (show) std::printf("==== frame %d, t = %.4f ====\n", f, g_time);
        // Tick takes a delta and keeps the elapsed time itself: frames go in order, a fixed step apart.
        SetupContext(ctx, 0x70100000); ctx.r3.u64 = app; ctx.f1.f64 = 1.0 / (double)fps;
        sub_9800EA10(ctx, g_base);
        SetupContext(ctx, 0x70100000); ctx.r3.u64 = app;
        sub_9800FB30(ctx, g_base);
        g_recording = false; g_text = text;
        if (show) break;
    }
    size_t words = 0, draws = 0;
    for (auto& fr : g_frames) { words += fr.size(); for (size_t i = 0; i < fr.size(); ) { if (fr[i] == kCmdDraw) { ++draws; i += 13 + kStateCount + 8 + (kVsRegs + kPsRegs) * 4; } else if (fr[i] == kCmdClear) i += 3; else i += 2; } }
    std::printf("%d frames, %zu draws, %zu KB of commands, %zu KB of vertex and index data\n", nf, draws, words * 4 / 1024,
                g_pool.size() / 1024);
    if (out) WriteFile(out, fps, dur);
    return 0;
}

// "BATRACE1", then u32s: frames, fps*1000, vsRegs, psRegs, stateCount, textures, surfaces, declarations, pool bytes.
// Then per texture: w, h, target, and w*h*4 bytes of BGRA as the title wrote them (none for a target).
// Then per surface: w, h. Per declaration: word count, words. The pool. Per frame: word count, words.
static void WriteFile(const char* path, float fps, float duration)
{
    (void)duration;
    FILE* fp = std::fopen(path, "wb");
    if (!fp) { std::perror(path); return; }
    auto w32 = [&](uint32_t v) { std::fwrite(&v, 4, 1, fp); };
    std::fwrite("BATRACE1", 8, 1, fp);
    w32((uint32_t)g_frames.size()); w32((uint32_t)(fps * 1000.0f)); w32(kVsRegs); w32(kPsRegs); w32(kStateCount);
    w32((uint32_t)g_textures.size()); w32((uint32_t)g_surfaces.size()); w32((uint32_t)g_decls.size());
    w32((uint32_t)g_pool.size());
    for (auto& t : g_textures) {
        w32(t.w); w32(t.h); w32(t.target ? 1u : 0u);
        if (!t.target) std::fwrite(g_base + t.bits, 1, t.w * t.h * 4, fp);
    }
    for (auto& s : g_surfaces) { w32(s.first); w32(s.second); }
    for (auto& d : g_decls) { w32((uint32_t)d.words.size()); for (uint32_t v : d.words) w32(v); }
    std::fwrite(g_pool.data(), 1, g_pool.size(), fp);
    for (auto& fr : g_frames) { w32((uint32_t)fr.size()); if (!fr.empty()) std::fwrite(fr.data(), 4, fr.size(), fp); }
    std::printf("wrote %s (%ld bytes)\n", path, std::ftell(fp));
    std::fclose(fp);
}
