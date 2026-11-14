// ---------------------------------------------------------------------------
//  Bakes the boot animation's per-frame draw matrices.
//
//  Running the real Init and the real RenderFrame at each time step and
//  capturing what the title hands to SetVertexShaderConstantF sidesteps the
//  node hierarchy entirely: whatever the 29 nodes and 666 curves compose to,
//  the answer arrives here already composed, in the exact form the vertex
//  shader consumes.
//
//  Per frame the scene pass issues four draws, and the first three share a
//  transform -- those are the sphere, the X inlay and the X trim. The fourth
//  carries the same transform scaled down by ~160x, which is the backdrop
//  sphere drawn at 160x radius around the camera.
// ---------------------------------------------------------------------------
#include "ppc_config.h"
#include "ppc_context.h"
#include <cstdio>
#include <cstring>
#include <vector>
#include <string>
#include <map>

extern uint8_t* g_base;
bool RuntimeInit(const char*);
void SetupContext(PPCContext&, uint32_t);
uint32_t GuestAlloc(uint32_t);

PPC_EXTERN_FUNC(sub_9800F5A8);
PPC_EXTERN_FUNC(sub_9800FB30);
PPC_EXTERN_FUNC(sub_9800EA10);

static uint32_t GU32(uint32_t a){ return __builtin_bswap32(*(uint32_t*)(g_base+a)); }
static void     SU32(uint32_t a,uint32_t v){ *(uint32_t*)(g_base+a)=__builtin_bswap32(v); }
static float    GF32(uint32_t a){ uint32_t v=GU32(a); float f; std::memcpy(&f,&v,4); return f; }

static const uint32_t FAKE_DEVICE = 0x60000000;
static float g_time = 0.0f;

// Capture state for the frame currently being rendered.
struct Draw { int declIndex = -1; float wvp[16] = {}; float world[16] = {}; bool haveW = false, haveWorld = false; };
static std::vector<Draw> g_draws;
static bool g_capture = false;
static std::map<uint32_t,int> g_declIndex;
static int g_nextDecl = 0;

#include "trace_stubs.inc"   // the resource/state stubs, shared with trace.cpp

void sub_98027B68(PPCContext& ctx, uint8_t* base)     // SetVertexDeclaration
{
    (void)base;
    if (!g_capture) return;
    auto it = g_declIndex.find(ctx.r4.u32);
    int idx = (it == g_declIndex.end()) ? (g_declIndex[ctx.r4.u32] = g_nextDecl++) : it->second;
    g_draws.push_back(Draw{}); g_draws.back().declIndex = idx;
}
void sub_98027560(PPCContext& ctx, uint8_t* base)     // SetVertexShaderConstantF
{
    (void)base;
    if (!g_capture || g_draws.empty()) return;
    uint32_t start = ctx.r4.u32, data = ctx.r5.u32, count = ctx.r6.u32;
    Draw& d = g_draws.back();
    if (start == 0 && count >= 4) { for (int i=0;i<16;++i) d.wvp[i]   = GF32(data+i*4); d.haveW = true; }
    if (start == 4 && count >= 4) { for (int i=0;i<16;++i) d.world[i] = GF32(data+i*4); d.haveWorld = true; }
}
void sub_98045A00(PPCContext& ctx, uint8_t* base){ (void)base; ctx.r3.u64 = (uint32_t)(g_time*1000.0f); }

int main(int argc, char** argv)
{
    const char* xex = argc > 1 ? argv[1] : "bootanim_noaudio.xex";
    const char* out = argc > 2 ? argv[2] : "frames.bin";
    const float fps = argc > 3 ? (float)atof(argv[3]) : 60.0f;
    const float dur = 6.5f;
    const int   nf  = (int)(dur*fps) + 1;
    if (!RuntimeInit(xex)) return 1;

    for (uint32_t i = 0; i < 0x40000; i += 4) SU32(FAKE_DEVICE + i, 0);
    SU32(0x98077768, 1280); SU32(0x9807776C, 720);
    uint32_t app = GuestAlloc(4096);
    SU32(0x98059080, app);

    PPCContext ctx; SetupContext(ctx, 0x70100000);
    ctx.r3.u64 = app; ctx.r4.u64 = 1280; ctx.r5.u64 = 720;
    ctx.r6.u64 = 0x9800222C; ctx.r7.u64 = 0; ctx.r8.u64 = 0;
    sub_9800F5A8(ctx, g_base);

    // frame record: 4 matrices - orb wvp, orb world, backdrop wvp, backdrop world
    std::vector<float> outData;
    int missing = 0;
    for (int f = 0; f < nf; ++f) {
        g_time = (float)f / fps;
        g_draws.clear(); g_capture = true;
        // Tick takes a delta and accumulates elapsed time itself, so the
        // frames have to be stepped in order at a fixed step -- handing it an
        // absolute time makes elapsed run away and the animation sits on its
        // end state.
        SetupContext(ctx, 0x70100000); ctx.r3.u64 = app; ctx.f1.f64 = 1.0 / (double)fps;
        sub_9800EA10(ctx, g_base);
        SetupContext(ctx, 0x70100000); ctx.r3.u64 = app;
        sub_9800FB30(ctx, g_base);
        g_capture = false;

        // The three orb meshes are submitted back to back with one shared
        // transform, so a run of three consecutive draws carrying identical
        // matrices identifies them unambiguously -- which matters because the
        // number and order of the other draws changes across the timeline.
        const Draw *orb = nullptr, *back = nullptr;
        auto same = [](const Draw& a, const Draw& b) {
            for (int i = 0; i < 16; ++i)
                if (a.wvp[i] != b.wvp[i] || a.world[i] != b.world[i]) return false;
            return true;
        };
        for (size_t i = 0; i + 2 < g_draws.size(); ++i) {
            const Draw &a = g_draws[i], &b = g_draws[i+1], &c = g_draws[i+2];
            if (!a.haveW || !a.haveWorld || !b.haveW || !c.haveW) continue;
            // The blur chain also submits runs of identically-transformed
            // quads, but it reuses one declaration for all of them. The orb's
            // three meshes each have their own, so requiring three distinct
            // declarations separates the two cases.
            if (a.declIndex == b.declIndex || b.declIndex == c.declIndex || a.declIndex == c.declIndex) continue;
            if (same(a,b) && same(b,c)) {
                orb = &a;
                // the backdrop follows, same orientation scaled ~160x down
                for (size_t j = i+3; j < g_draws.size(); ++j)
                    if (g_draws[j].haveW && g_draws[j].haveWorld &&
                        std::abs(g_draws[j].wvp[0]) < std::abs(a.wvp[0]) * 0.05f) { back = &g_draws[j]; break; }
                break;
            }
        }
        if (!orb) ++missing;
        static float ident[16] = {1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1};
        const float* a = orb  ? orb->wvp   : ident;
        const float* b = orb  ? orb->world : ident;
        const float* c = back ? back->wvp  : ident;
        const float* e = back ? back->world: ident;
        outData.insert(outData.end(), a, a+16);
        outData.insert(outData.end(), b, b+16);
        outData.insert(outData.end(), c, c+16);
        outData.insert(outData.end(), e, e+16);
        if (f == 0 || f == nf/2)
            std::printf("frame %3d t=%.3f  draws captured %zu  orb %s  backdrop %s\n",
                        f, g_time, g_draws.size(), orb?"yes":"NO", back?"yes":"no");
    }
    std::printf("frames %d, frames with no orb transform: %d\n", nf, missing);

    FILE* fp = std::fopen(out, "wb");
    struct { char magic[8]; uint32_t frames, matsPerFrame; float fps, duration; } hdr =
        { {'B','A','F','R','A','M','E','1'}, (uint32_t)nf, 4, fps, dur };
    std::fwrite(&hdr, sizeof hdr, 1, fp);
    std::fwrite(outData.data(), 4, outData.size(), fp);
    std::fclose(fp);
    std::printf("wrote %s (%zu bytes)\n", out, sizeof(hdr)+outData.size()*4);
    return 0;
}
