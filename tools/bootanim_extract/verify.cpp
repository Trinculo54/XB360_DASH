// End-to-end check: load the packed asset files through the exact loader that
// ships in the app, and compare against the title's own code running under
// the recompiled harness. If this passes, the data files and the C++ that
// reads them reproduce the console, not just my notes about it.
#include "ppc_config.h"
#include "ppc_context.h"
#include "BootAnimAssets.h"
#include <cstdio>
#include <cmath>
#include <cstring>
#include <algorithm>

extern uint8_t* g_base;
bool RuntimeInit(const char*);
void SetupContext(PPCContext&, uint32_t);
uint32_t GuestAlloc(uint32_t);
PPC_EXTERN_FUNC(sub_98016B48);   // Curves_Init
PPC_EXTERN_FUNC(sub_98014F88);   // Spline_Eval
PPC_EXTERN_FUNC(sub_98015D58);   // Curve_Evaluate

static uint32_t GU32(uint32_t a){ return __builtin_bswap32(*(uint32_t*)(g_base+a)); }
static float    GF32(uint32_t a){ uint32_t v=GU32(a); float f; std::memcpy(&f,&v,4); return f; }
static uint8_t  GU8 (uint32_t a){ return *(uint8_t*)(g_base+a); }

int main(int argc, char** argv)
{
    const char* nodesFile = argc > 1 ? argv[1] : "bootanim_nodes.bin";
    if (!RuntimeInit("bootanim_noaudio.xex")) return 1;
    PPCContext ctx; SetupContext(ctx, 0x70100000);
    sub_98016B48(ctx, g_base);
    uint32_t splines = GU32(0x98077984), tracks = GU32(0x98077974), nodes = GU32(0x98077970);
    int splineCount = (int)((tracks - splines) / 64);
    uint32_t nodeCount = GU32(nodes - 4);

    BootCurves curves;
    BootNodes  baked;
    BootMeshes meshes;
    if (!curves.Load("curves.bin"))        { std::printf("curves.bin failed to load\n"); return 2; }
    if (!baked.Load(nodesFile)) { std::printf("nodes.bin failed to load\n");  return 2; }
    if (!meshes.Load("meshes.bin"))        { std::printf("meshes.bin failed to load\n"); return 2; }

    std::printf("loaded: %d splines, %d tracks, %u baked nodes, %zu meshes\n\n",
                curves.SplineCount(), curves.TrackCount(), baked.NodeCount(), meshes.Count());
    if (curves.SplineCount() != splineCount) { std::printf("spline count mismatch!\n"); return 3; }

    // ---- 1. every curve, densely sampled, against the guest evaluator -----
    double worst = 0; int ws = -1; float wt = 0; long n = 0;
    for (int s = 0; s < splineCount; ++s) {
        int keys = (int8_t)GU8(splines + s*64 + 57);
        if (keys < 2) continue;
        float t0 = GF32(GU32(splines + s*64 + 60));
        float t1 = GF32(GU32(splines + s*64 + 60) + (keys-1)*24);
        for (int i = 0; i <= 300; ++i) {
            float t = t0 + (t1 - t0) * (float)i / 300.0f;
            PPCContext c; SetupContext(c, 0x70100000);
            c.r3.u64 = splines + s*64; c.f1.f64 = t;
            sub_98014F88(c, g_base);
            double guest = c.f1.f64, mine = curves.Eval(s, t);
            double e = std::fabs(guest - mine) / std::max(1.0, std::fabs(guest));
            if (e > worst) { worst = e; ws = s; wt = t; }
            ++n;
        }
    }
    std::printf("curves    %7ld samples  worst rel err %.3e  (spline %d @ %.3f)  %s\n",
                n, worst, ws, wt, worst < 1e-4 ? "PASS" : "FAIL");

    // ---- 2. baked node matrices against a fresh guest evaluation ----------
    uint32_t mat = GuestAlloc(256);
    // Two separate questions. On a stored sample the bake must be exact --
    // any error there is a bug in the baker or the loader. Between samples
    // the componentwise lerp is an approximation, and the number below is
    // what the 60 Hz bake rate actually costs; it is reported, not asserted.
    double onSample = 0, offSample = 0; long mn = 0;
    const float bakedFps = 60.0f * (baked.Duration() > 0 ? 1.0f : 1.0f);
    (void)bakedFps;
    const int totalFrames = (int)(6.4f * (baked.NodeCount() ? 1 : 1) * 60);
    (void)totalFrames;
    for (int f = 0; f <= 384; ++f) {
        for (uint32_t k = 0; k < nodeCount; ++k) {
            for (int half = 0; half < 2; ++half) {
                float t = ((float)f + (half ? 0.5f : 0.0f)) / 60.0f;   // probe on the 60 Hz grid regardless of bake rate
                PPCContext c; SetupContext(c, 0x70100000);
                c.r3.u64 = nodes + k*56; c.f1.f64 = t; c.r5.u64 = mat;
                sub_98015D58(c, g_base);
                float mine[16]; baked.GetMatrix(k, t, mine);
                for (int i = 0; i < 16; ++i) {
                    double g = GF32(mat + i*4);
                    double e = std::fabs(g - mine[i]) / std::max(1.0, std::fabs(g));
                    if (half) offSample = std::max(offSample, e);
                    else      onSample  = std::max(onSample,  e);
                }
                ++mn;
            }
        }
    }
    std::printf("nodes     %7ld matrices on-sample err %.3e                     %s\n",
                mn, onSample, onSample < 1e-5 ? "PASS" : "FAIL");
    std::printf("          worst midpoint-lerp error between 60 Hz samples: %.3e\n", offSample);
    double mworst = onSample;

    // ---- 3. meshes: index range and vertex counts -------------------------
    bool meshOk = true;
    for (size_t i = 0; i < meshes.Count(); ++i) {
        const BootMeshes::Mesh& m = meshes.At(i);
        uint16_t hi = 0;
        for (uint16_t v : m.indices) hi = std::max(hi, v);
        bool ok = (hi + 1u == m.vertices.size());
        meshOk = meshOk && ok;
        std::printf("mesh %-10s %5zu verts %6zu idx  maxIdx %5u  uv %d  %s\n",
                    m.name.c_str(), m.vertices.size(), m.indices.size(), hi, m.hasUV ? 1 : 0,
                    ok ? "PASS" : "FAIL");
    }

    bool pass = worst < 1e-4 && mworst < 1e-5 && meshOk;
    std::printf("\n%s\n", pass ? "ALL CHECKS PASSED" : "SOMETHING FAILED");
    return pass ? 0 : 4;
}
